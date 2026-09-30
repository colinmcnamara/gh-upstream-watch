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
   | `claimable` | `{search, title, comment_marker, section, row, alert}`: a claim-board issue; `row` needs named groups `number`, `title`, `url`; `{group}` in `comment_marker` and `alert` |

2. Add a test with a synthetic fixture that shows the alert firing, and one that shows an
   unauthorized or unrelated comment staying silent. `tests/test_core.py` has examples.
3. Anything packs cannot express belongs in a `--hook` script, not in the engine.

## Out of scope

Anything that writes to GitHub, a daemon or web UI, GraphQL, YAML/TOML config, rule packs fetched
from the network, email. A pack for another project's command workflow is a welcome first PR.

## Releasing (maintainers)

Bump the version in both `pyproject.toml` and `__version__` (a test checks they match) and `CHANGELOG.md`, tag `vX.Y.Z`, push the tag. `release.yml` builds, publishes
with `uv publish` to TestPyPI then PyPI by trusted publishing, and creates the GitHub Release.

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
