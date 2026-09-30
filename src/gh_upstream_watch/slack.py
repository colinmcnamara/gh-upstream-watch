"""Opt-in Slack source: a headless Claude Code call with the Slack plugin, read-only tools only.

Model output is untrusted. The answer must decode as a JSON array, and each item must carry
proof (a channel id, a message ts, the text, and either the raw <@user> tag or your own ts in
the thread) or it is dropped. A failed check has its own failure counter and never touches
GitHub state. Off unless config slack.enabled is true (or --slack).

The proof is itself model output: a prompt injection in a Slack message can forge it. What limits
the damage is that the model has no tools but two Slack read tools, and only https://*.slack.com/
links are ever passed on to a notifier.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile

TOOLS = [f"mcp__plugin_slack_slack__slack_{t}" for t in ("search_public", "read_thread")]
# --tools "" removes every built-in tool (Bash, Read, Write, WebFetch...); --allowedTools lets the two
# Slack read tools run unprompted, and in print mode any other tool is denied. No user settings:
# hooks or output styles can turn the answer into prose. The run happens in an empty temp dir.
ISOLATION = ["--tools", "", "--allowedTools", ",".join(TOOLS), "--permission-mode", "default",
             "--setting-sources", "project", "--disable-slash-commands", "--no-session-persistence",
             "--settings", json.dumps({"enabledPlugins": {"slack@claude-plugins-official": True}})]
ENV_KEEP = ("HOME", "PATH", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "SHELL", "TERM")
SLACK_LINK = re.compile(r"^https://[\w-]+\.slack\.com/")
CHANNEL_RE = re.compile(r"^[CDG][A-Z0-9]{2,}$")
TS_RE = re.compile(r"^\d{9,}\.\d+$")


class SlackError(Exception):
    pass


def parse(stdout):
    """The item list from `claude -p --output-format json`, or SlackError. One optional code fence
    around the array is tolerated; anything else that is not exactly a JSON array is a failure."""
    try:
        result = json.loads(stdout)["result"]
    except (ValueError, KeyError, TypeError):
        raise SlackError("claude did not return its JSON envelope")
    if not isinstance(result, str):
        raise SlackError(f"claude envelope has no text result ({type(result).__name__})")
    body = result.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", body, re.S)
    try:
        items = json.loads(fence.group(1) if fence else body)
    except ValueError:
        raise SlackError(f"answer is not a JSON array: {' '.join(body.split())[:160]}")
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise SlackError("answer is not a JSON array of objects")
    return items


def proven(item, user, channels):
    """Hallucination filter: every item must point at something checkable, or it is dropped."""
    if not (CHANNEL_RE.match(str(item.get("channel_id", ""))) and TS_RE.match(str(item.get("ts", "")))
            and str(item.get("text", "")).strip()):
        return False
    if channels and item["channel_id"] not in channels:
        return False
    if item.get("kind") == "mention":
        return re.search(rf"<@{re.escape(user)}[>|]", str(item.get("evidence", ""))) is not None
    return item.get("kind") == "thread_reply" and bool(TS_RE.match(str(item.get("your_ts", ""))))


def minimal_env():
    """What claude needs to run and sign in, and nothing else (no GitHub tokens)."""
    return {k: v for k, v in os.environ.items() if k in ENV_KEEP or k.startswith(("ANTHROPIC_", "CLAUDE_"))}


def check(cfg, st, now, runner=None):
    """New Slack alerts, throttled to one model call per every_minutes. Raises SlackError."""
    every, days = cfg.get("every_minutes", 25), cfg.get("days", 14)
    if now - st.get("last", 0) < every * 60:
        return []
    user, query = cfg.get("user"), cfg.get("query")
    if not user or not query:
        raise SlackError("slack.user and slack.query must be set in the config file")
    claude = cfg.get("claude") or shutil.which("claude")
    if not claude:
        raise SlackError("claude (Claude Code) not found; set slack.claude")
    argv = [claude, "-p", query.format(user=user, days=days), "--model", cfg.get("model", "haiku"),
            "--output-format", "json", "--max-turns", "20", *ISOLATION]
    try:
        with tempfile.TemporaryDirectory(prefix="ghuw-slack-") as cwd:
            proc = (runner or subprocess.run)(argv, capture_output=True, text=True, timeout=cfg.get("timeout", 600),
                                              cwd=cwd, env=minimal_env())
    except (OSError, subprocess.SubprocessError) as e:
        raise SlackError(str(e))
    if proc.returncode != 0:
        raise SlackError(f"claude exited {proc.returncode}: {' '.join((proc.stderr or proc.stdout or '').split())[-200:]}")
    items = parse(proc.stdout)
    first = not st.get("seeded")
    st.update(last=now, seeded=True)
    seen, fresh = st.setdefault("seen", {}), []
    for item in items:
        if not proven(item, user, cfg.get("channels") or []):
            continue
        key = f"{item['channel_id']}:{item['ts']}"
        if key in seen:
            continue
        seen[key] = float(item["ts"])
        # The model ignores the time window sometimes, so it is enforced here.
        if first or float(item["ts"]) < now - days * 86400:
            continue
        verb = "mentioned you" if item["kind"] == "mention" else "replied in a thread you're in"
        fresh.append({"kind": "slack", "key": key, "title": f"Slack {item.get('channel', item['channel_id'])}: "
                      f"{item.get('author', '?')} {verb}", "message": str(item["text"])[:120],
                      "url": item["link"] if SLACK_LINK.match(str(item.get("link", ""))) else ""})
    return fresh
