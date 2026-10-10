"""EBI round 4, from two days of real alerts: say who and what, keep megathreads quiet, one line
per item, no references on settled items, and ride out network blips."""
import time

import pytest
from conftest import FIXTURES

from gh_upstream_watch import cli, core, github, packs

ME = "octocat"
NOTES = "notifications?all=true&page=1&participating=true&per_page=50&since=SINCE"
RULES = packs.for_repo(packs.load([FIXTURES / "packs"]), "acme/widgets")  # quiet: ^\[Community\]
REBASE = ("This pull request has merge conflicts that must be resolved before it can be\nmerged. "
          "Please rebase the PR, @octocat.\n\nhttps://docs.github.com/en/pull-requests")


def note(title="Keep null stop fields", latest=None, n=56376, kind="pulls", reason="mention"):
    return {"id": "n1", "reason": reason, "updated_at": "2026-10-02T00:44:00Z", "repository": {"full_name": "acme/widgets"},
            "subject": {"title": title, "url": f"https://api.github.com/repos/acme/widgets/{kind}/{n}",
                        "latest_comment_url": latest}}


def asks(fake, *notes, seen=None):
    fake.responses[NOTES] = list(notes)
    return core.notification_asks(seen if seen is not None else {}, 30, 1.79e9, set(), ("*",), ME)


def test_a_mention_says_who_and_what_from_the_latest_comment(fake):
    fake.responses["repos/acme/widgets/issues/comments/9"] = {
        "user": {"login": "mergify[bot]"}, "body": REBASE, "html_url": "https://github.com/acme/widgets/pull/56376#issuecomment-9"}
    got = asks(fake, note(latest="https://api.github.com/repos/acme/widgets/issues/comments/9"))
    assert got[0]["key"] == "acme/widgets#56376" and got[0]["title"] == "acme/widgets#56376 Keep null stop fields"
    assert got[0]["message"].startswith('@mergify[bot] mentioned you: "…') and "Please rebase the PR, @octocat" in got[0]["message"]
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
L70 = "https://api.github.com/repos/acme/widgets/issues/comments/70"  # GitHub's latest comment: a reply that does not name you


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
    fake.responses["repos/acme/widgets/issues/comments/70"] = {"user": {"login": "x"}, "body": "+1", "created_at": "2026-09-30T00:00:00Z"}
    fake.responses["repos/acme/widgets/issues/3983/comments?per_page=100&since=SINCE"] = [{"user": {"login": "x"}, "body": "+1"}]
    fake.responses["repos/acme/widgets/issues/3983"] = {"user": {"login": "lead"}, "body": "Members: @octocat",
                                                        "created_at": "2026-09-01T00:00:00Z"}
    later = asks(fake, note(BOARD, L70, 3983, "issues"), seen={"n1": "2026-09-30T00:00:00Z"})
    assert later == [], "the body is older than the last update already handled"
    fake.responses[NOTES] = [note(BOARD, "https://api.github.com/repos/acme/widgets/issues/3983", 3983, "issues")]
    got = core.notification_asks({}, 30, 1.79e9, set(), ("*",), ME)
    assert got and got[0]["message"].startswith("@lead mentioned you"), "a thread seen first reads a body from the window"


# --- red team of 0.2.3 (Opus) --------------------------------------------------------------------

def test_a_quiet_discussion_mention_alerts_because_nothing_could_be_read(fake):
    """M1: a discussion has no subject url; no lookup is possible, so never 'nobody named you'."""
    d = dict(note(BOARD), subject={"title": BOARD, "url": None, "latest_comment_url": None})
    assert [a["message"] for a in asks(fake, d)] == ["someone mentioned you"]


def test_the_window_is_the_last_handled_update_not_three_days(fake):
    """M2: a maintainer's ask older than 3 days, after the laptop slept, is still found."""
    old_ask = {"user": {"login": "maint"}, "author_association": "OWNER", "body": "@octocat please rebase",
               "created_at": "2026-09-20T00:00:00Z", "html_url": "https://github.com/acme/widgets/issues/8#c1"}
    fake.responses["repos/acme/widgets/issues/8/comments?per_page=100&since=SINCE"] = [old_ask, {"user": {"login": "x"}, "body": "+1"}]
    got = asks(fake, note(BOARD, None, 8, "issues"), seen={"n1": "2026-09-19T00:00:00Z"})
    assert got[0]["message"] == '@maint mentioned you: "@octocat please rebase"'
    assert any("since=SINCE" in a[-1] or "since=2026-09-19" in " ".join(a) for a in fake.calls)


