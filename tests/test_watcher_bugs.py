"""Two bugs seen on the live watcher (2026-10-01/02): Slack answers wrapped in prose, and a
terminal-notifier subtitle that starts with a bracket."""
import json

import pytest
from test_extras import CFG, T, reply

from gh_upstream_watch import notify, slack

ITEMS = [reply(f"{T - 50:.6f}"), reply(f"{T - 40:.6f}", author="B")]
# Logged 2026-10-01 07:22: prose, then a ```json fence holding the array.
PROSE_THEN_FENCE = ("Based on the search and thread analysis, I found 2 thread replies from others in the last 14 "
                    "days where they responded to your messages:\n\n```json\n" + json.dumps(ITEMS, indent=2) + "\n```")
# Logged 2026-10-02 13:44: prose only, no array.
PROSE_ONLY = ("Looking at the search results:\n\nFrom the mention search: The only message mentioning you is from "
              "a colleague on 2026-09-04, which is outside the 14-day window.")


def envelope(result, structured=None):
    d = {"type": "result", "result": result}
    if structured is not None:
        d["structured_output"] = structured
    return json.dumps(d)


def test_structured_output_wins_over_prose():
    assert slack.parse(envelope("Here is what I found, in prose.", {"items": ITEMS})) == ITEMS


def test_one_json_fence_inside_prose_is_read():
    assert slack.parse(envelope(PROSE_THEN_FENCE)) == ITEMS


def test_prose_with_no_array_is_still_a_failure():
    with pytest.raises(slack.SlackError, match="not a JSON array"):
        slack.parse(envelope(PROSE_ONLY))


def test_two_fences_are_ambiguous_and_fail():
    two = f"first:\n```json\n{json.dumps(ITEMS[:1])}\n```\nsecond:\n```json\n{json.dumps(ITEMS[1:])}\n```"
    with pytest.raises(slack.SlackError):
        slack.parse(envelope(two))


def test_check_asks_claude_for_structured_output():
    calls = []

    class P:
        returncode, stderr, stdout = 0, "", envelope("prose", {"items": ITEMS})
    st = {"seeded": True}
    got = slack.check(CFG, st, T, lambda argv, **k: calls.append(argv) or P())
    argv = calls[0]
    schema = json.loads(argv[argv.index("--json-schema") + 1])
    assert schema["properties"]["items"]["type"] == "array" and len(got) == 2


def test_an_older_claude_without_json_schema_still_works():
    calls = []

    class Old:
        returncode, stdout, stderr = 1, "", "error: unknown option '--json-schema'"

    class P:
        returncode, stderr, stdout = 0, "", envelope(json.dumps(ITEMS))
    run = lambda argv, **k: calls.append(argv) or (Old() if "--json-schema" in argv else P())  # noqa: E731
    assert len(slack.check(CFG, {"seeded": True}, T, run)) == 2
    assert "--json-schema" not in calls[-1]


@pytest.mark.parametrize("title", ["[Bugfix] Keep the stop sequence", "(draft) x", "{x}", '"quoted"', "'q'"])
def test_terminal_notifier_escapes_every_outside_value(title):
    argv = notify.desktop_argv("terminal-notifier", {"title": title, "message": "[Bug] x", "url": ""})
    assert argv[argv.index("-subtitle") + 1] == "\\" + title
    assert argv[argv.index("-message") + 1] == "\\[Bug] x"
    plain = notify.desktop_argv("terminal-notifier", {"title": "ok", "message": "fine", "url": ""})
    assert plain[plain.index("-subtitle") + 1] == "ok" and plain[plain.index("-message") + 1] == "fine", "no escape needed"
