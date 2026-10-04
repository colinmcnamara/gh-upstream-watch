# Contributing

Small and boring is the goal: stdlib only, Python 3.9+, one pass per run, read-only.

```sh
uv sync                          # dev tools (pytest, ruff) from uv.lock; the package has no runtime deps
uv run pytest -q
uv run --python 3.9 pytest -q    # the supported floor
uv run ruff check .
uv build                         # sdist + wheel in dist/
scripts/check-personal-data.sh   # CI runs this too
scripts/demo.sh                  # the README demo, on recorded fixtures
```

Tests never touch the network. `tests/fake_gh.py` replays a JSON fixture of
`"path?sorted&params": response` pairs and refuses any call that is not a GET; the `fake`
pytest fixture does the same in-process and records every argv. A response of
`{"__error__": "HTTP 502"}` injects a failure.

Use synthetic data only: `octocat`, `monalisa`, `maint`, `acme/widgets`, invented issue numbers.
No real people, handles, Slack ids or local paths; the personal-data gate fails the build on them.

## Adding a rule pack

1. Write `src/gh_upstream_watch/packs/<id>.json` (bundled) or keep it in your own packs dir.

   | key | meaning |
   | --- | --- |
   | `id` | unique name; a later pack with the same id replaces an earlier one |
   | `repos` | globs (`owner/*`) this pack applies to; `*` packs apply first |
   | `gates` | `{id, comment, authorized_by: {associations, logins}, alert, then?, alert_done?}`; `comment` must start with `^`; a non-empty `logins` wins over `associations`; `{actor}` in alerts |
   | `label_transitions` | `{id, alert, has?, lacks?, assigned_to_me?}`: alerts when the condition becomes true |
   | `quiet_titles` | regexes; matching items alert on comments only when they `@`-name you |
   | `messages` | wording for `assigned`, `reopened`, `competing_pr`, `reference` (`{number}`, `{author}`, `{kind}`, `{target}`) |
   | `claimable` | `{search, title, comment_marker, section, row, alert, authorized_by?}`: a claim-board issue; `row` needs named groups `number`, `title`, `url`; `{group}` in `comment_marker` and `alert`; only a board and rows by authors `authorized_by` trusts are read (default OWNER, COLLABORATOR) |

   Check it with `gh-upstream-watch check-pack path/to/pack.json`: it validates the file and prints
   what each gate trusts, which titles are quiet, and whether it replaces a bundled pack.

2. Add a test with a synthetic fixture that shows the alert firing, and one that shows an
   unauthorized or unrelated comment staying silent. `tests/test_core.py` has examples.
3. Anything packs cannot express belongs in a `--hook` script, not in the engine.

## The state file

One JSON object (`schema` 1), written atomically under an exclusive lock, mode 0600. New keys are
additive; `state.load` quarantines a file whose known keys have the wrong type.

| key | holds |
| --- | --- |
| `items` | `owner/repo#n` -> fingerprint (`core.fingerprint`): title, state, labels, assignees, comment ids (`max_comment_id`, `human_ids`, `mention_ids`), gates, cross-references, reviews, `_seen` |
| `seeded_sources` | sources that have had one complete run: `repo:owner/repo`, `extra:owner/repo#n`, `claim:owner/repo`, `notifications` |
| `seeded` | true once every configured source is in `seeded_sources` |
| `retry` | new items whose first fetch failed -> first failure time; retried until they get a baseline |
| `unknown_streak` | source -> `{since, runs}` while it keeps failing; drives the escalation alert |
| `notifications.seen`, `claimable.seen` | ids already alerted, pruned after `retention_days` once GitHub stops returning them |
| `slack` | the optional Slack source's own seen ids and failure count |
| `outbox` | alerts not yet delivered to every destination: `{alert, pending, attempts}` |
| `last_complete`, `last_unknown` | when every check last succeeded, and what the last run could not check |

Alerts carry a source while a run is in progress. `state.hold_until_seeded` holds an alert until its
source is seeded, except `live` (a change against a saved baseline) and `slack` (which seeds
itself). A change to seeding belongs there, with a unit test in `tests/test_state.py`.

## Out of scope

Anything that writes to GitHub, a daemon or web UI, GraphQL, YAML/TOML config, rule packs fetched
from the network, email. A pack for another project's command workflow is a welcome first PR.

## Releasing (maintainers)

1. On a branch: bump the version in both `pyproject.toml` and `__version__` (a test checks they
   match), and add a dated `## [X.Y.Z] - YYYY-MM-DD` section to `CHANGELOG.md`. Date it before the
   PR, so the merge needs no extra commit.
2. Open the PR. `main` requires a PR and the five CI checks (`hygiene` and the four `test (...)`
   jobs), squash only. `gh pr merge N --squash --auto` merges when they pass.
3. Tag the merged commit `vX.Y.Z` and push the tag. The `release tags` ruleset protects `v*`; the
   push may print "creations being restricted" while it applies the maintainer bypass, and the
   tag is still created.
4. `release.yml` checks that the tag matches both version sources and the CHANGELOG, builds once,
   publishes to TestPyPI by trusted publishing, and installs and runs it from there. Then the
   `pypi` job waits for a maintainer to approve the `pypi` environment (in the run's page, or
   through the REST API's pending-deployments endpoint). Configure PyPI's trusted publisher with
   environment `pypi` (and TestPyPI's with `testpypi`), so no other job can publish. After approval it publishes to PyPI and creates the
   GitHub Release from the CHANGELOG section.
5. A failed release never moves a tag: fix forward with the next patch version.

## Worked example: a Kubernetes (prow) pack

Prow merges when a PR has `lgtm` and `approved` and no hold. Those are labels, so a label
transition says "ready" more reliably than a comment gate: a reviewer can take `/lgtm` back with
`/lgtm cancel`, which removes the label, and a gate on `^/lgtm\b` would also match that cancel.

```json
{
  "id": "kubernetes-prow",
  "repos": ["kubernetes/*", "kubernetes-sigs/*"],
  "label_transitions": [
    {"id": "mergeable", "has": ["lgtm", "approved"], "lacks": ["do-not-merge/hold"],
     "alert": "LGTM + APPROVED, no hold: tide will merge; watch the tests"},
    {"id": "held", "has": ["do-not-merge/hold"], "alert": "HOLD placed: read why before pushing"}
  ],
  "messages": {"reopened": "REOPENED: /reopen worked; check whether you still own it"}
}
```

Label changes are also reported generically (`labels: +lgtm`), so a cancelled `/lgtm` shows up as
`labels: -lgtm`. This exact block is checked by `tests/test_extras.py`.
