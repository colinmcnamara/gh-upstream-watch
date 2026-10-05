"""Fingerprints of watched items, and the diff that turns two fingerprints into alerts."""
import calendar
import fnmatch
import re
import time
from typing import Any

from . import github
from .packs import authorized, parse_claimable

KEEP_IDS = 200
SETTLED_DAYS = 7  # ponytail: fixed; "referenced by" on an item closed longer than this is dropped
# Kinds that need you to do something; the rest is information. Alerts carry this as `action`.
ACTION_KINDS = {"gate", "competing_pr", "reopened", "assigned", "label_rule", "claimable", "notification",
                "mentions", "slack", "stuck", "changes_requested", "ci_failed", "conflict", "approval"}


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"
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


def who(x):
    """The login on an API object; a deleted account comes back as user: null."""
    return (x.get("user") or {}).get("login") or "ghost"


def is_bot(login, extra=()):
    login = login.lower()
    return login.endswith("[bot]") or login in BOTS or login in extra


def mentions(body, login):
    """Exact @login: not @login-bot, not someone@login.com."""
    return re.search(r"(?i)(?<![\w-])@" + re.escape(login) + r"(?![\w-])", body or "") is not None


# More targets after the first: "Closes #5, #6 and acme/x#7".
MORE_RE = re.compile(r"\s*(?:,|&|\band\b)\s*(?:https?://github\.com/([\w.-]+/[\w.-]+)/(?:issues|pull)/|([\w.-]+/[\w.-]+)#|#)(\d+)(?!\d)", re.I)


def closes(body, src_repo, repo, n):
    """True when body says it closes repo#n, including later items in a list ("Closes #5, #6").
    A bare #n means the source's own repo."""
    body = body or ""
    for first in CLOSE_RE.finditer(body):
        m: re.Match[str] | None = first
        while m:
            if (m.group(1) or m.group(2) or src_repo).lower() == repo.lower() and int(m.group(3)) == n:
                return True
            m = MORE_RE.match(body, m.end())
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
    refs: dict[str, Any] = {}
    for ev in github.paginate(f"repos/{repo}/issues/{n}/timeline"):
        src = (ev.get("source") or {}).get("issue") or {}
        if ev.get("event") != "cross-referenced" or not src or who(src) == me:
            continue
        url = src["html_url"]
        prev = refs.get(url)
        cl = closes(src.get("body"), repo_of(url), repo, n) or bool(prev and prev[3])
        refs[url] = [url, "pr" if "pull_request" in src else "issue", who(src), cl]
    return sorted(refs.values())


def fingerprint(repo, n, me, rules, bots=()):
    """Everything about repo#n that can change. Raises GHError if any page fails."""
    i = github.gh_get(f"repos/{repo}/issues/{n}")  # NotFound here means the item is gone
    try:
        return _fingerprint(i, repo, n, me, rules, bots)
    except github.NotFound as e:
        # The issue is visible but a part of it is not (say, PR data the token cannot read): unknown, not gone.
        raise github.GHError(str(e))


