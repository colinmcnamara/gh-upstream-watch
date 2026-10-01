"""Rule packs: local JSON data that turns generic changes into next-action alerts.

Packs load only from the bundled packs/ directory and local directories you name. Nothing is
ever read from a watched repo: the repo you watch must not control what you are told.
"""
import fnmatch
import json
import re
from pathlib import Path

BUNDLED = Path(__file__).parent / "packs"
KEYS = {"id", "description", "repos", "gates", "label_transitions", "quiet_titles", "messages", "claimable"}
MESSAGE_KEYS = {"assigned", "reopened", "competing_pr", "reference"}
CLAIMABLE_KEYS = {"search", "title", "comment_marker", "section", "row", "alert"}


class PackError(Exception):
    pass


ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR", "CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "NONE"}
# MEMBER is not a default: an organization member may have read-only access to the repo.
DEFAULT_ASSOCIATIONS = ["OWNER", "COLLABORATOR"]


def _regex(pattern, where, flags=0):
    try:
        return re.compile(pattern, flags)
    except (re.error, TypeError) as e:
        raise PackError(f"{where}: bad regex {pattern!r}: {e}")


def _strs(v):
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def validate(pack, source):
    """Check a pack's shape and types, and attach compiled regexes under '_c'. Unknown keys are errors."""
    def need(cond, msg):
        if not cond:
            raise PackError(f"{source}: {msg}")
    need(isinstance(pack, dict), "a pack is a JSON object")
    need(not set(pack) - KEYS, f"unknown keys {sorted(set(pack) - KEYS)}")
    need(isinstance(pack.get("id"), str) and pack["id"], "'id' must be a non-empty string")
    need(_strs(pack.get("repos")) and pack["repos"], "'repos' must be a non-empty list of strings (globs)")
    for key in ("gates", "label_transitions", "quiet_titles"):
        need(isinstance(pack.get(key, []), list), f"'{key}' must be a list")
    def check_by(by, where):
        need(isinstance(by, dict) and not set(by) - {"associations", "logins"}, f"{where}: authorized_by takes associations, logins")
        need(_strs(by.get("logins", [])) and _strs(by.get("associations", [])), f"{where}: logins and associations must be string lists")
        need(not set(by.get("associations", [])) - ASSOCIATIONS, f"{where}: unknown association in {by.get('associations')}")

    c = {"gates": [], "quiet_titles": [], "row": None}
    for g in pack.get("gates", []):
        need(isinstance(g, dict) and {"id", "comment", "authorized_by", "alert"} <= set(g),
             f"gate needs id, comment, authorized_by, alert: {g}")
        need(all(isinstance(g.get(k, ""), str) for k in ("id", "comment", "alert", "then", "alert_done")),
             f"gate {g['id']}: id, comment, alert, then, alert_done must be strings")
        need(g["comment"].startswith("^"), f"gate {g['id']}: comment regex must be anchored with ^")
        check_by(g["authorized_by"], f"gate {g['id']}")
        # No re.M: a gate matches only at the start of the comment body, never a quoted line further down.
        c["gates"].append((_regex(g["comment"], source), _regex(g["then"], source) if g.get("then") else None))
    for t in pack.get("label_transitions", []):
        need(isinstance(t, dict) and {"id", "alert"} <= set(t), f"label_transition needs id and alert: {t}")
        need(_strs(t.get("has", [])) and _strs(t.get("lacks", [])), f"label_transition {t['id']}: has and lacks must be string lists")
        need(isinstance(t.get("assigned_to_me", False), bool), f"label_transition {t['id']}: assigned_to_me must be true or false")
    need(_strs(pack.get("quiet_titles", [])), "'quiet_titles' must be a list of regex strings")
    c["quiet_titles"] = [_regex(p, source) for p in pack.get("quiet_titles", [])]
    msgs = pack.get("messages", {})
    need(isinstance(msgs, dict) and all(isinstance(v, str) for v in msgs.values()), "'messages' must map names to strings")
    need(not set(msgs) - MESSAGE_KEYS, f"unknown messages {sorted(set(msgs) - MESSAGE_KEYS)}")
    cl = pack.get("claimable")
    if cl:
        need(isinstance(cl, dict) and set(cl) - {"authorized_by"} == CLAIMABLE_KEYS,
             f"claimable needs exactly {sorted(CLAIMABLE_KEYS)}, plus an optional authorized_by")
        need(all(isinstance(v, str) for k, v in cl.items() if k != "authorized_by"), "claimable values must be strings")
        check_by(cl.get("authorized_by", {}), "claimable")
        c["row"] = _regex(cl["row"], source, re.M)
        need({"number", "title", "url"} <= set(c["row"].groupindex), "claimable row needs groups number, title, url")
        c["claim_title"] = _regex(cl["title"], source)
    pack["_c"] = c
    return pack


