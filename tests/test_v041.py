"""0.4.1, from the docs fact-check: the README's exit codes and lock promise, made true in the code."""
import contextlib
import json

from conftest import FIXTURES

from gh_upstream_watch import cli, schedule, state

R = "acme/widgets"


def test_a_bad_hook_path_is_a_config_error(tmp_path, capsys):
    """Exit 2, like every other config error, not 1 (which means "something was unknown this run")."""
    base = ["--repos", R, "--state", str(tmp_path / "s.json"), "--notify", "none"]
    assert cli.main([*base, "--hook", "relative/hook"]) == 2
    assert "--hook must be an absolute path" in capsys.readouterr().err
    plain = tmp_path / "hook.txt"
    plain.write_text("not a program")
    assert cli.main([*base, "--hook", str(plain)]) == 2
    assert "--hook is not executable" in capsys.readouterr().err


def test_migrate_refuses_while_a_run_holds_the_lock(tmp_path, capsys):
    """migrate writes the state file under the same lock a run holds, so it never races a run."""
    v0 = tmp_path / "old.json"
    v0.write_text((FIXTURES / "state_v0.json").read_text())
    dest = tmp_path / "state.json"
    with state.lock(str(dest)):
        assert cli.main(["migrate", "--from", str(v0), "--state", str(dest), "--claim-repo", R]) == 3
    assert not dest.exists(), "nothing written while a run held the lock"
    assert "locked by another run" in capsys.readouterr().err
    assert cli.main(["migrate", "--from", str(v0), "--state", str(dest), "--claim-repo", R]) == 0
    assert dest.exists()


def test_interval_help_says_init_uses_it_too():
    action = next(a for a in cli.build_parser()._actions if "--interval" in a.option_strings)
    assert "init --schedule" in action.help


def test_migrate_rechecks_the_destination_inside_the_lock(tmp_path, monkeypatch):
    """A run may create the state file between the first check and the lock: never overwrite it."""
    v0 = tmp_path / "old.json"
    v0.write_text((FIXTURES / "state_v0.json").read_text())
    dest = tmp_path / "state.json"

    @contextlib.contextmanager
    def a_run_got_there_first(path):
        dest.write_text('{"schema": 1}')
        yield

    monkeypatch.setattr(cli.state, "lock", a_run_got_there_first)
    assert cli.main(["migrate", "--from", str(v0), "--state", str(dest), "--claim-repo", R]) == 2
    assert dest.read_text() == '{"schema": 1}'


def test_migrate_with_a_missing_or_broken_source_is_a_config_error(tmp_path, capsys):
    dest = tmp_path / "state.json"
    assert cli.main(["migrate", "--from", str(tmp_path / "nope.json"), "--state", str(dest)]) == 2
    (tmp_path / "bad.json").write_text("{not json")
    assert cli.main(["migrate", "--from", str(tmp_path / "bad.json"), "--state", str(dest)]) == 2
    err = capsys.readouterr().err
    assert "cannot read" in err and "Traceback" not in err


def test_a_state_file_from_a_newer_version_is_a_setup_error_and_is_kept(tmp_path, capsys):
    """Exit 2, with the file left where it is: it is not corrupt, this version is too old for it."""
    st = tmp_path / "state.json"
    st.write_text(json.dumps({"schema": 99}))
    assert cli.main(["inbox", "--state", str(st), "--repos", R]) == 2
    assert "Upgrade" in capsys.readouterr().err
    assert json.loads(st.read_text()) == {"schema": 99} and not list(tmp_path.glob("state.json.corrupt-*"))


def test_init_schedule_without_launchd_or_systemd_needs_a_cron_interval(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(schedule.sys, "platform", "other")
    monkeypatch.setattr(schedule.shutil, "which", lambda t: None)
    assert cli.main(["init", "--repos", R, "--login", "octocat", "--schedule", "--interval", "45"]) == 2
    assert "divides 60" in capsys.readouterr().err