def _fingerprint(i, repo, n, me, rules, bots):
    comments = github.paginate(f"repos/{repo}/issues/{n}/comments") if i.get("comments") else []
    others = [c for c in comments if who(c) != me]
    humans = [c["id"] for c in others if not is_bot(who(c), bots)]
    mine = [c for c in comments if who(c) == me]
    # A maintainer's touch: the latest comment (or, below, review) by a trusted human who is not you.
    touches = [c.get("created_at") for c in others if c.get("author_association") in TRUSTED and not is_bot(who(c), bots)]
    fp = {
        "title": i["title"], "url": i["html_url"], "pr": "pull_request" in i, "state": i["state"],
        "closed_at": i.get("closed_at"),
        "labels": sorted(label["name"] for label in i["labels"]),
        "assignees": sorted(a["login"] for a in i.get("assignees") or []),
        "comments": len(comments),
        "human_comments": len(humans),
        "mentions_me": sum(1 for c in others if mentions(c.get("body"), me)),
        # Ids, not counts: a deleted comment cannot hide a new one. Ids only grow, so the newest
        # KEEP_IDS are enough to count what arrived since the last run.
        "max_comment_id": max((c["id"] for c in comments), default=0),
        "human_ids": humans[-KEEP_IDS:],
        "mention_ids": [c["id"] for c in others if mentions(c.get("body"), me)],  # few: no cap, so edits are seen
        "gates": {},
        "xrefs": cross_refs(repo, n, me),
        "author": who(i), "created_at": i.get("created_at"),
        "my_at": max((c.get("created_at") or "" for c in mine), default=None),
        # When the latest comment naming you was written. Not edited: a roll call re-edited weekly is not
        # a new ask (an edit that adds @you still alerts through notifications and mention_ids).
        "mention_at": max((c.get("created_at") or "" for c in others if mentions(c.get("body"), me)), default=None),
        "names_me": who(i) != me and mentions(visible(i.get("body")), me),
    }
    for g in rules["gates"]:
        for c in others:
            if g["_re"].match((c.get("body") or "").lstrip()) and authorized(g, who(c), c.get("author_association", "NONE")):
                # Only a follow-up posted after the gate counts: an old /assign does not answer a new /accept.
                done = bool(g["_then"]) and any(g["_then"].match((m.get("body") or "").lstrip())
                                                for m in comments if who(m) == me and m["id"] > c["id"])
                fp["gates"][g["id"]] = {"by": who(c), "done": done, "bot": is_bot(who(c), bots), "cid": c["id"]}
                break
    if fp["pr"]:
        pr = github.gh_get(f"repos/{repo}/pulls/{n}")
        reviews = github.paginate(f"repos/{repo}/pulls/{n}/reviews")
        fp["merged"] = bool(pr.get("merged"))
        fp["draft"] = bool(pr.get("draft"))
        # Review ids, not (reviewer, state) pairs: a second approval after changes-requested is new.
        # Not yours: a reply on a review thread is recorded as your own COMMENTED review. It is still a reply.
        fp["my_at"] = max([fp["my_at"] or ""] + [r.get("submitted_at") or "" for r in reviews if who(r) == me]) or None
        reviews = [r for r in reviews if who(r) != me]
        fp["reviews"] = sorted([r["id"], who(r), r["state"]] for r in reviews if not is_bot(who(r), bots))
        # "dirty" is a conflict; "unknown" (GitHub still computing) is None, and the run keeps the last answer.
        ms = pr.get("mergeable_state")
        fp["conflict"] = None if ms in (None, "unknown") else ms == "dirty"
        last = {}  # each reviewer's latest verdict; a later plain comment does not clear a request
        for r in sorted(reviews, key=lambda r: r["id"]):
            if r["state"] != "COMMENTED" and not is_bot(who(r), bots):
                last[who(r)] = r
        asked = [r for r in last.values() if r["state"] == "CHANGES_REQUESTED"]
        if asked:
            fp["changes_requested"] = {"by": sorted(who(r) for r in asked), "at": max(r.get("submitted_at") or "" for r in asked)}
        touches += [r.get("submitted_at") for r in reviews if r.get("author_association") in TRUSTED and not is_bot(who(r), bots)]
        if fp["author"] == me:  # None while closed, so red CI on a reopen is a change, not a first reading
            sha = (pr.get("head") or {}).get("sha")
            fp["ci"] = ci_status(repo, sha) if fp["state"] == "open" and sha else None
    fp["their_at"] = max((t for t in touches if t), default=None)
    return fp


FAILED = ("failure", "timed_out", "startup_failure")
PASSED = ("success", "neutral", "skipped")  # cancelled is neither: pending until it is re-run


def ci_status(repo, sha):
    """{"state": failure|approval|pending|success, "failing": names, "sha": sha} for a commit, or None when no CI
    ran. "approval" is GitHub Actions waiting for a maintainer to approve a fork's workflow runs.
    Any failed call (a 404 can be a token without access) raises: CI unknown, never "passed"."""
    checks: list[dict[str, Any]] = []
    for page in range(1, github.MAX_PAGES + 1):
        r = github.gh_get(f"repos/{repo}/commits/{sha}/check-runs", {"per_page": 100, "page": page})
        checks += r.get("check_runs") or []
        if len(checks) >= (r.get("total_count") or 0) or not r.get("check_runs"):
            break
    if len(checks) < (r.get("total_count") or 0):
        raise github.Incomplete(f"{repo}@{sha}: {len(checks)} of {r['total_count']} check runs")
    runs = github.gh_get(f"repos/{repo}/actions/runs", {"head_sha": sha, "per_page": 100}).get("workflow_runs") or []
    combined = github.gh_get(f"repos/{repo}/commits/{sha}/status") or {}  # commit statuses: buildkite, DCO apps
    failing = sorted({c.get("name") or "?" for c in checks if c.get("conclusion") in FAILED})
    if combined.get("total_count") and combined.get("state") in ("failure", "error"):
        failing.append("commit status")
    if failing:
        return {"state": "failure", "failing": failing, "sha": sha}
    if any(x.get("conclusion") == "action_required" for x in runs + checks):
        return {"state": "approval", "sha": sha}
    # Anything not plainly passed (in progress, stale, a conclusion GitHub adds later) is still pending.
    if any(c.get("status") != "completed" or c.get("conclusion") not in PASSED for c in checks) or (
            combined.get("total_count") and combined.get("state") == "pending"):
        return {"state": "pending", "sha": sha}
    return {"state": "success", "sha": sha} if checks or combined.get("total_count") else None


