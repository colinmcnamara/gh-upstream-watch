"""EBI round 4, from two days of real alerts: say who and what, keep megathreads quiet, one line
per item, no references on settled items, and ride out network blips."""
import time

import pytest
from conftest import FIXTURES

from gh_upstream_watch import cli, core, github, packs

ME = "octocat"
NOTES = "notifications?page=1&participating=true&per_page=50&since=SINCE"
RULES = packs.for_repo(packs.load([FIXTURES / "packs"]), "acme/widgets")  # quiet: ^\[Community\]
REBASE = ("This pull request has merge conflicts that must be resolved before it can be\nmerged. "
          "Please rebase the PR, @octocat.\n\nhttps://docs.github.com/en/pull-requests")


def note(title="Keep null stop fields", latest=None, n=56376, kind="pulls", reason="mention"):
    return {"id": "n1", "reason": reason, "updated_at": "2026-10-02T00:44:00Z", "repository": {"full_name": "acme/widgets"},
            "subject": {"title": title, "url": f"https://api.github.com/repos/acme/widgets/{kind}/{n}",
                        "latest_comment_url": latest}}


def asks(fake, *notes):
    fake.responses[NOTES] = list(notes)
    return core.notification_asks({}, 30, 1.79e9, set(), ("*",), ME, lambda r: RULES["quiet_titles"])


def test_a_mention_says_who_and_what_from_the_latest_comment(fake):
    fake.responses["repos/acme/widgets/issues/comments/9"] = {
        "user": {"login": "mergify[bot]"}, "body": REBASE, "html_url": "https://github.com/acme/widgets/pull/56376#issuecomment-9"}
    got = asks(fake, note(latest="https://api.github.com/repos/acme/widgets/issues/comments/9"))
    assert got[0]["key"] == "acme/widgets#56376" and got[0]["title"] == "acme/widgets#56376 Keep null stop fields"
    assert got[0]["message"].startswith('@mergify[bot] mentioned you: "This pull request has merge conflicts')
    assert got[0]["url"].endswith("#issuecomment-9"), "the link opens the comment itself"


def test_when_github_gives_no_latest_comment_the_thread_is_searched(fake):
    """Real #56376 shape: latest_comment_url null, the mention is mergify's rebase request."""
    fake.responses["repos/acme/widgets/issues/56376/comments?per_page=100&since=SINCE"] = [
        {"user": {"login": "maint"}, "body": "LGTM", "html_url": "u1"},
        {"user": {"login": "mergify[bot]"}, "body": REBASE, "html_url": "https://github.com/acme/widgets/pull/56376#c2"}]
    got = asks(fake, note(latest=None))
    assert got[0]["message"].startswith("@mergify[bot] mentioned you:") and got[0]["url"].endswith("#c2")


def test_a_failed_lookup_still_alerts_without_who(fake):
    assert asks(fake, note(latest=None))[0]["message"] == "someone mentioned you"


def test_a_megathread_mention_needs_a_comment_that_names_you(fake):
    """Real #3983 shape: GitHub calls every later post a 'mention'; the latest names someone else."""
    board = "[Community] Workgroup Issues · 2026-09-21 – 2026-09-27"
    fake.responses["repos/acme/widgets/issues/comments/7"] = {"user": {"login": "zi"}, "body": "That's a solid analysis", "html_url": "u"}
    fake.responses["repos/acme/widgets/issues/3983/comments?per_page=100&since=SINCE"] = []
    fake.responses["repos/acme/widgets/issues/3983"] = {"user": {"login": "lead"}, "body": "Weekly board"}
    assert asks(fake, note(board, "https://api.github.com/repos/acme/widgets/issues/comments/7", 3983, "issues")) == []
    fake.responses["repos/acme/widgets/issues/comments/7"]["body"] = "@octocat can you take this one?"
    got = asks(fake, note(board, "https://api.github.com/repos/acme/widgets/issues/comments/7", 3983, "issues"))
    assert got[0]["message"] == '@zi mentioned you: "@octocat can you take this one?"'