def load(extra_dirs=()):
    """Bundled packs, then each extra dir in order; a later pack with the same id replaces an earlier one."""
    packs = {}
    for d in [BUNDLED, *[Path(p).expanduser() for p in extra_dirs]]:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            try:
                data = json.loads(f.read_text())
            except ValueError as e:
                raise PackError(f"{f}: invalid JSON: {e}")
            packs[data.get("id")] = validate(data, f)
    return list(packs.values())


def check(path):
    """Validate one pack file and describe what it does, for `check-pack`. Raises PackError."""
    path = Path(path).expanduser()
    try:
        pack = validate(json.loads(path.read_text()), path)
    except (OSError, ValueError) as e:
        raise PackError(f"{path}: {e}")

    def trust(by):
        by = by or {}
        return f"logins {by['logins']}" if by.get("logins") else f"associations {by.get('associations', DEFAULT_ASSOCIATIONS)}"
    out = [f"{path}: ok", f"  id {pack['id']}" + (" (replaces the bundled pack)" if (BUNDLED / f"{pack['id']}.json").exists()
                                                 and BUNDLED not in path.resolve().parents else ""),
           f"  repos {pack.get('repos', [])}"]
    for g in pack.get("gates", []):
        out.append(f"  gate {g['id']}: comment `{g['comment']}`, trusts {trust(g['authorized_by'])}"
                   + (f", then `{g['then']}`" if g.get("then") else ""))
    for t in pack.get("label_transitions", []):
        out.append(f"  label rule {t['id']}: has {t.get('has', [])}, lacks {t.get('lacks', [])}")
    for q in pack.get("quiet_titles", []):
        out.append(f"  quiet title `{q}`: alerts only on comments naming you")
    if pack.get("claimable"):
        out.append(f"  claim board `{pack['claimable']['title']}`: trusts {trust(pack['claimable'].get('authorized_by'))}")
    return out


def for_repo(packs, repo):
    """The effective rules for one repo: every matching pack merged, catch-all ('*') packs first."""
    hits = [p for p in packs if any(fnmatch.fnmatch(repo.lower(), g.lower()) for g in p["repos"])]
    hits.sort(key=lambda p: "*" not in p["repos"])
    eff = {"ids": [], "gates": [], "label_transitions": [], "quiet_titles": [], "messages": {}, "claimable": None}
    for p in hits:
        eff["ids"].append(p["id"])
        eff["gates"] += [dict(g, _re=r, _then=t) for g, (r, t) in zip(p.get("gates", []), p["_c"]["gates"])]
        eff["label_transitions"] += p.get("label_transitions", [])
        eff["quiet_titles"] += p["_c"]["quiet_titles"]
        eff["messages"].update(p.get("messages", {}))
        if p.get("claimable"):
            eff["claimable"] = dict(p["claimable"], _row=p["_c"]["row"], _title=p["_c"]["claim_title"])
    return eff


def authorized(gate, login, association):
    """A gate that lists logins trusts only those logins. Otherwise only its associations count,
    OWNER and COLLABORATOR by default: author_association alone cannot prove who may run a gate."""
    by = gate.get("authorized_by") or {}
    if by.get("logins"):
        return login.lower() in {x.lower() for x in by["logins"]}
    assoc = by.get("associations")
    return association in (DEFAULT_ASSOCIATIONS if assoc is None else assoc)


def parse_claimable(body, cl, group):
    """(number, title, url) rows in one group's section of a claim-board comment."""
    if cl["comment_marker"].format(group=group) not in body or cl["section"] not in body:
        return []
    heading = cl["section"].split(" ", 1)[0] + " "
    section = body.split(cl["section"], 1)[1].split("\n" + heading, 1)[0]
    return [(m.group("number"), m.group("title").strip(), m.group("url")) for m in cl["_row"].finditer(section)]
