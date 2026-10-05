"""Paths no other test reaches: config errors, status rows, migrate and explain exits, CI and
search pagination limits, notifier fallbacks, Slack runner failures, and run() passes that only
fire with a claim board, a hook, a repo's pace or a first merge."""
import json
import subprocess

import pytest
from conftest import FIXTURES
from test_extras import CFG, T, make_hook
from test_run import watch  # noqa: F401  (fixture)

from gh_upstream_watch import cli, core, github, notify, slack, state

R = "acme/widgets"
NOTES = "notifications?page=1&participating=true&per_page=50&since=SINCE"


def args(*argv):
    return cli.build_parser().parse_args(list(argv))


# cli.load_config

def test_invalid_json_config_exits_2_with_the_reason(tmp_path, capsys):
    cfg = tmp_path / "c.json"
    cfg.write_text("{not json")
    assert cli.main(["status", "--config", str(cfg)]) == 2
    assert "invalid JSON" in capsys.readouterr().err


def test_a_named_config_that_is_missing_exits_2(tmp_path, capsys):
    assert cli.main(["status", "--config", str(tmp_path / "nope.json")]) == 2
    assert "not found" in capsys.readouterr().err


def test_slack_flags_override_the_config(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"slack": {"enabled": True, "user": "UEXAMPLE1"}}))
    off = cli.load_config(args("--config", str(cfg), "--no-slack"))["slack"]
    assert off == {"enabled": False, "user": "UEXAMPLE1"}, "--no-slack turns it off and keeps the rest"
    cfg.write_text(json.dumps({"slack": {"enabled": False}}))
    assert cli.load_config(args("--config", str(cfg), "--slack"))["slack"]["enabled"] is True


# cli.status

def status_out(capsys, *extra):
    assert cli.main(["status", "--repos", R, *extra]) == 0
    return capsys.readouterr().out


def test_status_says_when_gh_is_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GH_UPSTREAM_WATCH_GH", str(tmp_path / "no-gh"))
    assert "gh            NOT FOUND" in status_out(capsys)


def test_status_says_when_the_login_lookup_fails(fake, monkeypatch, capsys):
    monkeypatch.setenv("GH_UPSTREAM_WATCH_GH", str(FIXTURES.parent / "fake_gh.py"))
    assert "login         FAILED" in status_out(capsys), "fake has no `user` reply: a 404"


def test_status_shows_a_broken_pack_instead_of_crashing(tmp_path, capsys):
    (tmp_path / "packs").mkdir()
    (tmp_path / "packs" / "bad.json").write_text("{")
    assert "packs         ERROR" in status_out(capsys, "--packs-dir", str(tmp_path / "packs"))


def test_status_before_any_run(tmp_path, capsys):
    assert "none yet; the first run seeds quietly" in status_out(capsys, "--state", str(tmp_path / "s.json"))


def test_status_lists_items_still_waiting_for_a_baseline(tmp_path, capsys):
    st = state.empty()
    st["retry"] = {f"{R}#7": 1.79e9}
    state.save(str(tmp_path / "s.json"), st)
    assert f"first baseline for {R}#7" in status_out(capsys, "--state", str(tmp_path / "s.json"))


# cli.migrate and cli.explain

def test_migrate_error_exits(tmp_path, capsys):
    dest, v0 = tmp_path / "s.json", tmp_path / "v0.json"
    v0.write_text(json.dumps({"schema": 2}))
    assert cli.main(["migrate", "--state", str(dest)]) == 2, "no --from"
    assert cli.main(["migrate", "--from", str(v0), "--state", str(dest)]) == 2, "already migrated"
    dest.write_text("{}")
    assert cli.main(["migrate", "--from", str(v0), "--state", str(dest)]) == 2, "dest exists, no --force"
    err = capsys.readouterr().err
    assert "needs --from" in err and "already schema 2" in err and "use --force" in err


def test_explain_says_unknown_when_the_live_fetch_fails(fake, capsys):
    fake.responses["user"] = {"login": "octocat"}
    assert cli.main(["explain", f"{R}#9", "--repos", R]) == 1
    assert "live: unknown" in capsys.readouterr().out


def test_explain_shows_the_saved_baseline_and_no_alert(watch, capsys):  # noqa: F811
    watch("run1")
    assert cli.main(["explain", f"{R}#392", "--state", str(watch.path), "--repos", R]) == 0
    out = capsys.readouterr().out
    assert "saved: last checked" in out and "would alert: nothing" in out


# core.ci_status and github.paginate / search_issues

def test_check_runs_short_of_total_count_are_incomplete(fake):
    sha = "abc123"
    fake.responses.update({
        f"repos/{R}/commits/{sha}/check-runs?page=1&per_page=100":
            {"total_count": 3, "check_runs": [{"name": "a", "status": "completed", "conclusion": "success"}]},
        f"repos/{R}/commits/{sha}/check-runs?page=2&per_page=100": {"total_count": 3, "check_runs": []}})
    with pytest.raises(github.Incomplete, match="1 of 3 check runs"):
        core.ci_status(R, sha)


def test_a_page_that_is_not_a_list_fails_the_list(fake):
    fake.responses[f"repos/{R}/issues/1/comments?page=1&per_page=100"] = {"message": "oops"}
    with pytest.raises(github.GHError, match="expected a list, got dict"):
        github.paginate(f"repos/{R}/issues/1/comments")


