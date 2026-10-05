"""Fixes from the final pre-release review."""
import http.server
import json
import os
import socketserver
import stat
import threading

import pytest
from conftest import FIXTURES
from test_extras import CFG, T, reply

from gh_upstream_watch import cli, notify, packs, slack, state

ALERT = {"time": "t", "title": "acme/widgets#1", "message": "m", "url": "https://github.com/acme/widgets/issues/1"}


class LocalServer(http.server.HTTPServer):
    """HTTPServer without its reverse-DNS lookup of the host (socket.getfqdn), which can hang for
    30 seconds on a Mac: found by the test timeout as a test that sometimes took 35s."""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


@pytest.fixture
def server():
    """Local webhook: /ok answers 200, /moved redirects to /ok, /bad answers 500."""
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            hits.append(("POST", self.path))
            self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/moved":
                self.send_response(302)
                self.send_header("Location", "/ok")
            else:
                self.send_response(200 if self.path == "/ok" else 500)
            self.end_headers()

        def do_GET(self):
            hits.append(("GET", self.path))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = LocalServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", hits
    srv.shutdown()
    srv.server_close()


def test_webhook_needs_2xx_and_never_follows_a_redirect(server, capsys):
    base, hits = server
    assert notify.send_one("webhook", ALERT, webhook=base + "/ok")
    assert not notify.send_one("webhook", ALERT, webhook=base + "/moved"), "a redirect is not delivery"
    assert not notify.send_one("webhook", ALERT, webhook=base + "/bad")
    assert hits == [("POST", "/ok"), ("POST", "/moved"), ("POST", "/bad")], "the redirect target was never fetched"


def test_control_characters_never_reach_the_terminal():
    line = notify.text(dict(ALERT, title="evil\x1b]8;;https://x\x07\nFAKE ALERT"))
    assert "\x1b" not in line and "\x07" not in line and "\n" not in line


def test_state_file_is_private(tmp_path):
    p = tmp_path / "s.json"
    old = os.umask(0o022)
    try:
        state.save(str(p), {"x": 1})
    finally:
        os.umask(old)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_explicit_empty_associations_trust_nobody():
    assert not packs.authorized({"authorized_by": {"associations": []}}, "a", "OWNER")
    assert packs.authorized({"authorized_by": {}}, "a", "OWNER"), "absent means the defaults"


def test_slack_mention_needs_the_exact_user_id():
    longer = reply(f"{T - 10:.6f}", kind="mention", evidence="<@UEXAMPLE1>")
    exact = reply(f"{T - 10:.6f}", kind="mention", evidence="<@UEXAMPLE>")
    assert not slack.proven(longer, "UEXAMPLE", []) and slack.proven(exact, "UEXAMPLE", [])


def test_a_slack_bug_never_blocks_github(fake, tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"slack": dict(CFG, enabled=True)}))
    monkeypatch.setattr(slack, "check", lambda *a, **k: {}["boom"])  # KeyError, not SlackError
    args = ["--config", str(cfg), "--state", str(tmp_path / "s.json"), "--repos", "acme/widgets",
            "--packs-dir", str(FIXTURES / "packs"), "--notify", "none"]
    fake.load("run1")
    assert cli.main(args) == 0
    st = json.loads((tmp_path / "s.json").read_text())
    assert st["seeded"] and st["slack"]["failures"] == 1


def test_bidi_and_markup_never_reach_a_notifier():
    spoof = dict(ALERT, title="acme‮devorppa", message='<a href="https://evil">Approved</a> &')
    assert "‮" not in notify.text(spoof)
    body = notify.desktop_argv("notify-send", spoof)[-1]
    assert "<a" not in body and "&lt;a" in body and "&amp;" in body
    assert "‮" not in " ".join(notify.desktop_argv("osascript", spoof))


def test_python_dash_m_and_an_escaped_plist(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src"
    # Under scripts/test-wheel.sh, sys.executable is the wheel's venv: test the installed package.
    env = dict(os.environ) if os.environ.get("GH_UPSTREAM_WATCH_TEST_INSTALLED") else {**os.environ, "PYTHONPATH": str(src)}
    r = subprocess.run([sys.executable, "-m", "gh_upstream_watch", "--version"], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and "gh-upstream-watch" in r.stdout
    cfg = tmp_path / "a&b.json"
    r = subprocess.run([sys.executable, "-m", "gh_upstream_watch", "--print-plist", "--config", str(cfg)],
                       capture_output=True, text=True, env=env)
    assert "a&amp;b.json" in r.stdout and "a&b.json" not in r.stdout


def test_quiet_issue_edit_that_adds_a_mention_counts():
    from gh_upstream_watch import core
    rules = packs.for_repo(packs.load([FIXTURES / "packs"]), "acme/widgets")
    base = {"title": "[Community] sync", "url": "https://github.com/acme/widgets/issues/1", "state": "open",
            "labels": [], "assignees": [], "xrefs": [], "gates": {}, "human_comments": 2, "human_ids": [3, 5]}
    old = dict(base, max_comment_id=5, mention_ids=[5], mentions_me=1)
    new = dict(base, max_comment_id=5, mention_ids=[3, 5], mentions_me=2)  # comment 3 edited to name you
    assert core.changes(old, new, "octocat", rules) == [("mentions", "1 comment naming you", None)]


@pytest.mark.parametrize("heading,found", [("## [0.1.1] - 2026-09-30", True), ("## [0.1.1]", True),
                                           ("## [0.1.10] - 2026-10-01", False), ("## [0x1y1] - x", False)])
def test_release_changelog_check_matches_the_exact_version(tmp_path, heading, found):
    """Runs the awk line from release.yml itself."""
    import re
    import subprocess
    from pathlib import Path
    wf = (Path(__file__).resolve().parents[1] / ".github/workflows/release.yml").read_text()
    line = next(x.strip() for x in wf.splitlines() if x.strip().startswith("awk -v h="))
    (tmp_path / "CHANGELOG.md").write_text(f"# Changelog\n\n{heading}\n\n- a note\n\n## [0.1.0] - old\n\n- old\n")
    r = subprocess.run(["bash", "-c", line], cwd=tmp_path, env={**os.environ, "v": "0.1.1", "RUNNER_TEMP": str(tmp_path)})
    assert r.returncode == 0
    assert ("- a note" in (tmp_path / "notes.md").read_text()) is found
    assert re.search(r"\$v", line), "the version comes from the workflow's own variable"
