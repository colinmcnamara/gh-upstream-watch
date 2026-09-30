#!/bin/sh
# Two passes over recorded, synthetic GitHub data (tests/fixtures/demo): a silent seed, then alerts.
# No network: tests/fake_gh.py stands in for gh and refuses anything but GET.
set -eu
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
STATE="$(mktemp -d)/state.json"
export GH_UPSTREAM_WATCH_GH="$PWD/tests/fake_gh.py" XDG_CONFIG_HOME="$(mktemp -d)"
run() {
  echo "\$ gh-upstream-watch --repos acme/widgets --packs-dir tests/fixtures/packs --notify none"
  FAKE_GH_FIXTURE="tests/fixtures/demo/$1.json" "$PY" ./gh-upstream-watch --state "$STATE" \
    --repos acme/widgets --packs-dir tests/fixtures/packs --notify none 2>&1
}
run run1
echo
run run2
