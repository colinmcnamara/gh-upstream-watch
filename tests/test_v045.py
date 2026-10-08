"""0.4.5, from a read-only audit of the watcher against live threads (Switchyard#855, semantic-router#4658):
a maintainer who spoke after you puts the item on you, review bodies and inline review comments are read
for mentions, and notifications you already read on another device still alert."""
import time

from gh_upstream_watch import cli, core, packs

ME = "octocat"
R = "acme/widgets"
RULES = packs.for_repo(packs.load([]), R)
NOTES = "notifications?all=true&page=1&participating=true&per_page=50&since=SINCE"


def pr(fake, n=7, author=ME, comments=(), reviews=(), inline=None, state="open"):
    fake.responses.update({
        f"repos/{R}/issues/{n}": {"number": n, "title": "Fix", "html_url": f"https://github.com/{R}/pull/{n}",
                                  "state": state, "comments": len(comments), "labels": [], "assignees": [],
                                  "user": {"login": author}, "created_at": "2026-09-26T00:00:00Z", "body": "",
                                  "pull_request": {}},
        f"repos/{R}/issues/{n}/comments?page=1&per_page=100": list(comments),
        f"repos/{R}/issues/{n}/timeline?page=1&per_page=100": [],
        f"repos/{R}/pulls/{n}": {"merged": False, "mergeable_state": "clean"},
        f"repos/{R}/pulls/{n}/reviews?page=1&per_page=100": list(reviews),
    })
    if inline is not None:
        fake.responses[f"repos/{R}/pulls/{n}/comments?page=1&per_page=100"] = list(inline)


def comment(i, login, assoc, at, body="a question"):
    return {"id": i, "user": {"login": login}, "author_association": assoc, "body": body, "created_at": at}


def review(i, login, assoc, state, at, body=""):
    return {"id": i, "user": {"login": login}, "author_association": assoc, "state": state, "submitted_at": at, "body": body}


def reasons(fp):
    return cli._you_reasons(fp, ME, {})


MERGERS = {R: {"afourniernv": {"merges": True, "at": time.time()}}}


def test_a_maintainer_question_after_your_last_word_waits_on_you(fake):
    """Switchyard#855 on 2026-09-27: your last word was a review-thread reply at 13:19; afourniernv
    asked a plain question (no @) at 16:39."""
    pr(fake, reviews=[review(1, ME, "NONE", "COMMENTED", "2026-09-27T13:19:52Z")],
       comments=[comment(2, "afourniernv", "CONTRIBUTOR", "2026-09-27T16:39:39Z")], inline=[])
    fp = core.fingerprint(R, 7, ME, RULES, mergers=MERGERS)
    assert reasons(fp) == ["maintainer replied after you"]
    pr(fake, reviews=[review(1, ME, "NONE", "COMMENTED", "2026-09-27T13:19:52Z")],
       comments=[comment(2, "afourniernv", "CONTRIBUTOR", "2026-09-27T16:39:39Z"),
                 comment(3, ME, "NONE", "2026-09-27T20:04:55Z", "Not live, no.")], inline=[])
    assert reasons(core.fingerprint(R, 7, ME, RULES, mergers=MERGERS)) == [], "you answered"


def test_an_approval_after_your_last_word_does_not(fake):
    pr(fake, comments=[comment(1, ME, "NONE", "2026-09-27T10:00:00Z")],
       reviews=[review(2, "maint", "COLLABORATOR", "APPROVED", "2026-09-28T00:00:00Z")], inline=[])
    fp = core.fingerprint(R, 7, ME, RULES)
    assert fp["their_at"] == "2026-09-28T00:00:00Z" and reasons(fp) == []


def test_only_your_own_items_wait_on_you_for_a_maintainer_reply(fake):
    pr(fake, author="someone", comments=[comment(1, ME, "NONE", "2026-09-27T10:00:00Z"),
                                         comment(2, "maint", "COLLABORATOR", "2026-09-28T00:00:00Z")])
    assert reasons(core.fingerprint(R, 7, ME, RULES)) == []