def test_search_that_runs_out_of_pages_is_incomplete(fake):
    q = f"repo:{R} is:open"
    fake.responses[f"search/issues?page=1&per_page=999&q={q}"] = {"total_count": 1000, "items": [{"number": i} for i in range(999)]}
    with pytest.raises(github.Incomplete, match="page cap"):
        github.search_issues(q, per_page=999)


# notify

@pytest.mark.parametrize("found,platform,backend", [("notify-send", "linux", "notify-send"), (None, "darwin", "osascript"),
                                                    (None, "linux", "none")])
def test_auto_backend_falls_back(monkeypatch, found, platform, backend):
    monkeypatch.setattr(notify.shutil, "which", lambda name: f"/opt/{name}" if name == found else None)
    monkeypatch.setattr(notify.sys, "platform", platform)
    assert notify.resolve("auto") == backend


# slack.check runner failures

def test_slack_without_claude_is_an_error():
    with pytest.raises(slack.SlackError, match="not found; set slack"):
        slack.check(dict(CFG, claude=None), {}, T)


@pytest.mark.parametrize("exc", [OSError("no such file"), subprocess.TimeoutExpired("claude", 600)])
def test_slack_runner_crash_is_an_error(exc):
    def boom(*a, **k):
        raise exc
    with pytest.raises(slack.SlackError):
        slack.check(CFG, {}, T, boom)


def test_slack_nonzero_exit_is_an_error():
    class P:
        returncode, stdout, stderr = 1, "", "not signed in"
    with pytest.raises(slack.SlackError, match="claude exited 1: not signed in"):
        slack.check(CFG, {}, T, lambda *a, **k: P())


# run()

CLAIM_PACK = {"id": "acme-claim", "repos": ["acme/*"], "claimable": {
    "search": "is:issue is:open in:title \"Workgroup Issues\"", "authorized_by": {"associations": ["MEMBER"]},
    "title": "^\\[Community\\] Workgroup Issues", "comment_marker": "<!-- wg:{group} -->", "section": "### Available issues",
    "row": "^\\|\\s*AVAILABLE\\s*\\|\\s*\\[#(?P<number>\\d+) (?P<title>[^\\]]+)\\]\\((?P<url>\\S+?)\\)",
    "alert": "CLAIMABLE in wg/{group}: /assign now"}}
BOARD_Q = f'search/issues?order=desc&page=1&per_page=100&q=repo:{R} is:issue is:open in:title "Workgroup Issues"&sort=created'
LEAD = {"user": {"login": "lead"}, "author_association": "MEMBER"}


def test_run_records_the_claim_board(watch, tmp_path):  # noqa: F811
    (tmp_path / "packs").mkdir()
    (tmp_path / "packs" / "claim.json").write_text(json.dumps(CLAIM_PACK))
    flags = ("--packs-dir", str(tmp_path / "packs"), "--claim-group", "data-plane")
    board = {BOARD_Q: {"total_count": 1, "items": [{"number": 983, "title": "[Community] Workgroup Issues", **LEAD}]},
             f"repos/{R}/issues/983/comments?page=1&per_page=100": [
                 {"body": "<!-- wg:data-plane -->\n### Available issues\n| AVAILABLE | [#613 Add mappings]"
                          "(https://github.com/acme/widgets/issues/613) |", **LEAD}]}
    code, _, _ = watch("run1", *flags, responses=board)
    assert code == 0 and watch.state()["claimable"]["board"][R] == [f"{R}#613"]
    code, _, err = watch("run1", *flags, responses={BOARD_Q: {"__error__": "HTTP 500"}})
    assert code == 1 and f"claim board {R}: unknown this run" in err


def test_run_hook_alerts_join_the_run(watch, tmp_path):  # noqa: F811
    watch("run1")
    hook = make_hook(tmp_path, "item = json.load(sys.stdin)\nprint(json.dumps({'message': 'HOOK ' + item['key']}))")
    _, out, _ = watch("run1", "--hook", hook)
    assert any(f"HOOK {R}#390" in line for line in out), "a hook's alert goes out with the run's own"


def test_run_records_pace_and_rewrites_a_first_merge(watch):  # noqa: F811
    mine = {f"repos/{R}/issues/392": {**json.loads((FIXTURES / "demo" / "run1.json").read_text())["responses"][f"repos/{R}/issues/392"],
                                      "user": {"login": "octocat"}},
            f"search/issues?order=desc&page=1&per_page=100&q=repo:{R} is:pr is:merged&sort=updated": {
                "total_count": 1, "items": [{"created_at": "2026-09-01T00:00:00Z", "closed_at": "2026-09-03T00:00:00Z"}]}}
    watch("run1", responses=mine)
    assert watch.state()["pace"][R]["days"] == 2.0, "your open PR's repo gets its merge pace"
    merged = {**mine, f"repos/{R}/pulls/392": {"merged": True},
              f"search/issues?page=1&per_page=2&q=repo:{R} is:pr is:merged author:octocat": {"total_count": 1, "items": [{"number": 392}]}}
    _, out, _ = watch("run1", responses=merged)
    assert any(f"FIRST MERGE in {R}: your PR is in" in line for line in out), out
