"""EBI round 5: an inbox of what waits on you, how long your own work has waited on them (with the
repo's pace), CI on your own PRs (including a fork run waiting for approval), and milestones."""
import json

import pytest
from conftest import FIXTURES

from gh_upstream_watch import cli, core, packs, state

ME = "octocat"
R = "acme/widgets"
RULES = packs.for_repo(packs.load([FIXTURES / "packs"]), R)
DAY = 86400
NOW = 1.79e9
SHA = "abc123"


def pr_responses(fake, author=ME, mergeable="clean", runs=(), checks=(), status="pending", statuses=0, merged=False):
    fake.responses.update({
        f"repos/{R}/issues/7": {"number": 7, "title": "Add retry", "html_url": f"https://github.com/{R}/pull/7",
                                "state": "open", "comments": 1, "labels": [], "assignees": [], "user": {"login": author},
                                "created_at": "2026-09-01T00:00:00Z", "body": "cc @octocat", "pull_request": {}},
        f"repos/{R}/issues/7/comments?page=1&per_page=100": [
            {"id": 5, "user": {"login": "maint"}, "author_association": "MEMBER", "body": "@octocat please rebase",
             "created_at": "2026-09-03T00:00:00Z"},
            {"id": 9, "user": {"login": ME}, "body": "done", "created_at": "2026-09-04T00:00:00Z"}],
        f"repos/{R}/issues/7/timeline?page=1&per_page=100": [],
        f"repos/{R}/pulls/7": {"merged": merged, "mergeable_state": mergeable, "head": {"sha": SHA}},
        f"repos/{R}/pulls/7/reviews?page=1&per_page=100": [
            {"id": 1, "user": {"login": "maint"}, "state": "CHANGES_REQUESTED", "author_association": "MEMBER",
             "submitted_at": "2026-09-02T00:00:00Z"},
            {"id": 2, "user": {"login": ME}, "state": "COMMENTED", "author_association": "CONTRIBUTOR",
             "submitted_at": "2026-09-05T00:00:00Z"}],
        f"repos/{R}/pulls/7/comments?page=1&per_page=100": [],
        f"repos/{R}/commits/{SHA}/check-runs?page=1&per_page=100": {"check_runs": list(checks), "total_count": len(checks)},
        f"repos/{R}/actions/runs?head_sha={SHA}&per_page=100": {"workflow_runs": list(runs)},
        f"repos/{R}/commits/{SHA}/status": {"state": status, "total_count": statuses},
    })


def test_own_pr_fingerprint_records_who_when_ci_and_conflict(fake):
    pr_responses(fake, mergeable="dirty", checks=[{"name": "lint", "status": "completed", "conclusion": "failure"}])
    fake.responses[f"repos/{R}/issues/7/comments?page=1&per_page=100"][0]["updated_at"] = "2026-09-06T00:00:00Z"  # edited
    fp = core.fingerprint(R, 7, ME, RULES)
    assert fp["author"] == ME and fp["created_at"] == "2026-09-01T00:00:00Z"
    assert fp["my_at"] == "2026-09-05T00:00:00Z", "your reply on a review thread is a reply"
    assert fp["mention_at"] == "2026-09-03T00:00:00Z", "a re-edited roll call is not a new ask"
    assert fp["their_at"] == "2026-09-03T00:00:00Z", "the latest maintainer comment or review"
    assert fp["names_me"] is False, "your own body naming you is not news"
    assert fp["conflict"] is True
    assert fp["ci"] == {"state": "failure", "failing": ["lint"], "sha": SHA}
    assert fp["changes_requested"] == {"by": ["maint"], "at": "2026-09-02T00:00:00Z"}
    assert [r[1] for r in fp["reviews"]] == ["maint"], "your reply on a review thread is not a review of your PR"


def test_someone_elses_pr_costs_no_ci_calls(fake):
    pr_responses(fake, author="monalisa")
    fp = core.fingerprint(R, 7, ME, RULES)
    assert "ci" not in fp and not any("check-runs" in " ".join(c) for c in fake.calls)
    assert fp["names_me"] is True, "their body says cc @octocat"