def test_one_line_per_item_per_run():
    item = cli.alert(0, "labels", "acme/widgets#56376", "acme/widgets#56376 Keep", "labels: +needs-rebase", "")
    ask = cli.alert(0, "notification", "acme/widgets#56376", "acme/widgets#56376 Keep", '@mergify[bot] mentioned you: "rebase"', "")
    other = cli.alert(0, "notification", "acme/widgets#9", "acme/widgets#9 x", "someone assigned you", "")
    ask["url"] = "https://github.com/acme/widgets/pull/56376#c9"
    out = cli.fold([item, ask, other])
    assert [al["key"] for al in out] == ["acme/widgets#56376", "acme/widgets#9"]
    assert out[0]["message"] == '@mergify[bot] mentioned you: "rebase"; labels: +needs-rebase'
    assert (out[0]["action"], out[0]["kind"], out[0]["url"]) == (True, "notification", ask["url"]), \
        "the ask is what needs you: the line is labelled and linked as the ask"
    gate = cli.alert(0, "gate", "acme/widgets#1", "t", "ACCEPTED", "https://github.com/acme/widgets/issues/1")
    out = cli.fold([gate, cli.alert(0, "notification", "acme/widgets#1", "t", "someone assigned you", "x")])
    assert out[0]["kind"] == "gate" and out[0]["url"].endswith("/issues/1"), "an action alert keeps its own kind"


def fp(**kw):
    base = {"title": "Resolve blockers", "url": "https://github.com/acme/widgets/pull/4125", "state": "open",
            "labels": [], "assignees": [], "human_comments": 0, "xrefs": [], "gates": {}, "max_comment_id": 1,
            "human_ids": [], "mention_ids": []}
    return dict(base, **kw)


def test_references_stop_once_an_item_has_settled():
    """Real #4125 shape: merged a week earlier, then referenced by unrelated PRs."""
    week_ago = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 8 * 86400))
    ref = ["https://github.com/acme/widgets/pull/2899", "pr", "someone", False]
    closed = dict(state="closed", closed_at=week_ago)
    assert core.changes(fp(**closed), fp(**closed, xrefs=[ref]), ME, RULES) == []
    assert core.changes(fp(), fp(xrefs=[ref]), ME, RULES)[0][0] == "reference", "an open item still hears about it"
    closing = ["https://github.com/acme/widgets/pull/2900", "pr", "someone", True]
    assert core.changes(fp(**closed), fp(**closed, xrefs=[closing]), ME, RULES)[0][0] == "competing_pr", "never silenced"


def test_references_on_a_megathread_are_not_news():
    board = "[Community] Workgroup Issues"
    ref = ["https://github.com/acme/widgets/issues/4476", "issue", "someone", False]
    assert core.changes(fp(title=board), fp(title=board, xrefs=[ref]), ME, RULES) == []


@pytest.mark.parametrize("blip", ['Get "https://api.github.com/x": dial tcp: i/o timeout', "gh: Server Error (HTTP 502)",
                                  "read: connection reset by peer"])
def test_a_network_blip_gets_one_quick_retry(monkeypatch, blip):
    errors, slept = iter([blip, None]), []

    def run(argv):
        e = next(errors)
        if e:
            raise github.GHError(e)
        return "{}"
    monkeypatch.setattr(github, "_run", run)
    monkeypatch.setattr(github.time, "sleep", slept.append)
    assert github.gh_get("x") == {} and slept == [2]


def test_a_blip_that_persists_is_still_unknown(monkeypatch):
    monkeypatch.setattr(github, "_run", lambda argv: (_ for _ in ()).throw(github.GHError("gh: Server Error (HTTP 502)")))
    monkeypatch.setattr(github.time, "sleep", lambda s: None)
    with pytest.raises(github.GHError):
        github.gh_get("x")


def test_snippets_read_like_the_comment_not_its_markup():
    body = "<!-- workgroup-operations:weekly-wg:data-plane -->\n## Data Plane & Networking\n> quoted\nAvailable: @octocat"
    assert core.snippet(body) == "Data Plane & Networking Available: @octocat"
    assert core.snippet("word " * 40, 20).endswith("…") and len(core.snippet("word " * 40, 20)) == 20


