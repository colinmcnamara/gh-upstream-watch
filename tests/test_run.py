"""Whole passes over recorded multi-page fixtures: golden alerts, and fault injection proving that a
failed or truncated check never advances saved state."""
import json
import os
import re
import subprocess
import sys

import pytest
from conftest import FIXTURES, ROOT

from gh_upstream_watch import cli, notify, schedule

GOLDEN = (FIXTURES / "demo" / "run2.golden").read_text().splitlines()


@pytest.fixture
def watch(fake, tmp_path, capsys):
    state_path = tmp_path / "state.json"

    def run(fixture, *extra, responses=None):
        if fixture:
            fake.load(fixture)
        if responses:
            fake.responses.update(responses)
        code = cli.main(["--state", str(state_path), "--repos", "acme/widgets", "--packs-dir", str(FIXTURES / "packs"),
                         "--notify", "none", *extra])
        out = capsys.readouterr()
        return code, [re.sub(r"^\[[^\]]+\] ", "", line) for line in out.out.splitlines()], out.err

    run.state = lambda: json.loads(state_path.read_text())
    run.path = state_path
    return run


def test_golden_two_runs(watch, fake):
    code, out, err = watch("run1")
    assert (code, out) == (0, []) and "seed run: watching 4 item(s)" in err
    code, out, _ = watch("run2")
    assert code == 0
    assert out == GOLDEN
    assert all(argv[1:4] == ["api", "--method", "GET"] for argv in fake.calls), "read-only by construction"
    assert any(a[4].endswith("/390/comments") and "page=2" in a for a in fake.calls), "comment 101 is on page 2"
    code, out, _ = watch("run2")
    assert (code, out) == (0, []), "nothing changed: silence"


def test_disappearing_search_result_stays_watched(watch):
    watch("run1")
    watch("run2")
    assert "acme/widgets#392" in watch.state()["items"], "open, but gone from search: still watched"


def test_failed_page_does_not_advance_state(watch):
    watch("run1")
    before = watch.state()["items"]["acme/widgets#390"]
    broken = {"repos/acme/widgets/issues/390/comments?page=2&per_page=100": {"__error__": "HTTP 502"}}
    code, out, err = watch("run2", responses=broken)
    assert code == 1 and "acme/widgets#390: unknown this run" in err
    assert not [line for line in out if "#390" in line], "no partial diff for the unknown item"
    assert watch.state()["items"]["acme/widgets#390"] == before, "saved state not advanced"
    assert [line for line in out if "#392" in line], "one failed item does not lose the others"
    code, out, _ = watch("run2")
    assert [line for line in out if "#390" in line] == [line for line in GOLDEN if "#390" in line], "caught up next run"


def test_incomplete_search_keeps_every_item(watch):
    watch("run1")
    q = "search/issues?page=1&per_page=100&q=repo:acme/widgets involves:octocat is:closed updated:>=DATE"
    code, out, err = watch("run2", responses={q: {"total_count": 0, "incomplete_results": True, "items": []}})
    assert code == 1 and "incomplete_results" in err
    assert watch.state()["items"]["acme/widgets#388"]["state"] == "closed", "kept, and not advanced"
    code, out, _ = watch("run2")
    assert [line for line in out if "#388" in line and "REOPENED" in line], "the reopen alerts on the next good run"


def test_truncated_search_keeps_every_item(watch):
    """Item 2: a short page while total_count says more exist is incomplete, not the full list."""
    watch("run1")
    q = "search/issues?page=1&per_page=100&q=repo:acme/widgets involves:octocat is:closed updated:>=DATE"
    code, _, err = watch("run2", responses={q: {"total_count": 3, "incomplete_results": False, "items": []}})
    assert code == 1 and "got 0 of 3" in err
    assert "acme/widgets#388" in watch.state()["items"]


def test_closed_baseline_outlives_the_search_window(watch, fake):
    """Item 11: closed and out of search for weeks, then reopened: still REOPENED, not a quiet new item."""
    watch("run1")
    q = "search/issues?page=1&per_page=100&q=repo:acme/widgets involves:octocat is:closed updated:>=DATE"
    watch("run1", responses={q: {"total_count": 0, "incomplete_results": False, "items": []}})
    assert watch.state()["items"]["acme/widgets#388"]["state"] == "closed", "baseline kept though no longer searched"
    _, out, _ = watch("run2")
    assert [line for line in out if "REOPENED" in line]


