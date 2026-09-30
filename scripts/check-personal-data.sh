#!/bin/sh
# Release gate: fail if any tracked or untracked (not ignored) file carries personal data:
# an author's name or domain, a home-directory path, a package-manager prefix, a Slack user id,
# or a reference to a specific upstream issue. Patterns are assembled from pieces so this script
# does not match itself. Add a term here when a new kind of leak is found.
#
# Allowed, and nothing else: the LICENSE copyright line, the pyproject authors entry (both
# matched as whole lines in those files only), and the repository slug.
set -eu
cd "$(dirname "$0")/.."
NAME='Col''in McNa''mara'
PAT='col''in|mcna''mara|/Us''ers/|home''brew|2c''ups|U0[0-9A-Z]{8}|vllm-project/vl''lm#'
SLUG='col''inmcna''mara/gh-upstream-watch'
LICENSE_LINE="LICENSE:3:Copyright (c) 2026 $NAME LLC"
AUTHORS_LINE="pyproject.toml:13:authors = [{ name = \"$NAME\", email = \"col""in@2c""ups.com\" }]"
hits=$(git grep --untracked -nIiE "$PAT" -- . ':!*.whl' ':!*.tar.gz' \
  | grep -vxF -e "$LICENSE_LINE" -e "$AUTHORS_LINE" \
  | sed "s#$SLUG##g" | grep -iE "$PAT" || true)
if [ -n "$hits" ]; then
  echo "$hits"
  echo "check-personal-data: FAILED (matches above)" >&2
  exit 1
fi
echo "check-personal-data: ok"
