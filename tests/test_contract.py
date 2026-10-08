"""Contract fixtures: one real `gh api` reply per shape the parser reads, trimmed and renamed to
acme/widgets. Notifications and pending deployments come from GitHub's documented examples (the real
ones are private). If GitHub's shape drifts from the hand-made demo fixtures, these show it."""
import json

import pytest
from conftest import FIXTURES

from gh_upstream_watch import core, github, packs

R, ME = "acme/widgets", "octocat"
RULES = packs.for_repo(packs.load(), R)


def shape(name):
    return json.loads((FIXTURES / "contract" / f"{name}.json").read_text())


ISSUE, PULL = shape("issue"), shape("pull")
N, SHA = ISSUE["number"], PULL["head"]["sha"]


@pytest.fixture
def item(fake):
    """The issue, its comments and its timeline, as fingerprint() asks for them."""
    fake.responses.update({
        f"repos/{R}/issues/{N}": ISSUE,
        f"repos/{R}/issues/{N}/comments?page=1&per_page=100": shape("issue_comments"),
        f"repos/{R}/issues/{N}/timeline?page=1&per_page=100": shape("timeline")})
    return fake


@pytest.fixture
def ci(fake):
    fake.responses.update({
        f"repos/{R}/commits/{SHA}/check-runs?page=1&per_page=100": shape("check_runs"),
        f"repos/{R}/actions/runs?head_sha={SHA}&per_page=100": shape("workflow_runs"),
        f"repos/{R}/commits/{SHA}/status": shape("commit_status")})
    return fake


def test_search_reply_yields_every_item_number(fake):
    fake.responses[f"search/issues?page=1&per_page=100&q=repo:{R} is:open"] = shape("search_issues")
    got = [i["number"] for i in github.search_issues(f"repo:{R} is:open")]
    assert got == [i["number"] for i in shape("search_issues")["items"]], "total_count and items read as GitHub sends them"


def test_search_reply_gives_repo_pace_its_dates(fake):
    fake.responses[f"search/issues?order=desc&page=1&per_page=100&q=repo:{R} is:pr is:merged&sort=updated"] = shape("search_issues")
    assert core.repo_pace(R)["n"] == 2, "created_at and closed_at parse as ISO 8601 UTC"


def test_issue_reply_fills_the_fingerprint(item):
    fp = core.fingerprint(R, N, ME, RULES)
    assert (fp["title"], fp["state"], fp["closed_at"], fp["labels"], fp["assignees"], fp["author"], fp["pr"]) == \
        (ISSUE["title"], "closed", ISSUE["closed_at"], ["bug", "gh-pr", "priority-3"], [ME], "hubot", False)
    assert fp["names_me"], "the body's @octocat is read"


def test_comment_reply_counts_humans_and_mentions(item):
    fp = core.fingerprint(R, N, ME, RULES)
    ids = [c["id"] for c in shape("issue_comments")]
    assert (fp["comments"], fp["human_ids"], fp["mention_ids"], fp["max_comment_id"]) == (2, ids[:1], ids[:1], max(ids)), \
        "id, user.login and body are where the parser reads them"
    assert fp["my_at"] == shape("issue_comments")[1]["created_at"] and fp["their_at"] is None, "NONE is not a maintainer"


def test_timeline_reply_yields_the_closing_pr(fake):
    fake.responses[f"repos/{R}/issues/{N}/timeline?page=1&per_page=100"] = shape("timeline")
    pr = shape("timeline")[1]["source"]["issue"]["html_url"]
    assert core.cross_refs(R, N, ME) == [[pr, "pr", "maint", True]], "source.issue, pull_request and the Fixes body"


def test_pull_and_review_replies_fill_the_pr_fields(item):
    item.responses.update({f"repos/{R}/issues/{N}": dict(ISSUE, pull_request={"url": f"https://api.github.com/repos/{R}/pulls/{N}"}),
                           f"repos/{R}/pulls/{N}": PULL,
                           f"repos/{R}/pulls/{N}/reviews?page=1&per_page=100": shape("reviews")})
    fp = core.fingerprint(R, N, ME, RULES)
    human = shape("reviews")[1]
    assert (fp["merged"], fp["draft"], fp["conflict"]) == (True, False, None), "mergeable_state unknown is no answer"
    assert fp["reviews"] == [[human["id"], "maint", "APPROVED"]], "the [bot] review is dropped"
    assert fp["their_at"] == human["submitted_at"], "a MEMBER review is a maintainer touch"


def test_check_run_reply_reads_names_and_conclusions(ci):
    assert core.ci_status(R, SHA) == {"state": "success", "sha": SHA}
    red = shape("check_runs")
    red["check_runs"][0]["conclusion"] = "failure"
    ci.responses[f"repos/{R}/commits/{SHA}/check-runs?page=1&per_page=100"] = red
    assert core.ci_status(R, SHA)["failing"] == [red["check_runs"][0]["name"]]


def test_workflow_run_reply_shows_a_fork_waiting_for_approval(ci):
    runs = shape("workflow_runs")
    runs["workflow_runs"][0]["conclusion"] = "action_required"
    ci.responses[f"repos/{R}/actions/runs?head_sha={SHA}&per_page=100"] = runs
    assert core.ci_status(R, SHA)["state"] == "approval"


def test_commit_status_reply_counts_only_when_statuses_exist(ci):
    assert shape("commit_status")["state"] == "pending" and core.ci_status(R, SHA)["state"] == "success", \
        "GitHub says pending with total_count 0 when no status app posted"
    ci.responses[f"repos/{R}/commits/{SHA}/status"] = dict(shape("commit_status"), state="failure", total_count=1)
    assert core.ci_status(R, SHA)["failing"] == ["commit status"]


def test_pending_deployment_reply_names_the_environment(fake):
    runs = shape("workflow_runs")
    fake.responses[f"repos/{R}/actions/runs?per_page=20&status=waiting"] = runs
    for r in runs["workflow_runs"]:
        fake.responses[f"repos/{R}/actions/runs/{r['id']}/pending_deployments"] = shape("pending_deployments")
    got = core.approval_asks({}, R, 1.79e9, set())
    first = runs["workflow_runs"][0]
    assert (got[0]["message"], got[0]["url"]) == ("waiting for your approval: staging", first["html_url"])


def test_notification_reply_yields_key_and_link(fake):
    key = "notifications?all=true&page=1&participating=true&per_page=50&since=SINCE"
    fake.responses[key] = shape("notifications")
    live: set[str] = set()
    assert core.notification_asks({}, 30, 1.79e9, live, [R]) == [] and live == {"1"}, "subscribed is not an ask"
    fake.responses[key] = [dict(shape("notifications")[0], reason="review_requested")]
    got = core.notification_asks({}, 30, 1.79e9, set(), [R])
    assert (got[0]["key"], got[0]["url"], got[0]["message"]) == \
        (f"{R}#123", f"https://github.com/{R}/issues/123", "someone requested your review")


def test_user_reply_carries_the_login(fake):
    fake.responses["user"] = shape("user")
    assert github.gh_get("user")["login"] == "octocat"