def test_a_quiet_pr_without_a_comment_naming_you_alerts(fake):
    """M2: a PR's ask may be in a review; when the reviews cannot be read it is unknown, so it alerts."""
    fake.responses["repos/acme/widgets/issues/9/comments?per_page=100&since=SINCE"] = [{"user": {"login": "x"}, "body": "+1"}]
    fake.responses["repos/acme/widgets/issues/9"] = {"user": {"login": "x"}, "body": "a PR", "created_at": "2020-01-01T00:00:00Z"}
    got = asks(fake, note(BOARD, "https://api.github.com/repos/acme/widgets/pulls/9", 9, "pulls"),
               seen={"n1": "2026-09-30T00:00:00Z"})
    assert [a["message"] for a in got] == ["someone mentioned you"]


def test_a_maintainers_ask_beats_a_later_troll(fake):
    """M3: the newest mention from a maintainer is shown; the others are named, not hidden."""
    fake.responses["repos/acme/widgets/issues/comments/9"] = {
        "user": {"login": "troll"}, "author_association": "NONE", "body": "@octocat lol ignore this bot spam",
        "created_at": "2026-10-02T02:00:00Z", "html_url": "https://github.com/acme/widgets/issues/5#troll"}
    fake.responses["repos/acme/widgets/issues/5/comments?per_page=100&since=SINCE"] = [
        {"user": {"login": "maint"}, "author_association": "MEMBER", "body": "@octocat please rebase, release blocker",
         "created_at": "2026-10-02T01:00:00Z", "html_url": "https://github.com/acme/widgets/issues/5#maint"},
        fake.responses["repos/acme/widgets/issues/comments/9"]]
    got = asks(fake, note(latest="https://api.github.com/repos/acme/widgets/issues/comments/9", n=5, kind="issues"))
    assert got[0]["message"] == '@maint (also @troll) mentioned you: "@octocat please rebase, release blocker"'
    assert got[0]["url"].endswith("#maint")


def test_webhook_text_cannot_ping_a_channel_or_disguise_a_link(monkeypatch):
    """M4: Slack renders <!channel> and <url|label> in webhook text."""
    from gh_upstream_watch import notify
    sent = {}

    class R:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(notify._OPENER, "open", lambda req, timeout: sent.update(body=req.data) or R())
    al = cli.alert(0, "notification", "a/b#1", "a/b#1 x", '@t mentioned you: "<!channel> <https://evil|github.com> & go"', "")
    assert notify.send_one("webhook", al, webhook="https://hooks.example/x")
    import json
    text = json.loads(sent["body"])["text"]
    assert "<!channel>" not in text and "<https://evil" not in text and "&lt;!channel&gt;" in text and "&amp;" in text


def test_mention_lookups_are_capped_per_run(fake):
    """L1: spam beyond the cap alerts as 'someone mentioned you' without more API calls."""
    notes = [dict(note(f"t{i}", None, 100 + i, "issues"), id=f"n{i}") for i in range(core.LOOKUPS_PER_RUN + 5)]
    for i in range(core.LOOKUPS_PER_RUN + 5):
        fake.responses[f"repos/acme/widgets/issues/{100 + i}/comments?per_page=100&since=SINCE"] = []
    got = asks(fake, *notes)
    lookups = [c for c in fake.calls if "/comments" in " ".join(c)]
    assert len(got) == core.LOOKUPS_PER_RUN + 5 and len(lookups) == core.LOOKUPS_PER_RUN