def test_hook_failure_keeps_the_item_unknown(watch, tmp_path):
    """Item 3: a failing hook means the item was not fully checked; its fingerprint must not advance."""
    hook = tmp_path / "hook"
    hook.write_text(f"#!{sys.executable}\nimport sys; sys.exit(1)\n")
    hook.chmod(0o755)
    watch("run1")
    before = watch.state()["items"]["acme/widgets#390"]
    code, out, err = watch("run2", "--hook", str(hook))
    assert code == 1 and "--hook failed" in err and not [line for line in out if "#390" in line]
    assert watch.state()["items"]["acme/widgets#390"] == before
    _, out, _ = watch("run2")
    assert [line for line in out if "ACCEPTED" in line]


def test_failed_destination_stays_in_the_outbox(watch, monkeypatch):
    """Item 4: stdout counts as delivered; a failed desktop notifier is retried next run, alone."""
    watch("run1")
    monkeypatch.setenv("GH_UPSTREAM_WATCH_NOTIFY", "/no/such/notifier")
    code, out, err = watch("run2", "--notify", "command")
    assert out == GOLDEN and "will retry next run" in err
    assert {tuple(e["pending"]) for e in watch.state()["outbox"]} == {("desktop",)}
    monkeypatch.setenv("GH_UPSTREAM_WATCH_NOTIFY", "true")
    code, out, err = watch("run2", "--notify", "command")
    assert out == [] and watch.state()["outbox"] == [], "retried without re-printing to stdout"


def test_missing_gh_and_missing_repos_explain_the_next_step(tmp_path, monkeypatch, capsys):
    """Item 14."""
    monkeypatch.setenv("GH_UPSTREAM_WATCH_GH", str(tmp_path / "no-gh"))
    assert cli.main(["--state", str(tmp_path / "s.json"), "--repos", "acme/widgets"]) == 1
    assert "https://cli.github.com" in capsys.readouterr().err
    assert cli.main(["--state", str(tmp_path / "s.json")]) == 2
    assert "init --repos" in capsys.readouterr().err
    assert cli.main(["--state", str(tmp_path / "s.json"), "--repos", "owner/repo"]) == 2


def test_each_source_seeds_on_its_own(watch):
    """A failing source holds back only its own first-sight alerts; items with a baseline still alert."""
    notes = "notifications?page=1&participating=true&per_page=50&since=SINCE"
    code, _, err = watch("run1", responses={notes: {"__error__": "HTTP 500"}})
    assert code == 1 and watch.state()["seeded"] is False and "Still seeding: notifications" in err
    code, out, err = watch("run2")
    asks = [line for line in GOLDEN if "requested your review" in line]
    assert asks and out == [line for line in GOLDEN if line not in asks], "no flood from the late source, no loss elsewhere"
    assert "seed run" in err and watch.state()["seeded"] is True


def test_crash_between_outbox_write_and_notify_redelivers(watch, monkeypatch):
    watch("run1")
    real = notify.deliver

    def crash(*a, **k):
        raise KeyboardInterrupt  # the process dies before the first alert is shown

    monkeypatch.setattr(notify, "deliver", crash)
    with pytest.raises(KeyboardInterrupt):
        watch("run2")
    assert len(watch.state()["outbox"]) == len(GOLDEN) and watch.state()["outbox"][0]["pending"] == ["stdout"]
    monkeypatch.setattr(notify, "deliver", real)
    code, out, err = watch("run2")
    assert code == 0 and out == GOLDEN and "re-delivering" in err
    assert watch.state()["outbox"] == []


def test_corrupt_state_reseeds_quietly(watch):
    watch("run1")
    watch.path.write_text('{"schema": 1, "items": {"acme/wid')
    code, out, err = watch("run2")
    assert out == [] and "re-seeding quietly" in err


def test_dry_run_saves_nothing(watch):
    watch("run1")
    before = watch.path.read_text()
    code, out, _ = watch("run2", "--dry-run")
    assert out == GOLDEN and watch.path.read_text() == before


def test_locked_state_skips_the_run(watch):
    from gh_upstream_watch import state
    with state.lock(str(watch.path)):
        code, _, err = watch("run1")
    assert code == 3 and "locked" in err


def test_json_output(watch):
    watch("run1")
    _, out, _ = watch("run2", "--json")
    alerts = [json.loads(line) for line in out]
    assert {"time", "kind", "key", "title", "message", "url"} <= set(alerts[0])
    assert [a["kind"] for a in alerts][:3] == ["reopened", "gate", "competing_pr"]


