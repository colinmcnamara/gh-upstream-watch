#!/usr/bin/env python3
"""Writes demo/run1.json and demo/run2.json: two recorded passes over the synthetic acme/widgets repo.

You are @octocat. Between the runs:
  #388  your closed issue is reopened             -> REOPENED
  #390  comment 101 is an /accept by a collaborator -> ACCEPTED (needs page 2 of the comments;
                                                     not also counted as a new comment)
        comment 102 is a bot                      -> not counted
        PR #401 by @monalisa says "Closes: #390"  -> COMPETING PR
  #392  your PR is approved, and it drops out of the search results (index lag) but is still
        open, so it stays watched                 -> review
  #395  a [Community] megathread gets 3 comments, one naming you -> only that one alerts
  a review request arrives on acme/widgets        -> notification
  a mention in acme/gadgets (not watched)         -> ignored: notifications follow --repos
Run: python3 tests/fixtures/make_demo.py
"""
import json
import re
from pathlib import Path

R = "acme/widgets"
HERE = Path(__file__).parent / "demo"


def issue(n, title, state="open", comments=0, labels=(), assignees=(), pr=False):
    i = {"number": n, "title": title, "html_url": f"https://github.com/{R}/{'pull' if pr else 'issues'}/{n}",
         "state": state, "comments": comments, "labels": [{"name": x} for x in labels],
         "assignees": [{"login": x} for x in assignees]}
    if pr:
        i["pull_request"] = {"url": f"https://api.github.com/repos/{R}/pulls/{n}"}
    return i


def comment(login, body, assoc="NONE"):
    return {"user": {"login": login}, "body": body, "author_association": assoc}


def pages(out, path, items, per_page=100):
    """Record every page a paginating client will ask for, including the short last page."""
    # Comment ids from the issue number and position: stable across runs, growing like GitHub's.
    m = re.search(r"issues/(\d+)/comments", path)
    for idx, it in enumerate(items):
        if m and isinstance(it, dict) and "id" not in it:
            it["id"] = int(m.group(1)) * 1000 + idx + 1
    page = 1
    while True:
        chunk = items[(page - 1) * per_page: page * per_page]
        out[f"{path}{'&' if '?' in path else '?'}page={page}&per_page={per_page}"] = chunk
        if len(chunk) < per_page:
            return
        page += 1


def search(numbers):
    return {"total_count": len(numbers), "incomplete_results": False, "items": [{"number": n} for n in numbers]}


def xref(n, login, body, pr=True):
    src = {"number": n, "html_url": f"https://github.com/{R}/{'pull' if pr else 'issues'}/{n}",
           "user": {"login": login}, "body": body}
    if pr:
        src["pull_request"] = {}
    return {"event": "cross-referenced", "source": {"issue": src}}


def notification(nid, reason, repo, title, kind, n, updated):
    return {"id": nid, "reason": reason, "updated_at": updated, "repository": {"full_name": repo, "html_url": f"https://github.com/{repo}"},
            "subject": {"title": title, "url": f"https://api.github.com/repos/{repo}/{kind}/{n}"}}


def build(run):
    r = {"user": {"login": "octocat"}}
    q_open = f"search/issues?page=1&per_page=100&q=repo:{R} involves:octocat is:open"
    q_closed = f"search/issues?page=1&per_page=100&q=repo:{R} involves:octocat is:closed updated:>=DATE"
    r[q_open] = search([390, 392, 395] if run == 1 else [388, 390, 395])
    r[q_closed] = search([388] if run == 1 else [])

    r[f"repos/{R}/issues/388"] = issue(388, "Document the retry flag", state="closed" if run == 1 else "open")
    pages(r, f"repos/{R}/issues/388/timeline", [])

    thread = [comment(f"user{i % 7}", f"Seeing this too ({i})") for i in range(1, 101)]
    if run == 2:
        thread += [comment("maint", "/accept", "COLLABORATOR"), comment("ci-bot[bot]", "Labeled: needs-triage")]
    r[f"repos/{R}/issues/390"] = issue(390, "Widget spins forever on an empty config", comments=len(thread), labels=["bug"])
    pages(r, f"repos/{R}/issues/390/comments", thread)
    refs = [xref(350, "user3", "Related to #390", pr=False)]
    if run == 2:
        refs.append(xref(401, "monalisa", "Refactor config loading.\n\nCloses: #390"))
    pages(r, f"repos/{R}/issues/390/timeline", refs)

    r[f"repos/{R}/issues/392"] = issue(392, "Add retry backoff", pr=True)
    pages(r, f"repos/{R}/issues/392/timeline", [])
    r[f"repos/{R}/pulls/392"] = {"merged": False}
    reviews = [] if run == 1 else [{"id": 7001, "user": {"login": "maint"}, "state": "APPROVED"},
                                   {"id": 7002, "user": {"login": "review-bot[bot]"}, "state": "COMMENTED"}]
    pages(r, f"repos/{R}/pulls/392/reviews", reviews)

    mega = [comment("user1", "Agenda item: release notes"), comment("user2", "I can take the changelog"),
            comment("user4", "Need a task")]
    if run == 2:
        mega += [comment("user5", "Need a task"), comment("user6", "+1"),
                 comment("maint", "@octocat can you take the docs item?", "COLLABORATOR")]
    r[f"repos/{R}/issues/395"] = issue(395, "[Community] Weekly sync thread", comments=len(mega))
    pages(r, f"repos/{R}/issues/395/comments", mega)
    pages(r, f"repos/{R}/issues/395/timeline", [])

    notes = [notification("1001", "mention", "acme/widgets", "Flaky test in the widget suite", "issues", 12, "2026-01-05T10:00:00Z")]
    if run == 2:
        notes.append(notification("1002", "review_requested", "acme/widgets", "Tighten lint config", "pulls", 14, "2026-01-06T09:00:00Z"))
        notes.append(notification("1003", "subscribed", "acme/widgets", "Weekly digest", "issues", 15, "2026-01-06T09:30:00Z"))
        notes.append(notification("1004", "mention", "acme/gadgets", "Not a watched repo", "issues", 3, "2026-01-06T09:40:00Z"))
    pages(r, "notifications?participating=true&since=SINCE", notes, per_page=50)
    # The client sends params sorted by name; key the recordings the same way.
    return {"responses": {_sorted_key(k): v for k, v in r.items()}}


def _sorted_key(key):
    path, _, query = key.partition("?")
    return path + ("?" + "&".join(sorted(query.split("&"))) if query else "")


if __name__ == "__main__":
    HERE.mkdir(exist_ok=True)
    for run in (1, 2):
        (HERE / f"run{run}.json").write_text(json.dumps(build(run), indent=1, sort_keys=True) + "\n")
    print(f"wrote {HERE}/run1.json and run2.json")
