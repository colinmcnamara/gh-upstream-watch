import inspect
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
if not os.environ.get("GH_UPSTREAM_WATCH_TEST_INSTALLED"):  # CI also tests the built wheel, not src/
    sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import fake_gh  # noqa: E402

from gh_upstream_watch import github  # noqa: E402

# Programs that reach the developer's real machine or account. A test may run a fake by one of
# these names only from its tmp_path or this repo (fake_gh.py, hook scripts, the personal-data gate).
REAL_SYSTEM = {"gh", "launchctl", "systemctl", "crontab", "terminal-notifier", "notify-send", "osascript", "claude"}
# Tokens that leave this test run's isolation: gh would act as the developer with any of them.
CREDENTIALS = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")


class RealSystemCall(BaseException):
    """BaseException, so no `except Exception` in the package can swallow it: the test errors."""


def _inside(path, roots):
    """Whole path components: /x/tmp-other is not inside /x/tmp."""
    p = Path(os.path.realpath(path))
    return any(p == r or r in p.parents for r in roots)


SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
WRAPPERS = {"env", "nohup", "nice", "command", "exec", "xargs"}  # each runs a later word as a program


def _words(code):
    """Shell code as words, split at `;`, `&&`, `|`, `(` too: `true;/usr/bin/gh` is two programs."""
    lexer = shlex.shlex(code, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:  # an unbalanced quote in a script body: plain words are enough
        return re.split(r"[\s;&|()<>]+", code)


def _programs(args, shell=False):
    """The words a process runs as programs: argv[0], a wrapper's target (`env gh`), and every word
    of shell code (`shell=True`, `sh -c "..."`). Plain arguments are data and stay unchecked, so
    `echo gh` runs. Shell code is checked word by word: a command hidden in a variable is not seen."""
    argv = [os.fsdecode(args)] if isinstance(args, (str, bytes, os.PathLike)) else [os.fsdecode(a) for a in args]
    if shell:
        return _words(argv[0])
    out, i = [], 0
    while i < len(argv):
        out.append(argv[i])
        name = os.path.basename(argv[i])
        if name in SHELLS:
            k, flags = i + 1, ""
            while k < len(argv) and argv[k].startswith("-"):
                flags, k = flags + argv[k], k + 1
            if "c" in flags and k < len(argv):
                out += _words(argv[k])
            break
        if name not in WRAPPERS:
            break
        i += 1
        while i < len(argv) and (argv[i].startswith("-") or "=" in argv[i]):
            i += 1
    return out


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Never read or touch the developer's real config, state, home, scheduler, notifiers or gh.

    A test once replaced the real launchd agent; a draft of this guard missed bare names. Layers:
    subprocess.Popen (under run, call, check_output and asyncio) and os.system refuse a real tool by
    the program it runs (argv[0], a wrapper's target, the words of shell code), executable= or symlink
    target; tripwire stubs first on PATH catch a child process that has no guard and runs a bare name
    (exit 97); gh tokens are removed, so a real gh reached anyway is signed out.
    This catches accidents, it is not a sandbox. Not covered: a command hidden in a shell variable,
    a child process that runs a tool by absolute path or gets a PATH without the tripwire, and
    os.exec*, os.spawn* and os.posix_spawn by absolute path."""
    trip = tmp_path / ".tripwire"
    trip.mkdir()
    for name in REAL_SYSTEM:
        stub = trip / name
        stub.write_text(f'#!/bin/sh\necho "tripwire: a test ran the real {name}" >&2\nexit 97\n')
        stub.chmod(0o755)
    safe = [Path(os.path.realpath(tmp_path)), Path(os.path.realpath(ROOT))]
    trips = [Path(os.path.realpath(trip))]
    real_which, real_popen, real_system = shutil.which, subprocess.Popen, os.system

    def is_real(word, cwd=None):
        """A real-system tool: by name or symlink target, and not a fake in tmp_path or this repo.
        A bare name is always real: a test that wants a fake runs it by path."""
        if os.sep not in word:
            return word in REAL_SYSTEM
        full = word if os.path.isabs(word) else os.path.join(cwd or os.getcwd(), word)
        named = {os.path.basename(full), os.path.basename(os.path.realpath(full))} & REAL_SYSTEM
        return bool(named) and (_inside(full, trips) or not _inside(full, safe))

    def check(args, executable=None, cwd=None, via_shell=False):
        for word in _programs(args, via_shell) + ([os.fsdecode(executable)] if executable else []):
            if is_real(word, os.fsdecode(cwd) if cwd else None):
                raise RealSystemCall(f"a test tried to run the real {word!r} in {args!r}: use the fake fixture, pass a "
                                     "runner, or run a fake by its path (shell code is checked word by word)")

    signature = inspect.signature(real_popen.__init__)

    class GuardedPopen(real_popen):
        def __init__(self, *a, **k):
            # Bound like Popen binds them, so a positional cwd or executable counts too.
            b = signature.bind(self, *a, **k).arguments
            check(b["args"], b.get("executable"), b.get("cwd"), b.get("shell", False))
            super().__init__(*a, **k)

    def system(command):
        check(command, via_shell=True)
        return real_system(command)

    def which(cmd, *a, **k):
        """The real tools are absent in tests (Linux CI has systemctl, most machines have gh)."""
        found = real_which(cmd, *a, **k)
        return None if found and is_real(found) else found

    monkeypatch.setattr(subprocess, "Popen", GuardedPopen)
    monkeypatch.setattr(os, "system", system)
    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.setattr(time, "sleep", lambda s: None)  # retries and backoff never wait in tests
    monkeypatch.setenv("PATH", f"{trip}{os.pathsep}{os.environ.get('PATH', '')}")
    for var in CREDENTIALS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GH_CONFIG_DIR", str(tmp_path / "gh-config"))  # no saved gh login either
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    for var in ("GH_UPSTREAM_WATCH_CONFIG", "GH_UPSTREAM_WATCH_STATE", "GH_UPSTREAM_WATCH_REPOS",
                "GH_UPSTREAM_WATCH_NOTIFY", "GH_UPSTREAM_WATCH_GH"):
        monkeypatch.delenv(var, raising=False)


class Fake:
    """In-process gh: replays a responses dict and records every argv it was given."""

    def __init__(self):
        self.responses, self.calls = {}, []

    def load(self, name):
        self.responses = json.loads((FIXTURES / "demo" / f"{name}.json").read_text())["responses"]
        return self

    def run(self, argv, timeout=None):
        self.calls.append(argv)
        code, out, err = fake_gh.respond({"responses": self.responses}, argv[1:])
        if code:
            raise github.GHError(f"{argv[4]}: {err}")
        return out


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(github, "_run", f.run)
    return f