def first_merge(repo, me, n):
    """True only when a complete search finds exactly one merged PR of yours in repo, and it is #n.
    Anything else (a lagging index, a partial result, a failed call) keeps the plain message."""
    try:
        r = github.gh_get("search/issues", {"q": f"repo:{repo} is:pr is:merged author:{me}", "per_page": 2, "page": 1})
        return (not r.get("incomplete_results") and r.get("total_count") == 1
                and [i.get("number") for i in r.get("items", [])] == [n])
    except github.GHError:
        return False


def repo_pace(repo):
    """Days from open to merge over the repo's 100 most recently updated merged PRs: the median, and
    the 90th percentile. The median alone misleads where insiders merge their own PRs within hours."""
    r = github.gh_get("search/issues", {"q": f"repo:{repo} is:pr is:merged", "sort": "updated", "order": "desc",
                                        "per_page": 100, "page": 1})
    days = sorted((_epoch(i["closed_at"]) - _epoch(i["created_at"])) / 86400 for i in r.get("items", [])
                  if i.get("closed_at") and i.get("created_at"))
    if not days:
        return None
    p90 = days[max(0, -(-len(days) * 9 // 10) - 1)]  # nearest rank
    return {"days": round(days[len(days) // 2], 1), "p90": round(p90, 1), "n": len(days)}


def ci_view(ci, by_design):
    """(state, real failures, by-design failures) of a CI reading. A failure made only of checks a pack
    says are red by design (until a maintainer acts) is "maintainer": waiting on them, not on you."""
    ci = ci or {}
    failing = ci.get("failing") or []
    real = [f for f in failing if f not in by_design]
    gated = [f for f in failing if f in by_design]
    st = ci.get("state")
    return ("maintainer" if st == "failure" and not real else st), real, gated


def changes(old, new, me, rules):
    """[(kind, message, url or None)] for what changed. On first sight of an item (old None) only
    gates are evaluated, so an /accept already waiting alerts once; everything else seeds quietly."""
    msg, out, fired, fired_ids = rules["messages"], [], 0, set()
    for g in rules["gates"]:
        hit = new.get("gates", {}).get(g["id"])
        if hit and g["id"] not in (old or {}).get("gates", {}):
            text = (g.get("alert_done") or g["id"].upper() + " by @{actor}") if hit["done"] else g["alert"]
            out.append(("gate_done" if hit["done"] else "gate", text.format(actor=hit["by"]), None))
            fired += 0 if hit.get("bot") else 1
            fired_ids.add(hit.get("cid"))
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
    elif old.get("state") != new["state"] and not (new.get("merged") and not old.get("merged")):
        out.append(("state", "reopened" if new["state"] == "open" else "closed", None))
    loud = lambda labels: {x for x in labels if not any(r.search(x) for r in rules.get("quiet_labels", []))}  # noqa: E731
    added = sorted(loud(new["labels"]) - loud(old.get("labels", [])))
    removed = sorted(loud(old.get("labels", [])) - loud(new["labels"]))
    if added or removed:
        out.append(("labels", "labels: " + " ".join(["+" + x for x in added] + ["-" + x for x in removed]), None))
    if new.get("merged") and not old.get("merged"):
        if new.get("author") == me:
            out.append(("milestone", msg.get("milestone", "MERGED: your PR is in"), None))
        elif new.get("names_me"):
            out.append(("milestone", f"MERGED: a PR by @{new['author']} that names you", None))
        else:
            out.append(("merged", "MERGED", None))
    # A recorded unknown (None) counts as "not a conflict before"; only a state from before 0.3.0 seeds.
    if new.get("conflict") and "conflict" in old and old["conflict"] is not True and new.get("author") == me:
        out.append(("conflict", "MERGE CONFLICT: rebase or merge main", None))
    nci, oci, bd = new.get("ci") or {}, old.get("ci") or {}, rules.get("ci_by_design", {})
    (ci, real, gated), (was, was_real, was_gated) = ci_view(nci, bd), ci_view(oci, bd)
    # A state from before CI was recorded seeds quietly. A new failure on a new commit is news even
    # when the last reading was already red; so is a newly gated check (the same gate on a new push is not).
    if "ci" in old and (ci != was or (ci == "failure" and (nci.get("sha"), real) != (oci.get("sha"), was_real))
                        or (ci == "maintainer" and set(gated) != set(was_gated))):
        if ci == "failure":
            out.append(("ci_failed", "CI FAILED: " + ", ".join(real[:3]), None))
        elif ci == "maintainer":
            out.append(("ci_waiting", "CI waits for a maintainer: " + "; ".join(f"{g}: {bd[g]}" for g in gated), None))
        elif ci == "success":
            out.append(("ci_passed", "CI passed", None))
        elif ci == "approval":
            out.append(("ci_waiting", "CI waiting for a maintainer to approve the workflow runs (fork PR): nothing to do", None))
    known_ids = {r[0] for r in old.get("reviews", []) if len(r) == 3}
    legacy = {tuple(r) for r in old.get("reviews", []) if len(r) == 2}  # v0 state stored (login, state)
    fresh = [r for r in new.get("reviews", []) if r[0] not in known_ids and (r[1], r[2]) not in legacy]
    if fresh:
        kind = "changes_requested" if any(st == "CHANGES_REQUESTED" for _, _, st in fresh) else "review"
        out.append((kind, ", ".join(f"{st.replace('_', ' ')} by @{u}" for _, u, st in fresh), None))
    known = {ref[0]: ref for ref in old.get("xrefs", [])}
    target = new["url"].rstrip("/").split("/")[-1]
    # Quiet only if the title was quiet before too: renaming an issue cannot silence it.
    quiet = all(any(r.search(t) for r in rules["quiet_titles"]) for t in (new.get("title", ""), old.get("title", "")))
    settled = new.get("state") == "closed" and _epoch(new.get("closed_at")) < time.time() - SETTLED_DAYS * 86400
    for url, kind, author, cl in new.get("xrefs", []):
        was = known.get(url)
        # A known reference alerts again only when an edit makes it a closing PR.
        if was and not (cl and kind == "pr" and not was[3]):
            continue
        fields = dict(number=url.rstrip("/").split("/")[-1], author=author, kind=kind, target=target)
        if cl and kind == "pr":
            out.append(("competing_pr", msg.get("competing_pr", "COMPETING PR #{number}").format(**fields), url))
        elif quiet or settled:
            continue  # a megathread, or an item closed a while ago, is referenced constantly: not news
        else:
            out.append(("reference", msg.get("reference", "referenced by #{number}").format(**fields), url))
    since = old.get("max_comment_id")
    if quiet:
        # Set difference, not "id > since": an older comment edited to name you counts too.
        d = (len(set(new.get("mention_ids", [])) - set(old.get("mention_ids", []))) if since is not None
             else new.get("mentions_me", 0) - old.get("mentions_me", 0))
        if d > 0:
            out.append(("mentions", f"{plural(d, 'comment')} naming you", None))
    else:
        if since is not None:
            d = sum(1 for i in new.get("human_ids", []) if i > since and i not in fired_ids)
        else:  # state from before v0.1.1 has counts only
            d = new["human_comments"] - old.get("human_comments", 0) - fired  # a gate comment already alerted
        if d > 0:
            out.append(("comments", f"{plural(d, 'new comment')}", None))
    return out


def snippet(body, n=100):
    """The first words of a comment as a person reads it: no HTML comments, quoted lines or heading
    marks, on one line."""
    body = re.sub(r"<!--.*?-->", " ", body or "", flags=re.S)
    text = " ".join(re.sub(r"^\s*#{1,6}\s+", "", line) for line in body.splitlines() if not line.lstrip().startswith(">"))
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


UNKNOWN = "unknown"  # who_named_me could not look: never treat that as "nobody named you"
TRUSTED = ("OWNER", "MEMBER", "COLLABORATOR")
LOOKUPS_PER_RUN = 20  # ponytail: fixed cap; mention spam beyond it alerts as "someone mentioned you"


def visible(body):
    """What a reader sees: no HTML comments, quoted lines or code. A hidden @you does not name you."""
    body = re.sub(r"<!--.*?-->|```.*?```|`[^`\n]*`", " ", body or "", flags=re.S)
    return "\n".join(line for line in body.splitlines() if not line.lstrip().startswith(">"))


def around(body, me, n=100):
    """The words around the first @you, so the ask itself shows even after 100 characters of filler."""
    text = snippet(body, 10 ** 6)
    m = re.search(r"(?i)(?<![\w-])@" + re.escape(me) + r"(?![\w-])", text)
    start = max(0, m.start() - 30) if m else 0
    out = text[start:start + n]
    return ("…" if start else "") + (out if start + n >= len(text) else out.rstrip() + "…")


def who_named_me(n, me, since, budget):
    """(login, comment-or-item, other logins) for the place in this notification's thread that @-names
    you since `since`, preferring a maintainer's mention over anyone else's. None when nothing names
    you; UNKNOWN when it could not fully look (a failed or capped lookup, a discussion, a PR whose
    reviews it does not read, more comments than one page)."""
    subject = n.get("subject") or {}
    latest = subject.get("latest_comment_url") or ""
    api = subject.get("url") or ""
    thread = api.replace("https://api.github.com/", "").replace("/pulls/", "/issues/")
    if not thread:
        return UNKNOWN  # a discussion or release: nothing to read
    if budget[0] <= 0:
        return UNKNOWN
    budget[0] -= 1
    try:
        found = []
        if "/comments/" in latest:
            found.append(github.gh_get(latest.replace("https://api.github.com/", "")))
        # One page of comments since the last update this tool handled: a megathread is never read whole.
        try:
            page = github.gh_get(f"{thread}/comments", {"since": since, "per_page": 100}) or []
        except github.GHError:
            if not found:
                raise
            page = []  # the latest comment already answers who; the page only refines it
        found += page
        # Strictly newer than the last handled update: a mention already alerted is not found again.
        newer = [c for c in found if _epoch(c.get("updated_at") or c.get("created_at") or "") > _epoch(since)]
        named = [c for c in newer if mentions(visible(c.get("body")), me) and who(c) != me]
        if named:
            named.sort(key=lambda c: c.get("created_at") or "")
            pick = next((c for c in reversed(named) if c.get("author_association") in TRUSTED), named[-1])
            others = sorted({who(c) for c in named} - {who(pick)})
            return who(pick), pick, others
        if len(page) >= 100 or "/pulls/" in api:
            return UNKNOWN  # more than one page, or a PR's reviews: never conclude nobody named you
        # The body counts only when it is the news: new since `since`, or GitHub's latest event is the
        # body itself. A megathread body that always named you does not vouch for every later post.
        item = github.gh_get(thread)
        fresh = latest.rstrip("/") == api.rstrip("/") or _epoch(item.get("created_at")) >= _epoch(since)
        if fresh and mentions(visible(item.get("body")), me) and who(item) != me:
            return who(item), item, []
        if "/comments/" not in latest and not fresh:
            return UNKNOWN  # GitHub did not say what changed (a body edit, say): do not conclude nobody
    except github.GHError:
        return UNKNOWN
    return None


def _epoch(iso):
    try:
        return calendar.timegm(time.strptime(iso or "", "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return time.time()


def notification_asks(seen, retention_days, now, live, repos=("*",), me=None, quiet=lambda repo: ()):
    """Unread notifications, any repo, where someone asked for you; once per update. Mentions say who
    and what. On a quiet-title thread (a megathread) a mention alerts only when a comment really
    names you: GitHub keeps calling every later post in a thread you were named in a "mention".
    Every id GitHub still returns goes into `live`, so pruning never forgets it."""
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - retention_days * 86400))
    fresh, budget = [], [LOOKUPS_PER_RUN]
    for n in github.paginate("notifications", {"participating": "true", "since": since}, per_page=50):
        live.add(n["id"])
        if not repo_matches(n["repository"]["full_name"], repos) or n["reason"] not in ASKS or seen.get(n["id"]) == n["updated_at"]:
            continue
        # Look at what happened since the last update of this thread that was handled, exactly; a
        # thread seen for the first time is read back over the retention window.
        prev = seen.get(n["id"]) or since
        seen[n["id"]] = n["updated_at"]
        repo, subject = n["repository"]["full_name"], n.get("subject") or {}
        title = subject.get("title") or ""
        url = github.html_url(subject.get("url")) or n["repository"].get("html_url", "")
        number = (subject.get("url") or "").rstrip("/").split("/")[-1]
        # Only issues and PRs share the owner/repo#n space; a discussion or release #42 is not issue #42.
        kind = subject.get("type") or ("PullRequest" if "/pulls/" in (subject.get("url") or "") else "Issue")
        key = f"{repo}#{number}" if number.isdigit() and kind in ("Issue", "PullRequest") else f"{repo} {kind.lower()} {number}".strip()
        verb = ASKS[n["reason"]]
        named = who_named_me(n, me, prev, budget) if me and n["reason"] == "mention" else None
        # Only a plain mention is filtered on a megathread, and only when we looked and nobody named
        # you. Review requests, assignments and team mentions always alert; so does a failed lookup.
        if n["reason"] == "mention" and named is None and any(r.search(title) for r in quiet(repo)):
            continue
        named = None if named == UNKNOWN else named
        also = f" (also @{', @'.join(named[2])})" if named and named[2] else ""
        message = (f"@{named[0]}{also} {verb}: \"{around(visible(named[1].get('body')), me)}\"" if named else f"someone {verb}")
        fresh.append({"kind": "notification", "key": key, "title": f"{key} {title[:50]}", "message": message,
                      "url": github.html_url((named[1].get("html_url") if named else "") or "") or url})
    return fresh


def approval_asks(seen, repo, now, live):
    """Workflow runs in repo waiting on an environment you can approve (a protected `pypi`, say), once
    per run. A run you cannot approve is checked again next time, not marked seen."""
    reply = github.gh_get(f"repos/{repo}/actions/runs", {"status": "waiting", "per_page": 20})
    runs = reply.get("workflow_runs") or []
    if (reply.get("total_count") or 0) > len(runs):
        raise github.Incomplete(f"{repo}: {reply['total_count']} runs waiting, read {len(runs)}")
    fresh = []
    for run in runs:
        # The attempt too: a re-run keeps the run id and waits on you again.
        key = f"{repo} run {run['id']}" + (f".{run['run_attempt']}" if run.get("run_attempt", 1) > 1 else "")
        live.add(key)
        if key in seen:
            continue
        pending = github.gh_get(f"repos/{repo}/actions/runs/{run['id']}/pending_deployments")
        if not isinstance(pending, list):  # GitHub answers a list; anything else is unknown, not "nothing pending"
            raise github.GHError(f"{repo} run {run['id']}: pending deployments: expected a list, got {type(pending).__name__}")
        envs = [(p.get("environment") or {}).get("name") or "?" for p in pending if p.get("current_user_can_approve")]
        if envs:
            seen[key] = now
            fresh.append({"kind": "approval", "key": key, "title": f"{repo} {run.get('head_branch') or run.get('name') or ''}".strip(),
                          "message": f"waiting for your approval: {', '.join(envs)}",
                          "url": safe_url(run.get("html_url"), f"https://github.com/{repo}/actions")})
    return fresh


def claimable_asks(seen, repo, rules, groups, now, live):
    """Rows newly listed as claimable for your groups on the repo's current claim-board issue."""
    cl = rules["claimable"]
    found = github.search_issues(f"repo:{repo} {cl['search']}", sort="created", order="desc")
    # Anyone can open an issue with the board's title or comment the marker: only trusted authors count.
    trusted = lambda x: authorized(cl, who(x), x.get("author_association", "NONE"))  # noqa: E731
    board = next((i for i in found if cl["_title"].search(i["title"]) and trusted(i)), None)
    if not board:
        return []
    fresh, prefix = [], f"https://github.com/{repo}/".lower()
    for c in github.paginate(f"repos/{repo}/issues/{board['number']}/comments"):
        if not trusted(c):
            continue
        for group in groups:
            for number, title, url in parse_claimable(c.get("body") or "", cl, group):
                key = f"{repo}#{number}"
                live.add(key)
                if key not in seen:
                    seen[key] = now
                    fresh.append({"kind": "claimable", "key": key, "title": cl["alert"].format(group=group),
                                  "message": f"#{number} {title}"[:120],
                                  "url": url if safe_url(url, "").lower().startswith(prefix) else board.get("html_url", "")})
    return fresh