def test_a_hidden_mention_does_not_name_you(fake):
    """L2: @you in an HTML comment, a quote or code is not someone naming you."""
    for hidden in ("<!-- @octocat -->", "> @octocat said", "`@octocat`", "```\n@octocat\n```"):
        fake.responses["repos/acme/widgets/issues/comments/70"] = {"user": {"login": "x"}, "body": "+1", "created_at": "2026-09-30T00:00:00Z"}
        fake.responses["repos/acme/widgets/issues/3983/comments?per_page=100&since=SINCE"] = [
            {"user": {"login": "x"}, "body": hidden, "created_at": "2026-10-01T00:00:00Z"}]
        fake.responses["repos/acme/widgets/issues/3983"] = {"user": {"login": "lead"}, "body": "board", "created_at": "2020-01-01T00:00:00Z"}
        assert asks(fake, note(BOARD, L70, 3983, "issues"), seen={"n1": "2026-09-30T00:00:00Z"}) == [], hidden


def test_fold_ignores_repo_name_case():
    """L3: --repos Acme/Widgets and GitHub's acme/widgets are the same item."""
    item = cli.alert(0, "labels", "Acme/Widgets#7", "t", "labels: +x", "")
    ask = cli.alert(0, "notification", "acme/widgets#7", "t", "someone assigned you", "u")
    assert len(cli.fold([item, ask])) == 1


def test_a_mention_already_handled_is_not_found_again(fake):
    """A comment at or before the last handled update does not vouch for a later post."""
    fake.responses["repos/acme/widgets/issues/comments/70"] = {"user": {"login": "x"}, "body": "+1", "created_at": "2026-09-30T00:00:00Z"}
    fake.responses["repos/acme/widgets/issues/3983/comments?per_page=100&since=SINCE"] = [
        {"user": {"login": "maint"}, "body": "@octocat please review", "created_at": "2026-10-02T00:00:00Z"},
        {"user": {"login": "x"}, "body": "unrelated", "created_at": "2026-10-03T00:00:00Z"}]
    fake.responses["repos/acme/widgets/issues/3983"] = {"user": {"login": "lead"}, "body": "board", "created_at": "2020-01-01T00:00:00Z"}
    assert asks(fake, note(BOARD, L70, 3983, "issues"), seen={"n1": "2026-10-02T00:00:00Z"}) == []



# --- red team of 0.2.3 (Codex, Grok) ------------------------------------------------------------

def test_an_unexplained_update_on_a_quiet_thread_alerts(fake):
    """Grok 5: no latest comment from GitHub, nothing found, and the item itself unreadable: unknown, so alert."""
    fake.responses["repos/acme/widgets/issues/3983/comments?per_page=100&since=SINCE"] = []
    assert [a["message"] for a in asks(fake, note(BOARD, None, 3983, "issues"), seen={"n1": "2026-09-30T00:00:00Z"})] == \
        ["someone mentioned you"]


def test_a_discussion_never_shares_an_issue_key(fake):
    """Codex/Grok 7: discussion #42 must not fold into issue #42."""
    d = dict(note("Roadmap", None, 42, "issues", reason="assign"))
    d["subject"] = dict(d["subject"], type="Discussion", url="https://api.github.com/repos/acme/widgets/discussions/42")
    assert asks(fake, d)[0]["key"] == "acme/widgets discussion 42"


def test_the_snippet_shows_the_ask_not_the_filler():
    """Grok 8: 100 characters of 'ignore this' cannot push the real ask out of view."""
    body = "ignore this, already approved, nothing to do here " * 3 + "@octocat please rebase before Friday"
    got = core.around(body, "octocat")
    assert "@octocat please rebase before Friday" in got and got.startswith("…")
    assert core.around("@octocat short", "octocat") == "@octocat short"



def test_every_issue_in_a_closing_list_counts():
    """Sonnet B1: 'Closes #5, #6' must flag #6 too, or a settled #6 hears nothing at all."""
    for body in ("Closes #5, #6", "Closes #5 and #6", "Fixes #5, #4, #6", "Resolves #5 & acme/widgets#6"):
        assert core.closes(body, "acme/widgets", "acme/widgets", 6), body
    assert not core.closes("Closes #5. See also #6", "acme/widgets", "acme/widgets", 6), "a later sentence is not the list"
    assert not core.closes("Closes #5, #60", "acme/widgets", "acme/widgets", 6)
