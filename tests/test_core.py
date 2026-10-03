"""What fires an alert: the diff, mentions, closes/xref parsing, gates, notifications, claim boards.
Ported from the original single-file watcher's checks, with synthetic data."""
import itertools

import pytest
from conftest import FIXTURES

from gh_upstream_watch import core, github, packs

ME = "octocat"
SR = packs.for_repo(packs.load(), "vllm-project/semantic-router")  # the bundled pack with a gate
GENERIC = packs.for_repo(packs.load(), "acme/widgets")
NO_IN_PROGRESS = SR["label_transitions"][0]["alert"]

base = dict(title="Fix x", url="https://github.com/acme/widgets/issues/390", state="open", labels=[],
            assignees=[], human_comments=0, gates={})


def ch(old, new, rules=SR):
    return [msg for _, msg, _ in core.changes(old, new, ME, rules)]


def test_new_item_is_silent_and_no_change_is_silent():
    assert ch(None, {**base, "human_comments": 5, "labels": ["bug"]}) == [], "first sight seeds generic changes quietly"
    assert ch(base, dict(base)) == []


def test_first_sight_evaluates_gates_once():
    """Item 9: an /accept already waiting on a newly watched item alerts, and is not also a new comment."""
    accepted = {**base, "human_comments": 3, "gates": {"accept": {"by": "maint", "done": False, "bot": False}}}
    assert ch(None, accepted) == ["ACCEPTED by @maint: comment /assign now"]
    assert ch(accepted, dict(accepted)) == []
    assert ch({**base, "human_comments": 2}, accepted) == ["ACCEPTED by @maint: comment /assign now"], \
        "the /accept comment is not counted again as a new comment"
    assert ch({**base, "human_comments": 1}, accepted)[1] == "1 new comment"


def test_gate_alerts_once_with_next_action():
    accepted = {**base, "gates": {"accept": {"by": "maint", "done": False}}}
    assert ch(base, accepted) == ["ACCEPTED by @maint: comment /assign now"]
    assert ch(accepted, dict(accepted)) == []
    done = {**base, "gates": {"accept": {"by": "maint", "done": True}}}
    assert ch(base, done) == ["ACCEPTED by @maint"], "already asked: no call to action"


def test_assignment():
    assert ch(base, {**base, "assignees": [ME]}) == ["ASSIGNED to you: implementation may start"]
    assert ch(base, {**base, "assignees": ["someone"]}) == [], "someone else assigned is not yours"
    assert ch(base, {**base, "assignees": [ME]}, GENERIC) == ["ASSIGNED to you"]


def test_label_transition_alerts_once():
    acc = {**base, "labels": ["accepted", "wg/x"]}
    stuck = {**acc, "assignees": [ME]}
    assert ch(acc, stuck) == ["ASSIGNED to you: implementation may start", NO_IN_PROGRESS]
    assert ch(stuck, dict(stuck)) == []
    landed = {**stuck, "labels": ["accepted", "in-progress", "wg/x"]}
    assert NO_IN_PROGRESS not in ch(acc, landed)


def test_reviews_labels_merged_comments():
    pr = {**base, "reviews": [], "merged": False}
    assert ch(pr, {**pr, "reviews": [[1, "maint", "APPROVED"]]}) == ["APPROVED by @maint"]
    assert ch({**pr, "reviews": [[1, "maint", "COMMENTED"]]}, {**pr, "reviews": [[1, "maint", "COMMENTED"], [2, "maint", "APPROVED"]]}) \
        == ["APPROVED by @maint"], "only reviews that are new"
    assert ch(pr, {**pr, "labels": ["ready"]}) == ["labels: +ready"]
    assert ch({**pr, "labels": ["wip"]}, {**pr, "labels": ["ready"]}) == ["labels: +ready -wip"]
    assert ch(pr, {**pr, "merged": True}) == ["MERGED"]
    assert ch(base, {**base, "human_comments": 2}) == ["2 new comments"]