def test_malformed_item_does_not_lose_the_run(watch):
    watch("run1")
    code, out, err = watch("run2", responses={"repos/acme/widgets/issues/388": {"unexpected": True}})
    assert code == 1 and "acme/widgets#388: unknown this run" in err
    assert len(out) == len(GOLDEN) - 1 and watch.state()["outbox"] == []


def test_migrated_v0_state_does_not_reseed(watch, tmp_path):
    v0 = tmp_path / "v0.json"
    v0.write_text((FIXTURES / "state_v0.json").read_text())
    assert cli.main(["migrate", "--from", str(v0), "--state", str(watch.path), "--claim-repo", "acme/widgets"]) == 0
    code, out, err = watch("run2")
    assert "seed run" not in err
    assert "acme/widgets#390 Widget spins forever on an empty config: ACCEPTED by @maint: comment /assign now " \
           "(https://github.com/acme/widgets/issues/390)" in out
    assert not [line for line in out if "Flaky test" in line], "a notification seen by v0 stays seen"


def test_gh_extension_shim_end_to_end(tmp_path):
    """The real CLI through the shim, with tests/fake_gh.py as the gh binary: the README demo."""
    env = dict(os.environ, GH_UPSTREAM_WATCH_GH=str(ROOT / "tests" / "fake_gh.py"), XDG_CONFIG_HOME=str(tmp_path))
    args = [sys.executable, str(ROOT / "gh-upstream-watch"), "--state", str(tmp_path / "s.json"), "--repos", "acme/widgets",
            "--packs-dir", str(FIXTURES / "packs"), "--notify", "none"]
    for run in ("run1", "run2"):
        env["FAKE_GH_FIXTURE"] = str(FIXTURES / "demo" / f"{run}.json")
        p = subprocess.run(args, env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert [re.sub(r"^\[[^\]]+\] ", "", line) for line in p.stdout.splitlines()] == GOLDEN


# --- red-team round 2 (v0.1.1) -----------------------------------------------------------------

GONE = {"__error__": "gh: Not Found (HTTP 404)"}


def test_a_deleted_item_alerts_once_and_the_run_stays_complete(watch):
    watch("run1")
    code, out, _ = watch("run1", responses={"repos/acme/widgets/issues/395": GONE})
    assert code == 0 and [line for line in out if "GONE" in line] == [
        "acme/widgets#395 [Community] Weekly sync thread: GONE: deleted, moved, or no longer visible to you "
        "(https://github.com/acme/widgets/issues/395)"]
    assert "acme/widgets#395" not in watch.state()["items"]
    code, out, _ = watch("run1", responses={"repos/acme/widgets/issues/395": GONE})
    assert (code, out) == (0, []), "said once; still complete, so pruning keeps running"


def test_an_extra_that_does_not_exist_does_not_block_seeding(watch):
    code, _, err = watch("run1", "--extra", "acme/widgets#99999")
    assert code == 0 and "acme/widgets#99999: not found" in err and watch.state()["seeded"] is True


def test_a_new_item_whose_first_fetch_fails_is_retried(watch):
    code, _, _ = watch("run1", responses={"repos/acme/widgets/issues/388": {"__error__": "HTTP 502"}})
    assert code == 1 and "acme/widgets#388" in watch.state()["retry"]
    assert "acme/widgets#388" not in watch.state()["items"]
    watch("run1")
    assert watch.state()["retry"] == {} and "acme/widgets#388" in watch.state()["items"]


@pytest.mark.parametrize("bad", ['{"schema": 1, "items": null}', '{"schema": 1, "items": {"a/b#1": []}}',
                                 '{"schema": 1, "outbox": [1]}', '{"schema": 1, "outbox": [{}]}', '{"schema": "1"}', '{"schema": 1, "outbox": {}}',
                                 '{"schema": 1, "notifications": {"seen": []}}'])
def test_valid_json_in_the_wrong_shape_is_quarantined(watch, bad):
    watch.path.write_text(bad)
    code, _, err = watch("run1")
    assert code == 0 and "re-seeding quietly" in err and watch.state()["seeded"] is True
    assert list(watch.path.parent.glob("state.json.corrupt-*"))


@pytest.mark.parametrize("cfg", [{"repos": "acme/widgets"}, {"repos": ["acme"]}, {"retention_days": "30"},
                                 {"bots": [1]}, {"slack": True}, {"recent_closed_days": 0}, {"packs_dirs": "/x"}])
def test_config_values_are_type_checked(tmp_path, capsys, cfg):
    p = tmp_path / "c.json"
    p.write_text(json.dumps(dict({"repos": ["acme/widgets"]}, **cfg)))
    assert cli.main(["--config", str(p), "--dry-run"]) == 2
    err = capsys.readouterr().err
    assert "is not a valid value" in err or "expected owner/repo" in err


def test_print_helpers_bound_the_interval(capsys):
    assert cli.main(["--print-cron", "--interval", "0"]) == 2
    assert cli.main(["--print-cron", "--interval", "-1"]) == 2
    assert cli.main(["--print-cron", "--interval", "90"]) == 2
    assert cli.main(["--print-cron", "--interval", "59"]) == 2, "*/59 fires at :00 and :59, not every 59 minutes"
    assert cli.main(["--print-cron", "--interval", "20"]) == 0
    assert "*/20" in capsys.readouterr().out


def gadgets(fake):
    """A second repo with one issue where a maintainer already said /accept."""
    r = "repos/acme/gadgets/issues/1"
    fake.responses.update({
        "search/issues?page=1&per_page=100&q=repo:acme/gadgets involves:octocat is:open":
            {"total_count": 1, "incomplete_results": False, "items": [{"number": 1}]},
        "search/issues?page=1&per_page=100&q=repo:acme/gadgets involves:octocat is:closed updated:>=DATE":
            {"total_count": 0, "incomplete_results": False, "items": []},
        r: {"title": "Old idea", "html_url": "https://github.com/acme/gadgets/issues/1", "state": "open",
            "labels": [], "assignees": [], "comments": 1},
        f"{r}/comments?page=1&per_page=100": [{"id": 1, "user": {"login": "maint"}, "body": "/accept",
                                               "author_association": "OWNER"}],
        f"{r}/timeline?page=1&per_page=100": []})


def test_a_repo_added_to_upgraded_state_still_seeds(watch, fake):
    watch("run1")
    st = watch.state()
    del st["seeded_sources"]  # what a 0.1.0 state file looks like
    watch.path.write_text(json.dumps(st))
    fake.load("run1")
    gadgets(fake)
    code, out, err = watch(None, "--repos", "acme/widgets", "acme/gadgets")
    assert code == 0 and out == [] and "seed run" in err, "the new repo's old /accept does not alert"
    assert "repo:acme/gadgets" in watch.state()["seeded_sources"] and watch.state()["seeded"] is True


def test_a_404_on_part_of_an_item_is_unknown_not_gone(watch):
    watch("run1")
    code, out, _ = watch("run1", responses={"repos/acme/widgets/pulls/392": GONE})
    assert code == 1 and not any("GONE" in line for line in out)
    assert "acme/widgets#392" in watch.state()["items"]


def test_plist_escapes_every_field(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "a&b"))
    assert cli.main(["--print-plist"]) == 0
    out = capsys.readouterr().out
    assert "a&amp;b/Library/Logs" in out and "a&b/" not in out