@pytest.mark.parametrize("runs,checks,status,statuses,want", [
    ([{"name": "CI", "conclusion": "action_required", "status": "completed"}], [], "pending", 0, "approval"),
    ([], [{"name": "a", "status": "in_progress", "conclusion": None}], "pending", 0, "pending"),
    ([], [{"name": "a", "status": "completed", "conclusion": "success"}], "pending", 0, "success"),
    ([], [{"name": "a", "status": "completed", "conclusion": "cancelled"}], "pending", 0, "pending"),
    ([], [], "failure", 2, "failure"),  # buildkite and friends post commit statuses, not check runs
    ([], [], "pending", 1, "pending"),
    ([], [], "pending", 0, None),  # nothing ran at all
])
def test_ci_state(fake, runs, checks, status, statuses, want):
    pr_responses(fake, runs=runs, checks=checks, status=status, statuses=statuses)
    assert (core.ci_status(R, SHA) or {}).get("state") == want


def test_a_ci_lookup_that_fails_is_unknown_never_passed(fake):
    from gh_upstream_watch import github
    pr_responses(fake)
    del fake.responses[f"repos/{R}/actions/runs?head_sha={SHA}&per_page=100"]  # a 404: maybe a token without access
    with pytest.raises(github.GHError):
        core.ci_status(R, SHA)


def test_every_check_run_page_is_read(fake):
    from gh_upstream_watch import github
    ok = {"name": "a", "status": "completed", "conclusion": "success"}
    pr_responses(fake)
    fake.responses[f"repos/{R}/commits/{SHA}/check-runs?page=1&per_page=100"] = {"check_runs": [ok] * 100, "total_count": 101}
    fake.responses[f"repos/{R}/commits/{SHA}/check-runs?page=2&per_page=100"] = {
        "check_runs": [{"name": "late", "status": "completed", "conclusion": "failure"}], "total_count": 101}
    assert core.ci_status(R, SHA)["failing"] == ["late"]
    del fake.responses[f"repos/{R}/commits/{SHA}/check-runs?page=2&per_page=100"]
    with pytest.raises(github.GHError):
        core.ci_status(R, SHA)


base = dict(title="Add retry", url=f"https://github.com/{R}/pull/7", state="open", labels=[], assignees=[],
            human_comments=0, gates={}, author=ME, reviews=[], merged=False)


def kinds(old, new):
    return [(k, m) for k, m, _ in core.changes(old, new, ME, RULES)]


def test_ci_transitions_alert_and_a_first_reading_seeds():
    pend, red = {**base, "ci": {"state": "pending"}}, {**base, "ci": {"state": "failure", "failing": ["lint", "unit"]}}
    assert kinds(pend, red) == [("ci_failed", "CI FAILED: lint, unit")]
    assert kinds(red, {**base, "ci": {"state": "success"}}) == [("ci_passed", "CI passed")]
    assert kinds(pend, {**base, "ci": {"state": "approval"}})[0][0] == "ci_waiting"
    assert kinds(base, red) == [], "an upgrade from a state without CI seeds quietly"
    assert kinds(red, dict(red)) == []
    assert "ci_failed" in core.ACTION_KINDS and "ci_waiting" not in core.ACTION_KINDS, "a fork approval is theirs to do"


def test_a_new_merge_conflict_alerts_once():
    assert kinds({**base, "conflict": False}, {**base, "conflict": True}) == [("conflict", "MERGE CONFLICT: rebase or merge main")]
    assert kinds({**base, "conflict": True}, {**base, "conflict": True}) == []
    assert kinds(base, {**base, "conflict": True}) == [], "unknown before is not a transition"


def test_milestones():
    assert kinds(base, {**base, "merged": True}) == [("milestone", "MERGED: your PR is in")]
    theirs = {**base, "author": "lead"}
    assert kinds({**theirs, "names_me": True}, {**theirs, "names_me": True, "merged": True}) == \
        [("milestone", "MERGED: a PR by @lead that names you")], "a roster PR adding you, say"
    assert kinds(theirs, {**theirs, "merged": True}) == [("merged", "MERGED")]


