"""0.4.3: GitHub labels a maintainer whose org membership is private CONTRIBUTOR. Merging PRs in the repo
proves the role, so such a comment or review is a maintainer touch (Switchyard#855, afourniernv)."""
import time

import pytest
from conftest import FIXTURES

from gh_upstream_watch import core, github, packs

ME = "octocat"
R = "acme/widgets"
RULES = packs.for_repo(packs.load([FIXTURES / "packs"]), R)
DAY = 86400


def search_key(login):
    return f"search/issues?per_page={core.MERGER_PRS}&q=repo:{R} is:pr is:merged reviewed-by:{login} -author:{login}&sort=updated"


def item(fake, n, comments, reviews=None, author=ME):
    fake.responses.update({
        f"repos/{R}/issues/{n}": {"number": n, "title": "Fix", "html_url": f"https://github.com/{R}/issues/{n}",
                                  "state": "open", "comments": len(comments), "labels": [], "assignees": [],
                                  "user": {"login": author}, "created_at": "2026-09-01T00:00:00Z", "body": "",
                                  **({"pull_request": {}} if reviews is not None else {})},
        f"repos/{R}/issues/{n}/comments?page=1&per_page=100": comments,
        f"repos/{R}/issues/{n}/timeline?page=1&per_page=100": [],
    })
    if reviews is not None:
        fake.responses[f"repos/{R}/pulls/{n}"] = {"merged": False, "mergeable_state": "clean"}
        fake.responses[f"repos/{R}/pulls/{n}/reviews?page=1&per_page=100"] = reviews
        fake.responses[f"repos/{R}/pulls/{n}/comments?page=1&per_page=100"] = []


def comment(i, login, assoc, at):
    return {"id": i, "user": {"login": login}, "author_association": assoc, "body": "a question", "created_at": at}


def merges(fake, login, *merged_by):
    """`login`'s newest reviewed, merged PRs here, and who merged each."""
    fake.responses[search_key(login)] = {"items": [{"number": 40 + i} for i in range(len(merged_by))]}
    for i, m in enumerate(merged_by):
        fake.responses[f"repos/{R}/pulls/{40 + i}"] = {"merged_by": {"login": m}}


def searches(fake):
    return sum("search/issues" in argv for argv in fake.calls)


def test_a_contributor_who_merges_prs_here_is_a_maintainer(fake):
    item(fake, 1, [comment(1, "lead", "CONTRIBUTOR", "2026-09-27T16:39:39Z")])
    merges(fake, "lead", "other", "lead")
    assert core.fingerprint(R, 1, ME, RULES)["their_at"] == "2026-09-27T16:39:39Z"


def test_a_contributor_who_never_merged_here_is_not(fake):
    item(fake, 1, [comment(1, "helper", "CONTRIBUTOR", "2026-09-27T00:00:00Z")])
    merges(fake, "helper", "lead", "lead")
    assert core.fingerprint(R, 1, ME, RULES)["their_at"] is None


def test_a_review_by_a_contributor_who_merges_here_counts(fake):
    item(fake, 1, [], reviews=[{"id": 1, "user": {"login": "lead"}, "state": "COMMENTED",
                                "author_association": "CONTRIBUTOR", "submitted_at": "2026-09-28T00:00:00Z"}])
    merges(fake, "lead", "lead")
    assert core.fingerprint(R, 1, ME, RULES)["their_at"] == "2026-09-28T00:00:00Z"


def test_no_lookup_when_a_labeled_maintainer_spoke_last_and_one_per_login(fake):
    item(fake, 1, [comment(1, "helper", "CONTRIBUTOR", "2026-09-01T00:00:00Z"),
                   comment(2, "maint", "COLLABORATOR", "2026-09-02T00:00:00Z")])
    assert core.fingerprint(R, 1, ME, RULES)["their_at"] == "2026-09-02T00:00:00Z"
    assert searches(fake) == 0, "an older unlabeled comment cannot be the latest touch"
    item(fake, 2, [comment(3, "lead", "CONTRIBUTOR", "2026-09-03T00:00:00Z")])
    item(fake, 3, [comment(4, "lead", "CONTRIBUTOR", "2026-09-04T00:00:00Z")])
    merges(fake, "lead", "lead")
    seen = {}
    assert core.fingerprint(R, 2, ME, RULES, mergers=seen)["their_at"] == "2026-09-03T00:00:00Z"
    assert core.fingerprint(R, 3, ME, RULES, mergers=seen)["their_at"] == "2026-09-04T00:00:00Z"
    assert searches(fake) == 1, "one lookup per login"
    assert seen[R]["lead"]["merges"] is True, "the answer is kept for the state file"


def test_only_your_own_items_are_looked_up(fake):
    """inbox shows the maintainer touch only on your own open work: elsewhere a lookup is wasted."""
    item(fake, 1, [comment(1, "lead", "CONTRIBUTOR", "2026-09-27T00:00:00Z")], author="someone")
    assert core.fingerprint(R, 1, ME, RULES)["their_at"] is None and searches(fake) == 0


def test_a_remembered_answer_costs_no_call(fake):
    item(fake, 1, [comment(1, "lead", "CONTRIBUTOR", "2026-09-27T00:00:00Z")])
    seen = {R: {"lead": {"merges": True, "at": time.time()}}}
    assert core.fingerprint(R, 1, ME, RULES, mergers=seen)["their_at"] == "2026-09-27T00:00:00Z"
    assert searches(fake) == 0


def test_answers_older_than_a_week_are_asked_again():
    now = 1.79e9
    saved = {R: {"old": {"merges": True, "at": now - 8 * DAY}, "new": {"merges": False, "at": now - DAY}},
             "acme/gone": {"old": {"merges": True, "at": now - 30 * DAY}}}
    assert core.remembered(saved, now) == {R: {"new": {"merges": False, "at": now - DAY}}}


def test_a_none_commenter_costs_no_lookup(fake):
    item(fake, 1, [comment(1, "drive-by", "NONE", "2026-09-27T00:00:00Z")])
    assert core.fingerprint(R, 1, ME, RULES)["their_at"] is None and searches(fake) == 0


def test_a_failed_lookup_is_unknown_not_no_maintainer(fake):
    item(fake, 1, [comment(1, "lead", "CONTRIBUTOR", "2026-09-27T00:00:00Z")])
    fake.responses[search_key("lead")] = {"__error__": "gh: Server Error (HTTP 500)"}
    with pytest.raises(github.GHError):
        core.fingerprint(R, 1, ME, RULES)
