"""--hook: your own rules, as a program. Item JSON on stdin, alert JSONL on stdout.

The hook runs without a shell. It is trusted local config: it runs with your permissions.
It runs, with a timeout, once per watched item that has a previous fingerprint.
Each stdout line is {"message": "...", "kind": "...", "url": "..."}; only message is required.
"""
import json
import os
import subprocess
import sys

TIMEOUT = 30


def check(path):
    """Raises ValueError, which the CLI reports as a config error (exit 2)."""
    if not os.path.isabs(path):
        raise ValueError(f"--hook must be an absolute path: {path}")
    if not os.access(path, os.X_OK):
        raise ValueError(f"--hook is not executable: {path}")


def run(path, payload):
    """[(kind, message, url)] from the hook, or None when the hook failed (timeout, non-zero exit,
    a malformed line). None makes the item unknown for this run, so its state is not advanced."""
    try:
        proc = subprocess.run([path], input=json.dumps(payload), capture_output=True, text=True, timeout=TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"hook {path}: {e}", file=sys.stderr)
        return None
    if proc.returncode != 0:
        print(f"hook {path}: exit {proc.returncode}: {proc.stderr.strip()[:200]}", file=sys.stderr)
        return None
    out = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        try:
            a = json.loads(line)
            if not isinstance(a, dict) or not isinstance(a.get("message"), str):
                raise ValueError("needs a string 'message'")
        except ValueError as e:
            print(f"hook {path}: malformed output ({e}): {line[:120]}", file=sys.stderr)
            return None
        out.append((str(a.get("kind", "hook")), a["message"][:300], a.get("url") or None))
    return out