def test_first_merge_in_a_repo(fake):
    q = f"search/issues?page=1&per_page=2&q=repo:{R} is:pr is:merged author:{ME}"
    fake.responses[q] = {"total_count": 1, "items": [{"number": 7}]}
    assert core.first_merge(R, ME, 7) is True
    assert core.first_merge(R, ME, 8) is False, "the index lags: the one it found is an earlier merge"
    fake.responses[q] = {"total_count": 1, "items": [{"number": 7}], "incomplete_results": True}
    assert core.first_merge(R, ME, 7) is False
    fake.responses[q] = {"total_count": 4, "items": [{"number": 7}, {"number": 3}]}
    assert core.first_merge(R, ME, 7) is False
    del fake.responses[q]
    assert core.first_merge(R, ME, 7) is False, "a failed lookup keeps the plain message"


def test_repo_pace_is_the_median_and_90th_percentile_days_to_merge(fake):
    fake.responses[f"search/issues?order=desc&page=1&per_page=100&q=repo:{R} is:pr is:merged&sort=updated"] = {
        "total_count": 3, "items": [{"created_at": "2026-09-01T00:00:00Z", "closed_at": f"2026-09-0{d}T00:00:00Z"}
                                     for d in (2, 4, 9)]}
    assert core.repo_pace(R) == {"days": 3.0, "p90": 8.0, "n": 3}


def write_state(tmp_path, items, pace=None, claim=None, board=None):
    st = state.empty()
    st.update(items=items, last_complete=NOW - 600, pace=pace or {}, seeded=True, login=ME)
    st["claimable"].update(seen=claim or {}, board=board or {})
    p = tmp_path / "s.json"
    state.save(str(p), st)
    return ["--state", str(p), "--repos", R]


def iso(days_ago):
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - days_ago * DAY))


def test_inbox_sorts_your_turn_from_theirs(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli.time, "time", lambda: NOW)
    item = dict(state="open", labels=[], assignees=[], gates={}, reviews=[], url=f"https://github.com/{R}/pull/1")
    items = {
        f"{R}#1": dict(item, title="Red CI", author=ME, pr=True, ci={"state": "failure", "failing": ["lint"]}, created_at=iso(3)),
        f"{R}#2": dict(item, title="Accepted issue", author="lead", gates={"accept": {"by": "maint", "done": False, "bot": False}}),
        f"{R}#3": dict(item, title="Asked a question", author="lead", mention_at=iso(2), my_at=iso(3)),
        f"{R}#4": dict(item, title="Answered", author="lead", mention_at=iso(2), my_at=iso(1)),
        f"{R}#5": dict(item, title="Fresh PR", author=ME, pr=True, created_at=iso(2), their_at=None),
        f"{R}#6": dict(item, title="Old PR", author=ME, pr=True, created_at=iso(20), my_at=iso(12), their_at=iso(15)),
        f"{R}#7": dict(item, title="Fixed after review", author=ME, pr=True, created_at=iso(9), my_at=iso(1),
                       changes_requested={"by": ["maint"], "at": iso(2)}),
        f"{R}#8": dict(item, title="Needs fixes", author=ME, pr=True, created_at=iso(9), my_at=iso(4),
                       changes_requested={"by": ["maint"], "at": iso(2)}),
        f"{R}#9": dict(item, title="Closed", author=ME, state="closed", ci={"state": "failure", "failing": ["x"]}),
        f"{R}#10": dict(item, title="Conflict", author=ME, pr=True, conflict=True, created_at=iso(1)),
        f"{R}#11": dict(item, title="My issue", author=ME, pr=False, created_at=iso(1.5)),
        f"{R}#12": dict(item, title="Draft", author=ME, pr=True, draft=True, created_at=iso(1)),
    }
    args = write_state(tmp_path, items, pace={R: {"days": 1.0, "p90": 5.0, "n": 40, "at": NOW}},
                       claim={f"{R}#77": NOW - DAY, f"{R}#78": NOW - DAY}, board={R: [f"{R}#77"]})
    assert cli.main(["inbox", *args]) == 0
    out = capsys.readouterr().out
    you, them = out.split("waiting on them")
    for n, why in ((1, "CI FAILED: lint"), (2, "ACCEPTED by @maint"), (3, "unanswered mention"),
                   (8, "changes requested by @maint"), (10, "merge conflict"), (12, "draft: mark it ready")):
        assert f"{R}#{n} " in you and why in you, (n, why)
    for n in (4, 5, 6, 7, 9):
        assert f"{R}#{n} " not in you, n
    assert f"{R}#5 " in them and "2 days" in them and "too early to nudge" in them
    assert f"{R}#6 " in them and "12 days" in them and "last maintainer touch 15 days ago" in them and "past that" in them
    assert f"{R}#7 " in them, "you answered the review: the ball is theirs"
    assert f"{R}#9 " not in them
    assert f"{R}#11 " in them and "1 day;" in them and "within" not in them.split(f"{R}#11 ")[1].split("\n")[0], \
        "an issue gets no merge pace"
    assert f"{R}#77" in out and "claimable" in out
    assert f"{R}#78" not in out, "seen once but no longer on the board: claimed or removed"