# --- EBI round 1 ---------------------------------------------------------------------------------

def test_dry_run_on_a_new_install_previews_what_it_holds_back(watch):
    code, out, err = watch("run1", "--dry-run")
    assert code == 0 and "seed run" in err
    assert out and all(line.startswith("(preview, not sent while seeding) ") for line in out)
    assert not watch.path.exists(), "dry-run saves nothing"


def clock(monkeypatch, minutes):
    """A clock that moves `minutes` ahead each time tick() is called: one scheduled run apart."""
    t = [1.79e9]
    monkeypatch.setattr(cli.time, "time", lambda: t[0])
    return lambda: t.__setitem__(0, t[0] + minutes * 60)


def failing_runs(watch, tick, n, responses=None):
    outs = []
    for _ in range(n):
        tick()
        outs.append(watch("run1" if responses else None, responses=responses)[1])
    return [(i, line) for i, out in enumerate(outs) for line in out if "not checkable" in line]


NOTES_404 = {"notifications?page=1&participating=true&per_page=50&since=SINCE":
             {"__error__": "gh: Not Found (HTTP 404) notifications"}}


@pytest.mark.parametrize("minutes", [15, 30])
def test_a_source_unknown_for_three_hours_escalates_once(watch, monkeypatch, minutes):
    tick = clock(monkeypatch, minutes)
    watch("run1")
    stuck = failing_runs(watch, tick, 200 // minutes + 2, NOTES_404)
    assert len(stuck) == 1 and stuck[0][0] == 180 // minutes, "three hours after the first failure, once"
    assert "gh auth refresh -s notifications" in stuck[0][1]
    watch("run1")
    assert "notifications" not in watch.state()["unknown_streak"], "recovery ends the streak"


def test_escalation_while_gh_is_signed_out(watch, fake, monkeypatch):
    def signed_out(argv):
        raise cli.github.GHError("user: gh exited 4: To get started with GitHub CLI, please run:  gh auth login")
    monkeypatch.setattr(cli.github, "_run", signed_out)
    stuck = failing_runs(watch, clock(monkeypatch, 30), 8)
    assert len(stuck) == 1 and "gh auth login" in stuck[0][1]


def test_an_old_config_keeps_the_run_count_rule(watch, monkeypatch, tmp_path):
    d = tmp_path / "config" / "gh-upstream-watch"
    d.mkdir(parents=True)
    (d / "config.json").write_text('{"escalate_after_runs": 6}')
    tick = clock(monkeypatch, 1)
    watch("run1")
    stuck = failing_runs(watch, tick, 7, NOTES_404)
    assert len(stuck) == 1 and stuck[0][0] == 5, "the 6th failing run in a row, once"
    (d / "config.json").write_text('{"escalate_after_runs": "6"}')
    assert watch(None)[0] == 2


def test_status_prints_the_fix(watch, capsys):
    watch("run1", "--extra", "acme/widgets#390",
          responses={"notifications?page=1&participating=true&per_page=50&since=SINCE":
                     {"__error__": "gh: Not Found (HTTP 404) notifications"}})
    cli.main(["status", "--state", str(watch.path), "--repos", "acme/widgets"])
    out = capsys.readouterr().out
    assert "the token cannot read notifications" in out and "notifications: 1 run(s) in a row" in out


def test_forget_drops_an_item(watch, capsys):
    watch("run1")
    assert cli.main(["forget", "acme/widgets#390", "acme/widgets#1", "--state", str(watch.path),
                     "--repos", "acme/widgets"]) == 0
    out = capsys.readouterr().out
    assert "acme/widgets#390: forgotten" in out and "acme/widgets#1: not in the state" in out
    assert "acme/widgets#390" not in watch.state()["items"]
    assert cli.main(["forget", "--state", str(watch.path), "--repos", "acme/widgets"]) == 2


def test_explain_shows_what_a_run_would_alert(watch, fake, capsys):
    watch("run1")
    fake.load("run2")
    assert cli.main(["explain", "acme/widgets#390", "--state", str(watch.path), "--repos", "acme/widgets",
                     "--packs-dir", str(FIXTURES / "packs")]) == 0
    out = capsys.readouterr().out
    assert "packs generic, acme" in out and "would alert: [gate] ACCEPTED by @maint: comment /assign now" in out
    assert cli.main(["explain", "nonsense", "--repos", "acme/widgets"]) == 2


@pytest.mark.parametrize("platform", ["darwin", "linux", "other"])
def test_init_schedule_installs_the_scheduler(tmp_path, monkeypatch, capsys, platform):
    calls = []
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(schedule.sys, "platform", platform)
    monkeypatch.setattr(schedule.shutil, "which", lambda t: "/bin/systemctl" if platform == "linux" and t == "systemctl" else None)
    monkeypatch.setattr(schedule, "install", lambda a, run=None, _real=schedule.install: _real(a, run=lambda argv, **k: calls.append(argv)))
    assert cli.main(["init", "--repos", "acme/widgets", "--login", "octocat", "--schedule"]) == 0
    out = capsys.readouterr().out
    if platform == "darwin":
        plist = tmp_path / schedule.PLIST
        assert plist.exists() and "<string>--once</string>" in plist.read_text()
        assert calls[-1][:2] == ["launchctl", "bootstrap"] and calls[-1][3] == str(plist)
    elif platform == "linux":
        d = tmp_path / "config" / "systemd" / "user"
        assert (d / "gh-upstream-watch.timer").exists() and calls[-1][-1] == "gh-upstream-watch.timer"
    else:
        assert calls == [] and "crontab -e" in out and "*/15 * * * *" in out
    assert cli.main(["init", "--schedule"]) == 0, "with a config already there, --schedule just schedules"
    assert cli.main(["init", "--schedule", "--force"]) == 2, "a new config needs --repos to schedule"


# --- EBI round 3 ---------------------------------------------------------------------------------

def test_json_alerts_carry_action_ts_and_a_version(watch):
    watch("run1")
    code, out, _ = watch("run2", "--json")
    alerts = [json.loads(line) for line in out]
    assert code == 0 and all(a["v"] == 1 and re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$", a["ts"]) for a in alerts)
    flags = [a["action"] for a in alerts]
    assert flags == sorted(flags, reverse=True), "everything that needs you comes first"
    assert {a["kind"] for a in alerts if not a["action"]} == {"review"}


def test_many_desktop_alerts_become_one_banner(watch, monkeypatch):
    shown = []
    monkeypatch.setattr(notify.subprocess, "run", lambda argv, **k: shown.append(argv))
    monkeypatch.setattr(notify.shutil, "which", lambda t: "/usr/bin/notify-send" if t == "notify-send" else None)
    watch("run1")
    code, out, _ = watch("run2", "--notify", "notify-send")
    assert code == 0 and len(out) == len(GOLDEN), "stdout still gets every alert"
    assert len(shown) == 1 and "6 alerts, 5 need action" in shown[0][-2]
    assert "ACCEPTED" in shown[0][-1] or "REOPENED" in shown[0][-1]
    assert watch.state()["outbox"] == []


def test_a_few_desktop_alerts_stay_separate(watch, monkeypatch):
    shown = []
    monkeypatch.setattr(notify.subprocess, "run", lambda argv, **k: shown.append(argv))
    monkeypatch.setattr(notify, "BATCH", 10)
    watch("run1")
    watch("run2", "--notify", "notify-send")
    assert len(shown) == len(GOLDEN)


@pytest.mark.parametrize("platform,rc,expect", [("darwin", 0, "launchd, loaded"), ("darwin", 113, "NOT loaded"),
                                                ("linux", 0, "systemd timer, active")])
def test_status_reports_the_scheduler(tmp_path, monkeypatch, platform, rc, expect):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(schedule.sys, "platform", platform)
    monkeypatch.setattr(schedule.shutil, "which", lambda t: "/bin/systemctl" if platform == "linux" else None)
    (tmp_path / schedule.PLIST).parent.mkdir(parents=True)
    (tmp_path / schedule.PLIST).write_text("<plist/>")

    class R:
        returncode, stdout = rc, "active\n"
    assert expect in schedule.describe(run=lambda argv, **k: R())


def test_ago():
    assert cli.ago(100, now=130) == "30 s ago" and cli.ago(0, now=900) == "15 min ago"
    assert cli.ago(0, now=3 * 3600) == "3 h ago" and cli.ago(0, now=3 * 86400) == "3 days ago"


# --- review of 0.2.0 -------------------------------------------------------------------------------

def test_a_command_backend_gets_every_alert_not_a_summary(watch, monkeypatch, tmp_path):
    got = tmp_path / "got.jsonl"
    monkeypatch.setenv("GH_UPSTREAM_WATCH_NOTIFY", f"sh -c 'cat >> {got}; echo >> {got}'")
    watch("run1")
    watch("run2", "--notify", "command")
    kinds = [json.loads(line)["kind"] for line in got.read_text().splitlines() if line.strip()]
    assert len(kinds) == len(GOLDEN) and "summary" not in kinds


def test_a_failed_banner_retries_as_a_banner_not_a_burst(watch, monkeypatch):
    shown = []

    def flaky(argv, **k):
        shown.append(argv)
        raise notify.subprocess.CalledProcessError(1, argv)
    monkeypatch.setattr(notify.subprocess, "run", flaky)
    watch("run1")
    watch("run2", "--notify", "notify-send")
    assert len(shown) == 1, "one banner attempt, no fallback to six pop-ups"
    assert all(e["pending"] == ["desktop"] for e in watch.state()["outbox"]) and len(watch.state()["outbox"]) == 6
    shown.clear()
    monkeypatch.setattr(notify.subprocess, "run", lambda argv, **k: shown.append(argv))
    watch("run2", "--notify", "notify-send")
    assert len(shown) == 1 and watch.state()["outbox"] == []


def test_a_failing_destination_gets_five_runs(watch, monkeypatch):
    monkeypatch.setenv("GH_UPSTREAM_WATCH_NOTIFY", "/no/such/notifier")
    watch("run1")
    sizes = []
    for fixture in ("run2", "run2", "run2", "run2", "run2"):
        watch(fixture, "--notify", "command")
        sizes.append(len(watch.state()["outbox"]))
    assert sizes == [6, 6, 6, 6, 0], sizes


def test_alerts_left_by_an_older_version_get_the_v1_fields(watch, capsys):
    watch("run1")
    st = watch.state()
    st["outbox"] = [{"alert": {"time": "2026-09-30 10:00", "kind": "gate", "key": "a/b#1", "title": "a/b#1 x",
                               "message": "m", "url": ""}, "pending": ["stdout"]}]
    watch.path.write_text(json.dumps(st))
    _, out, _ = watch("run1", "--json")
    old = json.loads(out[0])
    assert old["v"] == 1 and old["action"] is True and old["ts"].startswith("2026-09-30T10:00:00")


def test_status_counts_undelivered_and_slack_failures_as_problems(watch, capsys):
    watch("run1")
    st = watch.state()
    st["outbox"] = [{"alert": {"title": "t"}, "pending": ["webhook"], "attempts": 1}]
    st["slack"] = {"failures": 3, "last_error": "invalid_auth"}
    watch.path.write_text(json.dumps(st))
    cli.main(["status", "--state", str(watch.path), "--repos", "acme/widgets"])
    out = capsys.readouterr().out
    assert "problems: none" not in out and "undelivered" in out and "invalid_auth" in out


def test_changes_requested_needs_you():
    from gh_upstream_watch import core, packs
    rules = packs.for_repo(packs.load(), "acme/widgets")
    base = {"title": "t", "url": "https://github.com/acme/widgets/pull/1", "state": "open", "labels": [],
            "assignees": [], "xrefs": [], "gates": {}, "human_comments": 0, "pr": True}
    got = core.changes(dict(base, reviews=[]), dict(base, reviews=[[9, "maint", "CHANGES_REQUESTED"]]), "octocat", rules)
    assert got == [("changes_requested", "CHANGES REQUESTED by @maint", None)] and "changes_requested" in core.ACTION_KINDS


def test_status_does_not_push_cron_users_to_systemd(monkeypatch):
    monkeypatch.setattr(schedule.sys, "platform", "linux")
    monkeypatch.setattr(schedule.shutil, "which", lambda t: "/bin/systemctl")

    class R:
        returncode, stdout = 1, "inactive\n"
    assert "crontab -l" in schedule.describe(run=lambda argv, **k: R())


def test_the_quiet_run_really_sends_nothing(watch, monkeypatch, tmp_path):
    """The printed absorb command must not reach a configured webhook either."""
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"repos": ["acme/widgets"], "webhook": "https://hooks.example/x"}))
    sent = []
    monkeypatch.setattr(notify, "send_one", lambda d, *a, **k: sent.append(d) or True)
    watch("run1", "--config", str(cfg), "--webhook", "", "--notify", "none")
    watch("run2", "--config", str(cfg), "--webhook", "", "--notify", "none")
    assert "webhook" not in sent and "stdout" in sent


