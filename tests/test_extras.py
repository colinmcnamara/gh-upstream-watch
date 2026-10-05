"""Notifiers, --hook, the opt-in Slack source, pack validation, and config precedence."""
import json
import sys
from pathlib import Path

import pytest
from conftest import FIXTURES

from gh_upstream_watch import cli, hooks, notify, packs, slack

ALERT = {"time": "2026-01-06 09:00", "kind": "gate", "key": "acme/widgets#390", "title": "acme/widgets#390 Fix x",
         "message": "[Bug] ACCEPTED by @maint", "url": "https://github.com/acme/widgets/issues/390"}


def test_osascript_takes_text_only_as_arguments():
    """Item 1: no title can change the AppleScript; the URL stays in the visible text."""
    evil = dict(ALERT, title='t" & do shell script "touch /tmp/pwned" & "\\')
    argv = notify.desktop_argv("osascript", evil)
    assert argv[:argv.index("--") + 1] == notify.OSASCRIPT, "the script is fixed"
    assert argv[-2:] == [evil["title"], f"{ALERT['message']} {ALERT['url']}"]
    assert not any("do shell script" in x for x in notify.OSASCRIPT)


def test_only_known_links_are_openable():
    """Item 7."""
    argv = notify.desktop_argv("terminal-notifier", dict(ALERT, url="https://evil.example/x"))
    assert "-open" not in argv
    assert slack.SLACK_LINK.match("https://acme.slack.com/archives/C1/p1") and not slack.SLACK_LINK.match("https://evil.example/")


@pytest.mark.parametrize("url,ok", [("https://hooks.example.com/x", True), ("http://localhost:8080/h", True),
                                    ("http://hooks.example.com/x", False), ("file:///etc/passwd", False)])
def test_webhook_scheme(url, ok):
    """Item 15."""
    assert notify.webhook_ok(url) is ok


def test_terminal_notifier_escapes_a_leading_bracket():
    argv = notify.desktop_argv("terminal-notifier", ALERT)
    assert argv[argv.index("-message") + 1] == "\\[Bug] ACCEPTED by @maint"
    assert argv[argv.index("-open") + 1] == ALERT["url"]


def test_stdout_always_and_backend_failures_do_not_raise(capsys, monkeypatch):
    monkeypatch.setenv("GH_UPSTREAM_WATCH_NOTIFY", "/no/such/notifier")
    entry = {"alert": ALERT, "pending": ["stdout", "desktop", "webhook"]}
    assert notify.deliver(entry, "command", webhook="https://127.0.0.1:1/x") == ["desktop", "webhook"], \
        "a desktop failure does not skip the webhook attempt"
    out, err = capsys.readouterr()
    assert ALERT["url"] in out and "desktop" in err and "webhook" in err
    notify.send_one("stdout", ALERT, as_json=True)
    assert json.loads(capsys.readouterr().out) == ALERT


def test_command_backend_gets_alert_json_on_stdin(tmp_path, monkeypatch, capsys):
    sink = tmp_path / "got.json"
    script = tmp_path / "notifier"
    script.write_text(f"#!{sys.executable}\nimport sys; open({str(sink)!r}, 'w').write(sys.stdin.read())\n")
    script.chmod(0o755)
    monkeypatch.setenv("GH_UPSTREAM_WATCH_NOTIFY", str(script))
    assert notify.resolve("auto") == "command"
    assert notify.send_one("desktop", ALERT, "command")
    assert json.loads(sink.read_text()) == ALERT


def make_hook(tmp_path, body):
    h = tmp_path / "hook"
    h.write_text(f"#!{sys.executable}\nimport json, sys\n{body}\n")
    h.chmod(0o755)
    return str(h)


def test_hook_reads_item_json_and_emits_alert_jsonl(tmp_path):
    h = make_hook(tmp_path, "item = json.load(sys.stdin)\n"
                            "print(json.dumps({'message': 'hook saw ' + item['key'], 'kind': 'custom'}))\n"
                            "print('')")
    assert hooks.run(h, {"key": "acme/widgets#1"}) == [("custom", "hook saw acme/widgets#1", None)]


