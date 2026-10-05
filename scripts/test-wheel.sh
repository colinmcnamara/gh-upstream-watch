#!/bin/sh
# Test what users install, not src/: the whole suite against the built wheel, and the sdist built
# and run on its own. Catches package data or an entry point that works only from the checkout.
# Usage: uv build --clear && scripts/test-wheel.sh [PYTHON_VERSION]
set -eu
cd "$(dirname "$0")/.."
unset PYTHONPATH  # an inherited src/ would quietly test the checkout instead
py="${1:-3.14}"
v=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
whl="dist/gh_upstream_watch-$v-py3-none-any.whl"
sdist="dist/gh_upstream_watch-$v.tar.gz"
test -f "$whl" && test -f "$sdist" || { echo "no $whl and $sdist: run uv build --clear first"; exit 1; }
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# Test tools at the versions and hashes in uv.lock, never whatever PyPI serves today.
uv export -q --frozen --only-group dev --no-emit-project -o "$work/dev.txt"
uv venv -q --python "$py" "$work/wheel"
uv pip install -q --python "$work/wheel/bin/python" -r "$work/dev.txt" "$whl"
"$work/wheel/bin/python" -c '
import os, sys, gh_upstream_watch as g
here = os.path.realpath(g.__file__)
sys.exit(0 if here.startswith(os.path.realpath(sys.prefix) + os.sep) else f"testing {here}, not the installed wheel")'
"$work/wheel/bin/gh-upstream-watch" --help > /dev/null
"$work/wheel/bin/gh-upstream-watch" --print-plist > /dev/null
GH_UPSTREAM_WATCH_TEST_INSTALLED=1 "$work/wheel/bin/python" -m pytest -q -p no:cacheprovider

uv venv -q --python "$py" "$work/sdist"
uv pip install -q --python "$work/sdist/bin/python" "$sdist"
"$work/sdist/bin/gh-upstream-watch" --version
"$work/sdist/bin/python" -m gh_upstream_watch --print-plist > /dev/null