def test_inbox_json_and_an_empty_state(tmp_path, capsys):
    args = write_state(tmp_path, {f"{R}#1": dict(state="open", title="t", url="u", author=ME, pr=True, conflict=True)})
    assert cli.main(["inbox", "--json", *args]) == 0
    rows = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert rows[0]["section"] == "you" and rows[0]["key"] == f"{R}#1" and rows[0]["why"] == "merge conflict"
    assert cli.main(["inbox", "--state", str(tmp_path / "none.json"), "--repos", R]) == 0
    assert "no runs yet" in capsys.readouterr().out


def test_review_fixes_from_the_opus_red_team(fake):
    # 1: a first reading of "unknown" (None) then a conflict alerts; only a pre-0.3.0 state seeds.
    assert kinds({**base, "conflict": None}, {**base, "conflict": True})[0][0] == "conflict"
    # 2: red, a push, red again for another reason: news.
    a = {**base, "ci": {"state": "failure", "failing": ["lint"], "sha": "a"}}
    b = {**base, "ci": {"state": "failure", "failing": ["unit"], "sha": "b"}}
    assert kinds(a, b) == [("ci_failed", "CI FAILED: unit")] and kinds(a, dict(a)) == []
    # 3: a stale or action_required check run is not a pass.
    for conclusion, want in (("stale", "pending"), ("action_required", "approval"), ("neutral", "success")):
        pr_responses(fake, checks=[{"name": "x", "status": "completed", "conclusion": conclusion}])
        assert core.ci_status(R, SHA)["state"] == want, conclusion
    # 7: own PR closed (ci None) then reopened red: an alert, not a first reading.
    assert kinds({**base, "state": "closed", "ci": None}, {**base, "ci": {"state": "failure", "failing": ["x"]}})[-1][0] == "ci_failed"


def test_pace_is_nearest_rank_and_judged_on_age(fake, tmp_path, capsys, monkeypatch):
    fake.responses[f"search/issues?order=desc&page=1&per_page=100&q=repo:{R} is:pr is:merged&sort=updated"] = {
        "items": [{"created_at": "2026-09-01T00:00:00Z", "closed_at": f"2026-09-{d + 1:02d}T00:00:00Z"} for d in range(1, 11)]}
    assert core.repo_pace(R)["p90"] == 9.0, "the 9th of 10, not the maximum"
    monkeypatch.setattr(cli.time, "time", lambda: NOW)
    items = {f"{R}#1": dict(state="open", title="Old", url="u", author=ME, pr=True, created_at=iso(60), my_at=iso(1)),
             f"{R}#2": dict(state="open", title="Pre-0.3.0", url="u", author="lead", mention_ids=[5], my_last_id=0)}
    assert cli.main(["inbox", *write_state(tmp_path, items, pace={R: {"p90": 10.0, "at": NOW}})]) == 0
    out = capsys.readouterr().out
    assert "waiting 1 day" in out and "past that" in out, "your own comment does not reset the repo's clock"
    assert f"{R}#2" not in out, "no mention_at yet (a 0.2.3 state): do not guess the mention is unanswered"