def test_hook_failures_yield_nothing(tmp_path, monkeypatch):
    assert hooks.run(make_hook(tmp_path, "sys.exit(3)"), {}) is None
    assert hooks.run(make_hook(tmp_path, "print('not json')"), {}) is None, "malformed output is a failure"
    assert hooks.run(make_hook(tmp_path, "print(json.dumps({'no_message': 1}))"), {}) is None
    monkeypatch.setattr(hooks, "TIMEOUT", 0.5)
    assert hooks.run(make_hook(tmp_path, "import time; time.sleep(5)"), {}) is None
    with pytest.raises(SystemExit):
        hooks.check("relative/hook")


# --- Slack (opt-in) ---------------------------------------------------------------------------

T = 1.8e9
CFG = {"user": "UEXAMPLE1", "query": "find <@{user}> in the last {days} days", "every_minutes": 60, "days": 14,
       "claude": "/bin/true"}


def answer(items):
    class P:
        stdout = json.dumps({"result": json.dumps(items)})
        stderr, returncode = "", 0
    return lambda *a, **k: P()


def reply(ts, your_ts="1790000000.000100", **kw):
    return dict({"channel": "#dev", "channel_id": "C0EXAMPLE", "ts": ts, "author": "A", "kind": "thread_reply",
                 "your_ts": your_ts, "text": "ping", "link": ""}, **kw)


def test_slack_parse_is_strict():
    assert slack.parse(json.dumps({"result": "[]"})) == []
    assert slack.parse(json.dumps({"result": "```json\n[{\"ts\": \"1\"}]\n```"})) == [{"ts": "1"}]
    for bad in ("prose", json.dumps({"result": "Sure! [{}] here you go"}), json.dumps({"result": "{}"}),
                json.dumps({"no_result": 1}), json.dumps({"result": None}), "[]", "null"):
        with pytest.raises(slack.SlackError):
            slack.parse(bad)


def test_slack_seeds_throttles_proves_and_windows():
    st = {}
    calls = []

    def run(items):
        base = answer(items)
        return lambda *a, **k: calls.append(a[0]) or base()

    assert slack.check(CFG, st, T, run([reply(f"{T - 50:.6f}")])) == [], "first run seeds quietly"
    argv = calls[0]
    assert argv[argv.index("--tools") + 1] == "", "item 6: no built-in tools at all"
    assert argv[argv.index("--allowedTools") + 1] == ",".join(slack.TOOLS)
    assert "--setting-sources" in argv and "slack@claude-plugins-official" in " ".join(argv)
    assert slack.check(CFG, st, T + 30 * 60, run([])) == [] and len(calls) == 1, "throttled"
    assert slack.check(CFG, st, T + 61 * 60, run([reply(f"{T - 40:.6f}", your_ts="")])) == [], "no proof: dropped"
    got = slack.check(CFG, st, T + 122 * 60, run([reply(f"{T - 30:.6f}")]))
    assert [(a["title"], a["message"]) for a in got] == [("Slack #dev: A replied in a thread you're in", "ping")]
    mention = reply(f"{T - 20:.6f}", kind="mention", evidence="Hi everyone")
    assert slack.check(CFG, st, T + 183 * 60, run([mention])) == [], "a mention without the raw tag is dropped"
    mention = reply(f"{T - 10:.6f}", kind="mention", evidence="<@UEXAMPLE1|someone>")
    assert len(slack.check(CFG, st, T + 244 * 60, run([mention]))) == 1
    old = reply(f"{T - 21 * 86400:.6f}", kind="mention", evidence="<@UEXAMPLE1>")
    assert slack.check(CFG, st, T + 305 * 60, run([old])) == [], "older than the window never alerts"
    phish = reply(f"{T - 6:.6f}", link="https://evil.example/login")
    assert slack.check(CFG, st, T + 366 * 60, run([phish]))[0]["url"] == "", "item 7: only slack.com links pass"
    fake_channel = reply(f"{T - 5:.6f}", channel_id="#dev")
    assert slack.check(CFG, st, T + 427 * 60, run([fake_channel])) == [], "no channel id: dropped"
    other = reply(f"{T - 4:.6f}", channel_id="C0OTHER1")
    assert slack.check(dict(CFG, channels=["C0EXAMPLE"]), st, T + 488 * 60, run([other])) == [], "outside the channel list"


