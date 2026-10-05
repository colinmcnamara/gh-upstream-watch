"""The autouse guard in conftest.py. Each probe here is harmless even if the guard breaks (read-only
`--version` and `print` of a label that does not exist), because a guard is never proved with the
real dangerous call: an unsafe probe of an earlier draft of this guard reloaded the developer's
launchd agent."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import CREDENTIALS, ROOT, RealSystemCall, _inside

# One real tool that exists on this machine, run only by probes the guard must refuse. Each probe's
# command is read-only even if the guard fails: `--version`, or `launchctl version`.
# Looked up at import, before the autouse guard hides the real tools.
REAL = next((p for p in map(shutil.which, ("launchctl", "systemctl", "gh")) if p), None)
needs_real = pytest.mark.skipif(REAL is None, reason="no real gh, launchctl or systemctl on this machine")


@pytest.mark.parametrize("argv", [["gh", "--version"], ["launchctl", "print", "gui/0/no.such.label"],
                                  ["systemctl", "--user", "is-active", "no-such.timer"], "gh"])
def test_real_system_tools_cannot_run(argv):
    with pytest.raises(RealSystemCall):
        subprocess.run(argv, capture_output=True)


def test_a_bare_name_is_not_resolved_against_the_repo(monkeypatch):
    monkeypatch.chdir(ROOT)  # the guard once allowed "gh" here, as if it were a file in the repo
    with pytest.raises(RealSystemCall):
        subprocess.run(["gh", "--version"], capture_output=True)


def test_fakes_inside_the_repo_or_tmp_still_run(tmp_path):
    fake = tmp_path / "gh"
    fake.write_text("#!/bin/sh\necho fake\n")
    fake.chmod(0o755)
    assert subprocess.run([str(fake)], capture_output=True, text=True).stdout == "fake\n"
    assert shutil.which(str(ROOT / "tests" / "fake_gh.py"))


def test_the_real_tools_look_absent_and_home_is_a_temp_dir(tmp_path):
    assert shutil.which("gh") is None and shutil.which("launchctl") is None
    assert os.environ["HOME"] == str(tmp_path)


@pytest.mark.parametrize("call", [
    lambda: subprocess.Popen(["gh", "--version"]),
    lambda: subprocess.check_output(["gh", "--version"]),
    lambda: subprocess.run(["sh", "-c", "gh --version"]),
    lambda: subprocess.run(["env", "gh", "--version"]),
    lambda: subprocess.run("FOO=1 gh --version", shell=True),  # noqa: S602 - the probe is that a shell is guarded
    lambda: subprocess.run([b"gh", b"--version"]),
    lambda: os.system("gh --version"),  # noqa: S605 - the probe is that os.system is guarded
], ids=["Popen", "check_output", "sh -c", "env", "shell=True", "bytes", "os.system"])
def test_every_way_of_starting_a_process_is_guarded(call):
    """Codex and Opus review: the first guard covered only subprocess.run and the first word."""
    with pytest.raises(RealSystemCall):
        call()


@needs_real
def test_a_renamed_symlink_or_executable_is_still_the_real_tool(tmp_path):
    link = tmp_path / "not-a-tool"
    link.symlink_to(REAL)
    with pytest.raises(RealSystemCall):
        subprocess.run([str(link), "version"], capture_output=True)
    with pytest.raises(RealSystemCall):
        subprocess.run(["anything", "version"], executable=REAL, capture_output=True)


def test_a_sibling_directory_is_not_inside_the_repo(tmp_path):
    sibling = ROOT.parent / (ROOT.name + "-sibling")
    assert not _inside(sibling / "gh", [ROOT]), "a path prefix is not containment"


def test_a_child_process_without_the_guard_hits_the_tripwire():
    """A child runs the package with no conftest: a bare `gh` there finds the tripwire stub first."""
    code = "import subprocess; print(subprocess.run(['gh', '--version'], capture_output=True).returncode)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout.strip()
    assert out == "97"


def test_no_github_credentials_reach_a_test():
    assert not any(os.environ.get(v) for v in CREDENTIALS)
    assert _inside(os.environ["GH_CONFIG_DIR"], [Path(os.path.realpath(os.environ["HOME"]))]), "no saved gh login either"


@needs_real
def test_a_positional_cwd_and_a_path_with_spaces_are_seen(tmp_path):
    """Codex round 2: cwd passed by position, and a path that word-splitting would break."""
    with pytest.raises(RealSystemCall):  # Popen(args, bufsize, executable, stdin, stdout, stderr, preexec_fn, close_fds, shell, cwd)
        subprocess.Popen(["./" + os.path.basename(REAL), "version"], -1, None, None, None, None, None, True, False,
                         os.path.dirname(REAL))
    link = tmp_path / "a tool with spaces"
    link.symlink_to(REAL)
    with pytest.raises(RealSystemCall):
        subprocess.run([str(link), "version"], capture_output=True)


def test_data_arguments_are_not_programs():
    """Codex round 2: `echo gh` prints a word, it does not run gh."""
    assert subprocess.run(["echo", "gh", "claude"], capture_output=True, text=True).stdout == "gh claude\n"
    assert subprocess.run(["env", "FOO=1", "echo", "--notify", "notify-send"], capture_output=True).returncode == 0


def test_a_wrapper_or_shell_flags_still_find_the_program():
    for argv in (["env", "-i", "FOO=1", "gh", "--version"], ["nohup", "gh", "--version"], ["bash", "-ec", "true; gh --version"]):
        with pytest.raises(RealSystemCall):
            subprocess.run(argv, capture_output=True)
