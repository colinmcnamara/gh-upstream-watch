"""Whole passes over recorded multi-page fixtures: golden alerts, and fault injection proving that a
failed or truncated check never advances saved state."""
import json
import os
import re
import subprocess
import sys

import pytest
from conftest import FIXTURES, ROOT

from gh_upstream_watch import cli, notify

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


def test_seeded_only_after_a_complete_run(watch):
    notes = "notifications?page=1&participating=true&per_page=50&since=SINCE"
    code, _, _ = watch("run1", responses={notes: {"__error__": "HTTP 500"}})
    assert code == 1 and watch.state()["seeded"] is False
    code, out, err = watch("run2")
    assert (code, out) == (0, []) and "seed run" in err, "still the seed run: no flood from a half-seeded state"
    assert watch.state()["seeded"] is True


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