def test_slack_failure_has_its_own_state_and_never_blocks_github(fake, tmp_path, capsys, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"slack": dict(CFG, enabled=True)}))
    monkeypatch.setattr(slack.subprocess, "run", answer("not a list"))
    monkeypatch.setenv("GH_TOKEN", "secret")
    assert "GH_TOKEN" not in slack.minimal_env(), "item 6: no GitHub token reaches claude"
    args = ["--config", str(cfg), "--state", str(tmp_path / "s.json"), "--repos", "acme/widgets",
            "--packs-dir", str(FIXTURES / "packs"), "--notify", "none"]
    fake.load("run1")
    assert cli.main(args) == 0, "GitHub pass is complete even though Slack failed"
    st = json.loads((tmp_path / "s.json").read_text())
    assert st["seeded"] and st["slack"]["failures"] == 1 and "JSON array" in st["slack"]["last_error"]
    fake.load("run2")
    assert cli.main(args) == 0 and len(capsys.readouterr().out.splitlines()) == 6


# --- packs and config ---------------------------------------------------------------------------

def test_bundled_packs_load():
    ids = {p["id"] for p in packs.load()}
    assert {"generic", "vllm-semantic-router"} <= ids


@pytest.mark.parametrize("bad", [
    {"id": "x"},
    {"id": "x", "repos": ["*"], "typo": 1},
    {"id": "x", "repos": ["*"], "gates": [{"id": "g", "comment": "/accept", "authorized_by": {"logins": ["a"]}, "alert": "a"}]},
    {"id": "x", "repos": ["*"], "gates": [{"id": "g", "comment": "^/accept", "authorized_by": {"logins": "maint"}, "alert": "a"}]},
    {"id": "x", "repos": ["*"], "gates": [{"id": "g", "comment": "^/accept", "authorized_by": {"associations": ["ADMIN"]}, "alert": "a"}]},
    {"id": "x", "repos": ["*"], "label_transitions": [{"id": "t", "alert": "a", "has": "lgtm"}]},
    {"id": "x", "repos": "acme/*"},
    {"id": "x", "repos": ["*"], "quiet_titles": ["("]},
    {"id": "x", "repos": ["*"], "messages": {"nope": "x"}}])
def test_bad_packs_are_rejected(bad):
    with pytest.raises(packs.PackError):
        packs.validate(bad, "test")


def test_a_local_pack_overrides_by_id_and_packs_merge(tmp_path):
    (tmp_path / "g.json").write_text(json.dumps({"id": "generic", "repos": ["*"], "messages": {"reopened": "BACK"}}))
    rules = packs.for_repo(packs.load([tmp_path, FIXTURES / "packs"]), "acme/widgets")
    assert rules["ids"] == ["generic", "acme"] and rules["messages"]["reopened"] == "BACK"
    assert [g["id"] for g in rules["gates"]] == ["accept"]


def test_config_precedence_cli_over_env_over_file(tmp_path, monkeypatch):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"repos": ["file/repo"], "notify": "none", "state": "~/from-file.json"}))
    a = cli.build_parser().parse_args(["--config", str(cfg)])
    assert cli.load_config(a)["repos"] == ["file/repo"]
    monkeypatch.setenv("GH_UPSTREAM_WATCH_REPOS", "env/one, env/two")
    assert cli.load_config(a)["repos"] == ["env/one", "env/two"]
    a = cli.build_parser().parse_args(["--config", str(cfg), "--repos", "cli/repo"])
    assert cli.load_config(a)["repos"] == ["cli/repo"]
    cfg.write_text(json.dumps({"repo": ["typo"]}))
    with pytest.raises(cli.ConfigError):
        cli.load_config(a)


@pytest.mark.parametrize("argv", [["--extra", "acme/widgets"], ["--extra", "widgets#3"], ["--webhook", "file:///etc/passwd"],
                                  ["--webhook", "http://hooks.example.com/x"]])
def test_bad_arguments_are_rejected(argv):
    with pytest.raises(cli.ConfigError):
        cli.load_config(cli.build_parser().parse_args(argv))