def test_xrefs():
    x = {**base, "xrefs": []}
    closing = ["https://github.com/acme/widgets/pull/401", "pr", "monalisa", True]
    mention = ["https://github.com/acme/widgets/issues/8", "issue", "someone", False]
    assert core.changes(x, {**x, "xrefs": [closing]}, ME, SR) == [
        ("competing_pr", "COMPETING PR #401 (by @monalisa) says Closes #390: check scope before /assign", closing[0])]
    assert ch(x, {**x, "xrefs": [mention]}) == ["referenced by @someone's issue #8"]
    assert ch({**x, "xrefs": [closing]}, {**x, "xrefs": [closing]}) == [], "a known reference stays silent"
    assert ch(base, {**x, "xrefs": [closing]}, GENERIC) == ["COMPETING PR #401 (by @monalisa) says Closes #390: check scope"]


def test_reopen():
    closed = {**base, "state": "closed"}
    assert ch(closed, {**closed, "state": "open"}) == ["REOPENED: comment /assign now"]
    assert ch(closed, {**closed, "state": "open"}, GENERIC) == ["REOPENED: claim it"]
    assert ch({**closed, "assignees": [ME]}, {**closed, "state": "open", "assignees": [ME]}) == ["reopened"]


def test_quiet_titles_alert_only_on_mentions():
    wg = {**base, "title": "[WG] Data plane charter", "mentions_me": 0}
    assert ch(wg, {**wg, "human_comments": 3}) == []
    assert ch(wg, {**wg, "human_comments": 1, "mentions_me": 1}) == ["1 comment naming you"]
    assert ch({**base, "title": "[Bug] x"}, {**base, "title": "[Bug] x", "human_comments": 1}) == ["1 new comment"]


@pytest.mark.parametrize("body,hit", [
    ("@octocat can you look", True), ("(@octocat)", True), ("thanks @OctoCat.", True),
    ("@octocat-bot ran", False), ("mail me@octocat.com", False), ("@octocats", False), ("octocat", False)])
def test_exact_mentions(body, hit):
    assert core.mentions(body, "octocat") is hit


@pytest.mark.parametrize("body,src,hit", [
    ("Closes #390", "acme/widgets", True),
    ("closes: #390", "acme/widgets", True),
    ("Fixed #390.", "acme/widgets", True),
    ("resolves acme/widgets#390", "other/repo", True),
    ("Fixes https://github.com/acme/widgets/issues/390", "other/repo", True),
    ("Closes #390", "other/repo", False),
    ("Closes #3900", "acme/widgets", False),
    ("Closes other/repo#390", "acme/widgets", False),
    ("Related to #390", "acme/widgets", False),
    ("prefixes #390", "acme/widgets", False)])
def test_closes(body, src, hit):
    assert core.closes(body, src, "acme/widgets", 390) is hit


IDS = itertools.count(1)


def comment(login, body, assoc="NONE"):
    return {"id": next(IDS), "user": {"login": login}, "body": body, "author_association": assoc}


def issue_fixture(fake, comments, xrefs=()):
    r = "repos/acme/widgets/issues/390"
    fake.responses = {
        r: {"title": "t", "html_url": "https://github.com/acme/widgets/issues/390", "state": "open",
            "labels": [], "assignees": [], "comments": len(comments)},
        f"{r}/comments?page=1&per_page=100": comments,
        f"{r}/timeline?page=1&per_page=100": list(xrefs)}


def test_unauthorized_accept_is_ignored(fake):
    rules = packs.for_repo(packs.load([FIXTURES / "packs"]), "acme/widgets")
    issue_fixture(fake, [comment("drive-by", "/accept")])
    assert core.fingerprint("acme/widgets", 390, ME, rules)["gates"] == {}
    issue_fixture(fake, [comment("org-member", "/accept", "MEMBER")])
    assert core.fingerprint("acme/widgets", 390, ME, rules)["gates"] == {}, "MEMBER may be read-only: not a default"
    issue_fixture(fake, [comment("maint", "> /accept\nquoting the bot", "OWNER"), comment("maint", "LGTM\n/accept", "OWNER")])
    assert core.fingerprint("acme/widgets", 390, ME, rules)["gates"] == {}, "only the start of a comment counts"
    issue_fixture(fake, [comment("drive-by", "/accept"), comment("maint", "  /accept", "COLLABORATOR"), comment(ME, "/assign")])
    gate = core.fingerprint("acme/widgets", 390, ME, rules)["gates"]["accept"]
    assert {k: gate[k] for k in ("by", "done", "bot")} == {"by": "maint", "done": True, "bot": False}
    issue_fixture(fake, [comment(ME, "/accept", "OWNER")])
    assert core.fingerprint("acme/widgets", 390, ME, rules)["gates"] == {}, "your own /accept is not a gate"