def test_a_notification_is_never_held_with_a_first_sight_alert(watch, fake):
    """A new --extra item seeds quietly; the notification about it (an already-seeded source) still arrives."""
    watch("run1")
    fake.load("run2")
    r = "repos/acme/widgets/issues/777"
    fake.responses.update({
        r: {"number": 777, "title": "New thing", "html_url": "https://github.com/acme/widgets/issues/777", "state": "open",
            "comments": 1, "labels": [], "assignees": []},
        f"{r}/comments?page=1&per_page=100": [{"id": 1, "user": {"login": "maint"}, "body": "/accept", "author_association": "OWNER"}],
        f"{r}/timeline?page=1&per_page=100": [],
        "notifications?page=1&participating=true&per_page=50&since=SINCE": [
            {"id": "n777", "reason": "assign", "updated_at": "2026-10-03T00:00:00Z", "repository": {"full_name": "acme/widgets"},
             "subject": {"title": "New thing", "url": "https://api.github.com/repos/acme/widgets/issues/777"}}]})
    code, out, err = watch(None, "--extra", "acme/widgets#777")
    assert any("acme/widgets#777" in line and "someone assigned you" in line for line in out), out
    assert not any("acme/widgets#777" in line and "ACCEPTED" in line for line in out), \
        "the first-sight gate on #777 is still held while the extra seeds"
