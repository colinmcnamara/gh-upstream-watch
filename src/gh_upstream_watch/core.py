"""Fingerprints of watched items, and the diff that turns two fingerprints into alerts."""
import fnmatch
import re
import time

from . import github
from .packs import authorized, parse_claimable

BOTS = ("coderabbitai", "mergify", "github-actions", "dependabot", "codecov")
ASKS = {"mention": "mentioned you", "team_mention": "mentioned your team",
        "review_requested": "requested your review", "assign": "assigned you"}
# Closes/Fixes/Resolves (any tense), optional colon, then #n, owner/repo#n, or an issue/PR URL.
CLOSE_RE = re.compile(
    r"(?i)(?<![\w-])(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+"
    r"(?:https?://github\.com/([\w.-]+/[\w.-]+)/(?:issues|pull)/|([\w.-]+/[\w.-]+)#|#)(\d+)(?!\d)")


GITHUB_URL = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+(/[\w./#?=&%-]*)?$")


def safe_url(url, fallback):
    """Only https://github.com/ links from issue text reach a notifier; anything else becomes fallback."""
    return url if isinstance(url, str) and GITHUB_URL.match(url) else fallback


def repo_matches(repo, patterns):
    """Exact (case-insensitive) unless the pattern is a glob."""
    return any(fnmatch.fnmatchcase(repo.lower(), p.lower()) if any(ch in p for ch in "*?[") else repo.lower() == p.lower()
               for p in patterns)


def is_bot(login, extra=()):
    login = login.lower()
    return login.endswith("[bot]") or login in BOTS or login in extra


def mentions(body, login):
    """Exact @login: not @login-bot, not someone@login.com."""
    return re.search(r"(?i)(?<![\w-])@" + re.escape(login) + r"(?![\w-])", body or "") is not None


def closes(body, src_repo, repo, n):
    """True when body closes repo#n. A bare #n means the source's own repo."""
    for m in CLOSE_RE.finditer(body or ""):
        if (m.group(1) or m.group(2) or src_repo).lower() == repo.lower() and int(m.group(3)) == n:
            return True
    return False


def repo_of(html):
    parts = html.split("/")
    return f"{parts[3]}/{parts[4]}"


def discover(repo, me, recent_days, now):
    """Keys of open items you are involved in, plus ones closed recently (so a reopen alerts)."""
    since = time.strftime("%Y-%m-%d", time.gmtime(now - recent_days * 86400))
    found = set()
    for q in (f"repo:{repo} involves:{me} is:open", f"repo:{repo} involves:{me} is:closed updated:>={since}"):
        found |= {f"{repo}#{i['number']}" for i in github.search_issues(q)}
    return found


def cross_refs(repo, n, me):
    """[url, 'pr'|'issue', author, closes] for each item by someone else that references repo#n."""
    refs = {}
    for ev in github.paginate(f"repos/{repo}/issues/{n}/timeline"):
        src = (ev.get("source") or {}).get("issue") or {}
        if ev.get("event") != "cross-referenced" or not src or (src.get("user") or {}).get("login") == me:
            continue
        url = src["html_url"]
        prev = refs.get(url)
        cl = closes(src.get("body"), repo_of(url), repo, n) or bool(prev and prev[3])
        refs[url] = [url, "pr" if "pull_request" in src else "issue", src["user"]["login"], cl]
    return sorted(refs.values())


def fingerprint(repo, n, me, rules, bots=()):
    """Everything about repo#n that can change. Raises GHError if any page fails."""
    i = github.gh_get(f"repos/{repo}/issues/{n}")
    comments = github.paginate(f"repos/{repo}/issues/{n}/comments") if i.get("comments") else []
    others = [c for c in comments if c["user"]["login"] != me]
    fp = {
        "title": i["title"], "url": i["html_url"], "pr": "pull_request" in i, "state": i["state"],
        "labels": sorted(label["name"] for label in i["labels"]),
        "assignees": sorted(a["login"] for a in i.get("assignees") or []),
        "comments": len(comments),
        "human_comments": sum(1 for c in others if not is_bot(c["user"]["login"], bots)),
        "mentions_me": sum(1 for c in others if mentions(c.get("body"), me)),
        "gates": {},
        "xrefs": cross_refs(repo, n, me),
    }
    for g in rules["gates"]:
        for c in others:
            if g["_re"].match((c.get("body") or "").lstrip()) and authorized(g, c["user"]["login"], c.get("author_association", "NONE")):
                done = bool(g["_then"]) and any(g["_then"].match((m.get("body") or "").lstrip())
                                                for m in comments if m["user"]["login"] == me)
                fp["gates"][g["id"]] = {"by": c["user"]["login"], "done": done, "bot": is_bot(c["user"]["login"], bots)}
                break
    if fp["pr"]:
        pr = github.gh_get(f"repos/{repo}/pulls/{n}")
        reviews = github.paginate(f"repos/{repo}/pulls/{n}/reviews")
        fp["merged"] = bool(pr.get("merged"))
        # Review ids, not (reviewer, state) pairs: a second approval after changes-requested is new.
        fp["reviews"] = sorted([r["id"], r["user"]["login"], r["state"]] for r in reviews if not is_bot(r["user"]["login"], bots))
    return fp


def changes(old, new, me, rules):
    """[(kind, message, url or None)] for what changed. On first sight of an item (old None) only
    gates are evaluated, so an /accept already waiting alerts once; everything else seeds quietly."""
    msg, out, fired = rules["messages"], [], 0
    for g in rules["gates"]:
        hit = new.get("gates", {}).get(g["id"])
        if hit and g["id"] not in (old or {}).get("gates", {}):
            text = (g.get("alert_done") or g["id"].upper() + " by @{actor}") if hit["done"] else g["alert"]
            out.append(("gate", text.format(actor=hit["by"]), None))
            fired += 0 if hit.get("bot") else 1
    if old is None:
        return out
    if me in new["assignees"] and me not in old.get("assignees", []):
        out.append(("assigned", msg.get("assigned", "ASSIGNED to you"), None))
    for t in rules["label_transitions"]:
        def holds(fp, t=t):
            labels = set(fp.get("labels", []))
            return ((not t.get("assigned_to_me") or me in fp.get("assignees", []))
                    and set(t.get("has", [])) <= labels and not set(t.get("lacks", [])) & labels)
        if holds(new) and not holds(old):
            out.append(("label_rule", t["alert"], None))
    if old.get("state") == "closed" and new["state"] == "open" and me not in new["assignees"]:
        out.append(("reopened", msg.get("reopened", "REOPENED"), None))
    elif old.get("state") != new["state"]:
        out.append(("state", f"state: {old.get('state')} -> {new['state']}", None))
    added = sorted(set(new["labels"]) - set(old.get("labels", [])))
    removed = sorted(set(old.get("labels", [])) - set(new["labels"]))
    if added or removed:
        out.append(("labels", "labels: " + " ".join(["+" + x for x in added] + ["-" + x for x in removed]), None))
    if new.get("merged") and not old.get("merged"):
        out.append(("merged", "MERGED", None))
    known_ids = {r[0] for r in old.get("reviews", []) if len(r) == 3}
    legacy = {tuple(r) for r in old.get("reviews", []) if len(r) == 2}  # v0 state stored (login, state)
    fresh = [r for r in new.get("reviews", []) if r[0] not in known_ids and (r[1], r[2]) not in legacy]
    if fresh:
        out.append(("review", "review: " + ", ".join(f"@{u} {st}" for _, u, st in fresh), None))
    known = {ref[0] for ref in old.get("xrefs", [])}
    target = new["url"].rstrip("/").split("/")[-1]
    for url, kind, author, cl in new.get("xrefs", []):
        if url in known:
            continue
        fields = dict(number=url.rstrip("/").split("/")[-1], author=author, kind=kind, target=target)
        if cl and kind == "pr":
            out.append(("competing_pr", msg.get("competing_pr", "COMPETING PR #{number}").format(**fields), url))
        else:
            out.append(("reference", msg.get("reference", "referenced by #{number}").format(**fields), url))
    if any(r.search(new.get("title", "")) for r in rules["quiet_titles"]):
        d = new.get("mentions_me", 0) - old.get("mentions_me", 0)
        if d > 0:
            out.append(("mentions", f"{d} comment(s) naming you", None))
    else:
        d = new["human_comments"] - old.get("human_comments", 0) - fired  # a gate comment already alerted
        if d > 0:
            out.append(("comments", f"{d} new comment(s) from people", None))
    return out


def notification_asks(seen, retention_days, now, live, repos=("*",)):
    """Unread notifications, any repo, where someone asked for you; once per update.
    Every id GitHub still returns goes into `live`, so pruning never forgets it."""
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - retention_days * 86400))
    fresh = []
    for n in github.paginate("notifications", {"participating": "true", "since": since}, per_page=50):
        live.add(n["id"])
        if not repo_matches(n["repository"]["full_name"], repos) or n["reason"] not in ASKS or seen.get(n["id"]) == n["updated_at"]:
            continue
        seen[n["id"]] = n["updated_at"]
        repo = n["repository"]["full_name"]
        url = github.html_url((n.get("subject") or {}).get("url")) or n["repository"].get("html_url", "")
        fresh.append({"kind": "notification", "key": repo, "title": f"{repo}: someone {ASKS[n['reason']]}",
                      "message": n["subject"]["title"][:100], "url": url})
    return fresh


def claimable_asks(seen, repo, rules, groups, now, live):
    """Rows newly listed as claimable for your groups on the repo's current claim-board issue."""
    cl = rules["claimable"]
    found = github.search_issues(f"repo:{repo} {cl['search']}", sort="created", order="desc")
    board = next((i for i in found if cl["_title"].search(i["title"])), None)
    if not board:
        return []
    fresh = []
    for c in github.paginate(f"repos/{repo}/issues/{board['number']}/comments"):
        for group in groups:
            for number, title, url in parse_claimable(c.get("body") or "", cl, group):
                key = f"{repo}#{number}"
                live.add(key)
                if key not in seen:
                    seen[key] = now
                    fresh.append({"kind": "claimable", "key": key, "title": cl["alert"].format(group=group),
                                  "message": f"#{number} {title}"[:120], "url": safe_url(url, board.get("html_url", ""))})
    return fresh