def test_login_allowlist_wins_over_association(fake):
    pack = packs.validate({"id": "p", "repos": ["acme/*"], "gates": [{
        "id": "accept", "comment": "^/accept\\b", "alert": "A by @{actor}",
        "authorized_by": {"associations": ["MEMBER"], "logins": ["lead"]}}]}, "test")
    rules = packs.for_repo([pack], "acme/widgets")
    issue_fixture(fake, [comment("maint", "/accept", "MEMBER")])
    assert core.fingerprint("acme/widgets", 390, ME, rules)["gates"] == {}
    issue_fixture(fake, [comment("lead", "/accept", "CONTRIBUTOR")])
    assert core.fingerprint("acme/widgets", 390, ME, rules)["gates"]["accept"]["by"] == "lead"


def test_bots_are_not_people(fake):
    issue_fixture(fake, [comment("ci-bot[bot]", "hi"), comment("coderabbitai", "hi"), comment("user1", "hi @octocat"),
                         comment(ME, "mine")])
    fp = core.fingerprint("acme/widgets", 390, ME, GENERIC)
    assert (fp["comments"], fp["human_comments"], fp["mentions_me"]) == (4, 1, 1)


def test_cross_refs_dedupe_and_skip_mine(fake):
    def ev(n, login, body, pr=True):
        src = {"html_url": f"https://github.com/acme/widgets/{'pull' if pr else 'issues'}/{n}", "user": {"login": login}, "body": body}
        if pr:
            src["pull_request"] = {"url": "x"}
        return {"event": "cross-referenced", "source": {"issue": src}}
    issue_fixture(fake, [], [ev(401, "monalisa", "Closes #390"), ev(401, "monalisa", "edited"), ev(402, ME, "Closes #390"),
                             {"event": "labeled"}, ev(9, "user1", "see #390", pr=False)])
    assert core.cross_refs("acme/widgets", 390, ME) == [
        ["https://github.com/acme/widgets/issues/9", "issue", "user1", False],
        ["https://github.com/acme/widgets/pull/401", "pr", "monalisa", True]]


def test_discover_encodes_search_with_f_q(fake):
    fake.responses = {
        "search/issues?page=1&per_page=100&q=repo:acme/widgets involves:octocat is:open": {"total_count": 1, "items": [{"number": 1}]},
        "search/issues?page=1&per_page=100&q=repo:acme/widgets involves:octocat is:closed updated:>=DATE":
            {"total_count": 2, "items": [{"number": 388}, {"number": 1}]}}
    assert core.discover("acme/widgets", ME, 14, 1.7e9) == {"acme/widgets#1", "acme/widgets#388"}
    assert ["-f", "q=repo:acme/widgets involves:octocat is:open"] == fake.calls[0][-2:], "gh encodes the query, not us"


def notification(nid, reason, updated):
    return {"id": nid, "reason": reason, "updated_at": updated, "repository": {"full_name": "acme/widgets"},
            "subject": {"title": "Fix x", "url": "https://api.github.com/repos/acme/widgets/issues/5"}}


def test_notifications_alert_once_per_update(fake):
    key = "notifications?page=1&participating=true&per_page=50&since=SINCE"
    fake.responses[key] = [notification("1", "mention", "t1"), notification("2", "subscribed", "t1")]
    seen, live = {}, set()
    first = core.notification_asks(seen, 30, 1.7e9, live)
    assert [(a["key"], a["title"], a["message"], a["url"]) for a in first] == [
        ("acme/widgets#5", "acme/widgets#5 Fix x", "someone mentioned you", "https://github.com/acme/widgets/issues/5")]
    assert live == {"1", "2"}
    assert core.notification_asks(seen, 30, 1.7e9, set()) == [], "the same update alerts once"
    fake.responses[key][0]["updated_at"] = "t2"
    assert len(core.notification_asks(seen, 30, 1.7e9, set())) == 1, "a new update on the thread alerts again"


BOARD = """<!-- workgroup-operations:weekly-wg:data-plane -->
### In progress
| IN_PROGRESS @a | [#1 Busy](https://github.com/acme/widgets/issues/1) |
### Available issues
| Status | Issue | Assigned (UTC) |
| --- | --- | --- |
| AVAILABLE | [#613 Add mappings](https://github.com/acme/widgets/issues/613) | - |
### Merged this week
| AVAILABLE | [#9 Not in the section](https://github.com/acme/widgets/issues/9) | - |"""


