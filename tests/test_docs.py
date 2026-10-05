"""The README cannot drift from the code: its commands parse and its config keys exist."""
import json
import re
import shlex
from pathlib import Path

from gh_upstream_watch import cli

README = (Path(__file__).resolve().parents[1] / "README.md").read_text()
BLOCKS = re.findall(r"```(\w*)\n(.*?)```", README, re.S)


def commands():
    """Every `gh-upstream-watch ...` invocation in a shell block, as argv after the program name."""
    for lang, body in BLOCKS:
        if lang not in ("sh", "bash", ""):
            continue
        for line in body.splitlines():
            for part in re.split(r"&&|\|", line.split(" #")[0]):
                m = re.search(r"(?:^|\s)gh-upstream-watch((?:\s+[^\s>]+)*)", part)
                if m and not part.strip().startswith(("uv ", "pipx ", "gh ")):
                    yield shlex.split(m.group(1))


def test_every_readme_command_parses():
    found = list(commands())
    assert len(found) >= 8, found
    parser = cli.build_parser()
    for argv in found:
        parser.parse_args(argv)  # SystemExit on an unknown flag or command


def test_readme_config_table_matches_the_code():
    """Every key in the Configuration table, with its default, exactly as the code has them."""
    section = README.split("## Configuration", 1)[1].split("\n## ", 1)[0]
    rows = dict(re.findall(r"^\| `(\w+)` \| `([^`]*)` \|", section, re.M))
    assert set(rows) == set(cli.DEFAULTS), set(rows) ^ set(cli.DEFAULTS)
    for key, default in rows.items():
        assert json.loads(default) == cli.DEFAULTS[key], key


def test_readme_pack_example_is_a_valid_pack():
    from gh_upstream_watch import packs
    examples = [json.loads(body) for lang, body in BLOCKS if lang == "json" and '"gates"' in body]
    assert examples
    for p in examples:
        packs.validate(p, "README")


def test_version_pins_in_the_docs_are_current():
    """The gh extension pin and the release-verification example name the current version."""
    from gh_upstream_watch import __version__ as v
    security = (Path(__file__).resolve().parents[1] / "SECURITY.md").read_text()
    assert f"--pin v{v} " in README
    assert f"pypi:gh_upstream_watch-{v}-py3-none-any.whl" in security


def test_readme_demo_matches_the_golden_run():
    golden = (Path(__file__).resolve().parent / "fixtures" / "demo" / "run2.golden").read_text().splitlines()
    shown = [re.sub(r"^\[[^]]+\] ", "", line) for line in README.splitlines() if line.startswith("[2026-")]
    assert shown == golden


def test_every_alert_kind_is_documented():
    from gh_upstream_watch import core
    row = next(line for line in README.splitlines() if line.startswith("| `kind` |"))
    listed = set(re.findall(r"`(\w+)`", row)) - {"kind"}
    emitted = core.ACTION_KINDS | {"gate_done", "reference", "state", "labels", "merged", "milestone", "ci_passed", "ci_waiting",
                                   "review", "comments", "gone"}
    assert emitted <= listed, emitted - listed
