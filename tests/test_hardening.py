"""Fixes from the final pre-release review."""
import http.server
import json
import os
import stat
import threading

import pytest
from conftest import FIXTURES
from test_extras import CFG, T, reply

from gh_upstream_watch import cli, notify, packs, slack, state

ALERT = {"time": "t", "title": "acme/widgets#1", "message": "m", "url": "https://github.com/acme/widgets/issues/1"}


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

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", hits
    srv.shutdown()


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
