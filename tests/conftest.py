import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import fake_gh  # noqa: E402
from gh_upstream_watch import github  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Never read the developer's real config, packs or state."""
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