def test_print_helpers(capsys):
    assert cli.main(["--print-plist", "--interval", "15"]) == 0
    out = capsys.readouterr().out
    assert "<key>Minute</key><integer>45</integer>" in out and "<string>--once</string>" in out
    assert cli.main(["--print-systemd"]) == 0 and "OnUnitActiveSec=15min" in capsys.readouterr().out
    cron = (cli.main(["--print-cron"]), capsys.readouterr().out)[1]
    assert "*/15 * * * * mkdir -p $HOME/.local/state/gh-upstream-watch &&" in cron, "item 15: the log dir exists"


def test_init_and_status(tmp_path, capsys, fake):
    """Item 14: init fills the login from gh; status is a doctor."""
    fake.load("run1")
    cfg = tmp_path / "config.json"
    assert cli.main(["init", "--config", str(cfg), "--repos", "acme/widgets"]) == 0
    assert json.loads(cfg.read_text())["repos"] == ["acme/widgets"] and json.loads(cfg.read_text())["login"] == "octocat"
    assert cli.main(["init", "--config", str(cfg)]) == 2, "no overwrite without --force"
    capsys.readouterr()
    st = tmp_path / "s.json"
    assert cli.main(["--config", str(cfg), "--state", str(st), "--notify", "none"]) == 0
    fake.responses["repos/acme/widgets/issues/390"] = {"__error__": "HTTP 502"}
    assert cli.main(["--config", str(cfg), "--state", str(st), "--notify", "none"]) == 1
    capsys.readouterr()
    assert cli.main(["status", "--config", str(cfg), "--state", str(st)]) == 0
    out = capsys.readouterr().out
    assert "login         @octocat" in out and "repo          acme/widgets (packs: generic" in out
    assert "unknown       acme/widgets#390" in out and "failing       repo:acme/widgets: 1 run(s) in a row" in out
    assert "seeded        yes" in out and "last complete" in out and "schedule" in out


def test_bundled_gate_trusts_owner_and_collaborator_only():
    """Item 8: the bundled pack does not trust MEMBER (org members can be read-only)."""
    sr = next(p for p in packs.load() if p["id"] == "vllm-semantic-router")
    assert sr["gates"][0]["authorized_by"]["associations"] == ["OWNER", "COLLABORATOR"]
    gate = {"authorized_by": {"logins": []}}
    assert packs.authorized(gate, "a", "OWNER") and not packs.authorized(gate, "a", "MEMBER"), "empty logins: defaults"
    assert not packs.authorized({"authorized_by": {"logins": ["lead"]}}, "a", "OWNER"), "listed logins only"


def test_contributing_prow_example_works():
    """Item 16: the worked example in CONTRIBUTING.md is a valid pack and fires as documented."""
    import re
    from conftest import ROOT
    block = re.search(r"```json\n(\{\n  \"id\": \"kubernetes-prow\".*?)```", (ROOT / "CONTRIBUTING.md").read_text(), re.S).group(1)
    rules = packs.for_repo([packs.validate(json.loads(block), "CONTRIBUTING.md")], "kubernetes/test-infra")
    from gh_upstream_watch import core
    fp = {"title": "x", "url": "https://github.com/kubernetes/test-infra/pull/1", "state": "open", "assignees": [],
          "human_comments": 0, "labels": ["lgtm"]}
    ready = dict(fp, labels=["approved", "lgtm"])
    assert [m for _, m, _ in core.changes(fp, ready, "octocat", rules)][0].startswith("LGTM + APPROVED")
    held = dict(fp, labels=["approved", "do-not-merge/hold", "lgtm"])
    assert "HOLD placed" in " ".join(m for _, m, _ in core.changes(ready, held, "octocat", rules))
    cancelled = dict(fp, labels=["approved"])
    assert "labels: -lgtm" in [m for _, m, _ in core.changes(ready, cancelled, "octocat", rules)]