BOARD = "[Community] Workgroup Issues · 2026-09-21 – 2026-09-27"


@pytest.mark.parametrize("reason", ["review_requested", "assign", "team_mention"])
def test_a_megathread_never_hides_a_review_request_assignment_or_team_mention(fake, reason):
    got = asks(fake, note(BOARD, None, 3983, "issues", reason=reason))
    assert len(got) == 1 and got[0]["message"].startswith("someone ")


def test_a_failed_lookup_on_a_megathread_still_alerts(fake):
    """Never 'nobody named you' when we could not look: the id is already marked seen."""
    fake.responses["repos/acme/widgets/issues/comments/7"] = {"__error__": "gh: Server Error (HTTP 502)"}
    got = asks(fake, note(BOARD, "https://api.github.com/repos/acme/widgets/issues/comments/7", 3983, "issues"))
    assert [a["message"] for a in got] == ["someone mentioned you"]


def test_a_mention_in_the_issue_body_counts(fake):
    fake.responses["repos/acme/widgets/issues/4500/comments?per_page=100&since=SINCE"] = []
    fake.responses["repos/acme/widgets/issues/4500"] = {"user": {"login": "lead"}, "body": "Owner: @octocat",
                                                        "created_at": "2026-10-01T12:00:00Z",
                                                        "html_url": "https://github.com/acme/widgets/issues/4500"}
    got = asks(fake, note("[Community] New board", None, 4500, "issues"))
    assert got[0]["message"] == '@lead mentioned you: "Owner: @octocat"'


def test_one_lookup_page_per_mention(fake):
    fake.responses["repos/acme/widgets/issues/3983/comments?per_page=100&since=SINCE"] = [
        {"user": {"login": f"u{i}"}, "body": "+1"} for i in range(100)]
    fake.responses["repos/acme/widgets/issues/3983"] = {"user": {"login": "lead"}, "body": "board"}
    asks(fake, note(BOARD, None, 3983, "issues"))
    assert sum(1 for c in fake.calls if "3983" in " ".join(c)) == 1, "one comments page, never 30"


def test_only_network_signatures_count_as_a_blip(monkeypatch):
    calls = []

    def run(argv):
        calls.append(argv)
        raise github.GHError("gh: Validation Failed: timeout must be positive (HTTP 422)")
    monkeypatch.setattr(github, "_run", run)
    monkeypatch.setattr(github.time, "sleep", lambda s: None)
    with pytest.raises(github.GHError):
        github.gh_get("x")
    assert len(calls) == 1


def test_a_full_comment_page_never_concludes_nobody_named_you(fake):
    """More than 100 comments in the window: the one naming you may be on page 2, so alert."""
    fake.responses["repos/acme/widgets/issues/3983/comments?per_page=100&since=SINCE"] = [
        {"user": {"login": f"u{i}"}, "body": "+1"} for i in range(100)]
    assert [a["message"] for a in asks(fake, note(BOARD, None, 3983, "issues"))] == ["someone mentioned you"]


def test_an_old_body_mention_does_not_vouch_for_later_posts(fake):
    """A megathread body that always named you (a roster) is not news on every later update."""
    fake.responses["repos/acme/widgets/issues/3983/comments?per_page=100&since=SINCE"] = [{"user": {"login": "x"}, "body": "+1"}]
    fake.responses["repos/acme/widgets/issues/3983"] = {"user": {"login": "lead"}, "body": "Members: @octocat",
                                                        "created_at": "2026-09-01T00:00:00Z"}
    assert asks(fake, note(BOARD, None, 3983, "issues")) == []
    fake.responses[NOTES] = [note(BOARD, "https://api.github.com/repos/acme/widgets/issues/3983", 3983, "issues")]
    got = core.notification_asks({}, 30, 1.79e9, set(), ("*",), ME, lambda r: RULES["quiet_titles"])
    assert got and got[0]["message"].startswith("@lead mentioned you"), "when GitHub's latest event is the body, it counts"
