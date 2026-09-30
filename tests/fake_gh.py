#!/usr/bin/env python3
"""A stand-in for `gh` that replays recorded GitHub responses from a JSON fixture.

It refuses anything that is not `gh api --method GET <path> [-f k=v ...]`, so a test that
reaches GitHub any other way fails loudly. Fixture: {"responses": {"path?k=v&...": body}}.
A body of {"__error__": "..."} makes that call fail like a gh error.

    GH_UPSTREAM_WATCH_GH=tests/fake_gh.py FAKE_GH_FIXTURE=tests/fixtures/demo/run1.json gh-upstream-watch
"""
import json
import os
import re
import sys


def normalize(key):
    """Dates computed from the clock become placeholders, so fixtures do not expire."""
    key = re.sub(r"updated:>=\d{4}-\d{2}-\d{2}", "updated:>=DATE", key)
    return re.sub(r"since=[^&]*", "since=SINCE", key)


def request_key(args):
    """args after the program name -> 'path?k=v&...'; ValueError for anything but a GET."""
    if args[:1] != ["api"]:
        raise ValueError("fake gh: only `gh api` is supported")
    if "--method" not in args or args[args.index("--method") + 1] != "GET":
        raise ValueError("fake gh: non-GET call refused")
    path, params, i, rest = None, [], 0, args[1:]
    while i < len(rest):
        if rest[i] == "--method":
            i += 2
        elif rest[i] == "-f":
            params.append(rest[i + 1])
            i += 2
        elif rest[i].startswith("-"):
            raise ValueError(f"fake gh: unexpected flag {rest[i]}")
        else:
            path, i = rest[i], i + 1
    return normalize(path + ("?" + "&".join(sorted(params)) if params else ""))


def respond(fixture, args):
    """(exit code, stdout, stderr) for one gh invocation."""
    try:
        key = request_key(args)
    except ValueError as e:
        return 2, "", str(e)
    if key not in fixture["responses"]:
        return 1, "", f"gh: Not Found (HTTP 404) [{key}]"
    body = fixture["responses"][key]
    if isinstance(body, dict) and "__error__" in body:
        return 1, "", body["__error__"]
    return 0, json.dumps(body), ""


def main():
    with open(os.environ["FAKE_GH_FIXTURE"]) as f:
        fixture = json.load(f)
    code, out, err = respond(fixture, sys.argv[1:])
    sys.stdout.write(out)
    sys.stderr.write(err)
    return code


if __name__ == "__main__":
    sys.exit(main())