def test_personal_data_gate_allows_only_the_repo_slug(tmp_path):
    """Item 5."""
    import shutil
    import subprocess
    from conftest import ROOT
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "check-personal-data.sh", tmp_path / "scripts")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    slug = "col" + "inmcna" + "mara/gh-upstream-watch"
    (tmp_path / "README.md").write_text(f"gh extension install {slug}\n")
    gate = [str(tmp_path / "scripts" / "check-personal-data.sh")]
    for f in ("LICENSE", "pyproject.toml"):  # the two allowed lines, copied verbatim
        shutil.copy(ROOT / f, tmp_path / f)
    assert subprocess.run(gate, capture_output=True).returncode == 0
    email = "col" + "in@2c" + "ups.com"
    (tmp_path / "README.md").write_text(f"gh extension install {slug}\nContact: {email}\n")
    assert subprocess.run(gate, capture_output=True).returncode == 1, "a planted email in README fails"
    (tmp_path / "README.md").write_text((ROOT / "LICENSE").read_text())
    assert subprocess.run(gate, capture_output=True).returncode == 1, "the copyright line is allowed only in LICENSE"


def test_version_is_single_valued():
    import re
    from conftest import ROOT
    from gh_upstream_watch import __version__
    assert re.search(r'^version = "(.*)"', (ROOT / "pyproject.toml").read_text(), re.M).group(1) == __version__


def test_check_pack_validates_and_describes(tmp_path, capsys):
    bundled = Path(packs.__file__).parent / "packs" / "vllm-semantic-router.json"
    assert cli.main(["check-pack", str(bundled)]) == 0
    out = capsys.readouterr().out
    assert "gate accept: comment `^/accept\\b`, trusts associations ['OWNER', 'COLLABORATOR']" in out
    assert "claim board" in out and "'MEMBER'" in out
    mine = tmp_path / "vllm-semantic-router.json"
    mine.write_text(bundled.read_text())
    assert cli.main(["check-pack", str(mine)]) == 0
    assert "(replaces the bundled pack)" in capsys.readouterr().out
    bad = tmp_path / "bad.json"
    bad.write_text('{"id": "x", "repos": ["a/*"], "gates": [{"id": "g", "comment": "/accept", "authorized_by": {}, "alert": "a"}]}')
    assert cli.main(["check-pack", str(bad)]) == 2
    assert "anchored" in capsys.readouterr().err
    assert cli.main(["check-pack", str(tmp_path / "missing.json")]) == 2
    assert cli.main(["check-pack"]) == 2


@pytest.mark.parametrize("err,fix", [
    ("user: gh exited 1: HTTP 401", "gh auth login"),
    ("user: gh exited 1: gh: Bad credentials (HTTP 401)", "gh auth login"),
    ("user: gh exited 4: To get started with GitHub CLI, please run:  gh auth login", "gh auth login"),
    ("user: [Errno 2] No such file or directory: 'gh'", "https://cli.github.com"),
    ("notifications: gh exited 1: gh: Not Found (HTTP 404)", "gh auth refresh -s notifications"),
    ("repos/a/b/issues/1: gh exited 1: gh: Not Found (HTTP 404)", "`forget` drops it"),
    ("search/issues: gh exited 1: API rate limit exceeded (HTTP 403)", "rate limited"),
    ("search 'repo:a/b': 1200 results exceed the 1000 cap", "narrow repos"),
    ("something else entirely", ""),
])
def test_hint_gives_the_next_step(err, fix):
    assert fix in cli.hint(err) if fix else cli.hint(err) == ""


def test_slack_messages_seen_by_the_old_script_do_not_repeat():
    """The single-file script keyed Slack messages as '#channel:ts'; a migrated state must still match."""
    ts = f"{T - 60:.6f}"
    st = {"last": 0, "seeded": True, "seen": {f"#dev:{ts}": float(ts)}}
    assert slack.check(CFG, st, T, answer([reply(ts)])) == []
    assert f"C0EXAMPLE:{ts}" in st["seen"], "remembered under the new key from now on"
    assert len(slack.check(CFG, dict(st, last=0), T, answer([reply(f"{T - 30:.6f}")]))) == 1, "a new one still alerts"


def test_migrate_says_how_to_absorb_old_history(tmp_path, capsys):
    v0 = tmp_path / "old.json"
    v0.write_text((FIXTURES / "state_v0.json").read_text())
    new = tmp_path / "new.json"
    assert cli.main(["migrate", "--from", str(v0), "--state", str(new), "--repos", "acme/widgets"]) == 0
    out = capsys.readouterr().out
    assert f"--state {new} --repos acme/widgets --notify none --webhook '' --no-slack" in out
    assert "old job is still running" in out