def test_parse_claimable():
    cl = SR["claimable"]
    assert packs.parse_claimable(BOARD, cl, "data-plane") == [("613", "Add mappings", "https://github.com/acme/widgets/issues/613")]
    assert packs.parse_claimable(BOARD, cl, "other") == [], "another group's comment"
    assert packs.parse_claimable("<!-- workgroup-operations:weekly-wg:data-plane -->\n### Available issues\nNone free.", cl, "data-plane") == []


PHISH = "| AVAILABLE | [#614 Phish](https://evil.example/x) |"
LEAD = {"user": {"login": "lead"}, "author_association": "MEMBER"}


def test_claimable_asks_repo_qualified_once(fake):
    repo = "acme/widgets"
    fake.responses = {
        f'search/issues?order=desc&page=1&per_page=100&q=repo:{repo} is:issue is:open in:title "Workgroup Issues"&sort=created':
            {"total_count": 1, "items": [{"number": 983, "title": "[Community] Workgroup Issues 2026-01-05",
                                          "html_url": "https://github.com/acme/widgets/issues/983", **LEAD}]},
        f"repos/{repo}/issues/983/comments?page=1&per_page=100": [{"body": BOARD.replace("### Merged", PHISH + "\n### Merged"), **LEAD}]}
    seen = {}
    got = core.claimable_asks(seen, repo, SR, ["data-plane"], 1.7e9, set())
    assert [(a["title"], a["key"]) for a in got][0] == ("CLAIMABLE in wg/data-plane: /assign now", "acme/widgets#613")
    assert got[1]["url"] == "https://github.com/acme/widgets/issues/983", "item 7: a non-GitHub link falls back to the board"
    assert core.claimable_asks(seen, repo, SR, ["data-plane"], 1.7e9, set()) == []


def test_claim_board_search_is_paginated_and_complete(fake):
    """Item 12: the board search reports total_count; a short answer is unknown, not 'no board'."""
    repo = "acme/widgets"
    fake.responses = {
        f'search/issues?order=desc&page=1&per_page=100&q=repo:{repo} is:issue is:open in:title "Workgroup Issues"&sort=created':
            {"total_count": 7, "items": [{"number": 1, "title": "old"}]}}
    with pytest.raises(github.Incomplete):
        core.claimable_asks({}, repo, SR, ["data-plane"], 1.7e9, set())


def test_review_ids_catch_a_second_approval(fake):
    """Item 10: approve, changes requested, approve again: the second approval is a new review."""
    old = {**base, "reviews": [[1, "maint", "APPROVED"], [2, "maint", "CHANGES_REQUESTED"]]}
    new = {**old, "reviews": old["reviews"] + [[3, "maint", "APPROVED"]]}
    assert ch(old, new) == ["APPROVED by @maint"]
    legacy = {**base, "reviews": [["maint", "APPROVED"]]}  # v0 state: no ids
    assert ch(legacy, {**base, "reviews": [[1, "maint", "APPROVED"]]}) == [], "migrated reviews do not re-alert"


def test_notifications_follow_watched_repos_exactly(fake):
    """Item 13: --repos acme/widgets does not pull in acme/gadgets; globs are explicit."""
    key = "notifications?page=1&participating=true&per_page=50&since=SINCE"
    other = dict(notification("9", "mention", "t1"), repository={"full_name": "acme/gadgets"})
    fake.responses[key] = [notification("1", "mention", "t1"), other]
    assert len(core.notification_asks({}, 30, 1.7e9, set(), ["acme/widgets"])) == 1
    assert len(core.notification_asks({}, 30, 1.7e9, set(), ["acme/*"])) == 2
    assert not core.repo_matches("acme/widgets-extra", ["acme/widgets"])


# --- red-team round 2 (v0.1.1) -----------------------------------------------------------------

ACME = packs.for_repo(packs.load([FIXTURES / "packs"]), "acme/widgets")


def fp(**kw):
    base = {"title": "Bug", "url": "https://github.com/acme/widgets/issues/390", "state": "open", "labels": [],
            "assignees": [], "human_comments": 0, "mentions_me": 0, "xrefs": [], "gates": {},
            "max_comment_id": 10, "human_ids": [], "mention_ids": []}
    return dict(base, **kw)


def test_an_edit_that_makes_a_known_reference_closing_alerts():
    url = "https://github.com/acme/widgets/pull/401"
    old, new = fp(xrefs=[[url, "pr", "mallory", False]]), fp(xrefs=[[url, "pr", "mallory", True]])
    assert [k for k, _, _ in core.changes(old, new, ME, ACME)] == ["competing_pr"]
    assert core.changes(new, new, ME, ACME) == [], "once"


def test_a_deleted_comment_cannot_hide_a_new_one():
    old = fp(human_comments=1, human_ids=[10], max_comment_id=10)
    new = fp(human_comments=1, human_ids=[12], max_comment_id=12)  # #10 deleted, #12 is new
    assert core.changes(old, new, ME, ACME) == [("comments", "1 new comment", None)]


def test_renaming_an_issue_to_a_quiet_title_does_not_silence_it():
    old = fp(human_ids=[10], human_comments=1)
    new = fp(title="[Community] Bug", human_ids=[10, 11], human_comments=2, max_comment_id=11)
    assert ("comments", "1 new comment", None) in core.changes(old, new, ME, ACME)


def test_state_from_before_ids_still_diffs_by_count():
    old = {k: v for k, v in fp(human_comments=1).items() if k not in ("max_comment_id", "human_ids", "mention_ids")}
    assert core.changes(old, fp(human_comments=3, human_ids=[11, 12, 13]), ME, ACME) == [
        ("comments", "2 new comments", None)]


def test_an_older_assign_does_not_answer_a_new_accept(fake):
    issue_fixture(fake, [comment(ME, "/assign"), comment("maint", "/accept", "COLLABORATOR")])
    assert core.fingerprint("acme/widgets", 390, ME, ACME)["gates"]["accept"]["done"] is False


def test_deleted_accounts_do_not_crash_an_item(fake):
    ghost = dict(comment("x", "/accept", "OWNER"), user=None)
    issue_fixture(fake, [ghost, comment("maint", "hi", "OWNER")])
    f = core.fingerprint("acme/widgets", 390, ME, ACME)
    assert f["gates"]["accept"]["by"] == "ghost" and f["human_comments"] == 2


def test_claim_board_trusts_only_authorized_authors(fake):
    repo, q = "acme/widgets", 'q=repo:acme/widgets is:issue is:open in:title "Workgroup Issues"&sort=created'
    fake_board = {"number": 999, "title": "[Community] Workgroup Issues (weekly, updated)",
                  "html_url": "https://github.com/acme/widgets/issues/999", "user": {"login": "mallory"},
                  "author_association": "NONE"}
    real_board = {"number": 983, "title": "[Community] Workgroup Issues 2026-01-05",
                  "html_url": "https://github.com/acme/widgets/issues/983", **LEAD}
    other_repo = BOARD.replace("https://github.com/acme/widgets/issues/613", "https://github.com/mallory/phish/issues/1")
    fake.responses = {
        f"search/issues?order=desc&page=1&per_page=100&{q}": {"total_count": 2, "items": [fake_board, real_board]},
        f"repos/{repo}/issues/983/comments?page=1&per_page=100": [
            {"body": BOARD.replace("#613 Add mappings", "#700 Spoofed"), "user": {"login": "mallory"}, "author_association": "NONE"},
            {"body": other_repo, **LEAD}]}
    got = core.claimable_asks({}, repo, SR, ["data-plane"], 1.7e9, set())
    assert [a["key"] for a in got] == ["acme/widgets#613"], "fake board and outsider rows are ignored"
    assert got[0]["url"] == "https://github.com/acme/widgets/issues/983", "a row link outside the repo falls back"
    assert not any(c[4].endswith("/999/comments") for c in fake.calls)


def test_not_found_and_rate_limits(monkeypatch):
    errors = iter(["gh: API rate limit exceeded (HTTP 403)", None])
    slept = []

    def run(argv):
        e = next(errors)
        if e:
            raise github.GHError(e)
        return "{}"

    monkeypatch.setattr(github, "_run", run)
    monkeypatch.setattr(github.time, "sleep", slept.append)
    assert github.gh_get("x") == {} and slept == [github.RETRY_WAIT]
    monkeypatch.setattr(github, "_run", lambda argv: (_ for _ in ()).throw(github.GHError("gh: Not Found (HTTP 404)")))
    with pytest.raises(github.NotFound):
        github.gh_get("x")
