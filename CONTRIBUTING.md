# Contributing

Small and boring is the goal: stdlib only, Python 3.10+, one pass per run, read-only.

```sh
uv sync                          # dev tools from uv.lock; the package has no runtime deps
uv run pytest -q
uv run --python 3.10 pytest -q   # the supported floor
uv run ruff check .
uv run mypy
uv run coverage run -m pytest -q && uv run coverage report   # fails under the floor in pyproject.toml
uv build --clear && scripts/test-wheel.sh   # the suite against the installed wheel; the sdist built and run
scripts/check-personal-data.sh   # CI runs this too
scripts/demo.sh                  # the README demo, on recorded fixtures
```

## How the tests work

Tests never reach the internet (the webhook tests use a local server). `tests/fake_gh.py` replays
a JSON fixture of `"path?sorted&params": response` pairs and refuses any call that is not a GET;
the `fake` pytest fixture does the same in-process and records every argv. A response of
`{"__error__": "HTTP 502"}` injects a failure.

Use synthetic data only: `octocat`, `monalisa`, `maint`, `acme/widgets`, invented issue numbers.
No real people, handles, Slack ids or local paths. The personal-data gate
(`scripts/check-personal-data.sh`) catches the maintainer's own names, paths and ids from a fixed
list; anything else is on the reviewer.

## Quality gates

CI runs the tests, coverage, ruff, mypy and the wheel tests on Linux and macOS, on the oldest and
newest supported Python; the personal-data gate, the lock check, gitleaks and zizmor run once, on Linux:

- **Tests are hermetic.** `tests/conftest.py` keeps every test away from the real `gh`,
  `launchctl`, `systemctl`, `crontab`, desktop notifiers and `claude`:
  - `subprocess` (all of it goes through `Popen`) and `os.system` refuse them, checking the words
    that run as programs (argv[0], the program after `env` or `nohup`, every word of shell code),
    `executable=` and symlink targets. The error is a `RealSystemCall`, which no
    `except Exception` can swallow.
  - A child process has no such guard, so tripwire stubs come first on its `PATH` (exit 97).
  - gh tokens are removed, so a real `gh` reached any other way is signed out. `HOME` is a temp
    dir and `time.sleep` is instant.
  - It catches accidents; it is not a sandbox. Not covered: a command hidden in a shell variable,
    a child process that runs a tool by absolute path or gets a `PATH` without the tripwire, and
    `os.exec*`, `os.spawn*` or `os.posix_spawn` by absolute path. Nothing here does those.
  - A test that needs a tool uses a fake: the `fake` fixture, a runner argument, or a script in
    its `tmp_path`. `tests/test_isolation.py` proves each layer with probes that are harmless even
    if the guard breaks.
- **pytest is strict:** unknown markers and config are errors, an unexpected xpass fails, any
  warning is an error (it found a leaked socket), and every test has a 30s timeout (it found a
  test server that hung on a reverse-DNS lookup).
- **Branch coverage** has a floor (`fail_under` in `pyproject.toml`). It is a ratchet: raise it
  when coverage rises, never lower it.
- **ruff** with bugbear, pyupgrade, isort, simplify, ruff, pytest-style, comprehensions, bandit
  and pylint errors and warnings; each ignored rule says why in `pyproject.toml`. `ruff format`
  is not used: adopting it would rewrite about 1,500 lines for no behavior change.
- **mypy** checks the bodies of untyped functions; tighten it module by module.
- **What users install is tested,** not just `src/`: the whole suite runs against the installed
  wheel, and the sdist is installed and run on its own. A release uploads the built files and its
  notes before any test code runs, then publishes exactly those files, only if the tests pass.
- **Contract fixtures** (`tests/fixtures/contract/`) are real GitHub API replies captured once from
  public repos (GitHub's own documented examples for the private ones: notifications and pending
  deployments), trimmed, with synthetic names. They keep the parsers honest against real shapes,
  not hand-made ones. They do not notice GitHub changing later: recapture them when it does.
- **Workflows** pin every action to a commit SHA, give each job only the permissions it needs, and
  are linted by zizmor. Dependabot updates the pinned actions and the dev tools weekly, taking a
  release only once it is a week old.

Occasional, by hand: `uvx mutmut run` on `core.py` to find assertions that do not really check,
and `uv run --with pytest-randomly pytest -p randomly` to find tests that depend on order. Hypothesis is a candidate if a
real parser grows.

## Out of scope

Anything that writes to GitHub, a daemon or web UI, GraphQL, YAML/TOML config, rule packs fetched
from the network, email. A pack for another project's command workflow is a welcome first PR.

## Adding a rule pack

1. Write `src/gh_upstream_watch/packs/<id>.json`, with the keys in the README's
   [rule pack schema](https://github.com/colinmcnamara/gh-upstream-watch/blob/main/README.md#rule-packs).

   Check it with `gh-upstream-watch check-pack path/to/pack.json`: it validates the file and prints
   what each gate trusts, which titles are quiet, and whether it replaces a bundled pack.

2. Add a test with a synthetic fixture that shows the alert firing, and one that shows an
   unauthorized or unrelated comment staying silent. `tests/test_core.py` has examples.
3. Anything packs cannot express belongs in a `--hook` script, not in the engine.

### Worked example: a Kubernetes (prow) pack

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

Label changes are also reported generically (`labels: +lgtm`), so a canceled `/lgtm` shows up as
`labels: -lgtm`. This exact block is checked by `tests/test_extras.py`.

## The state file

One JSON object (`schema` 1), written atomically, mode 0600; a run holds an exclusive lock while it
works. New keys are additive; `state.load` quarantines a file it cannot read, or whose core keys
(the lists and objects in `state.empty()`, the seen stores, items and outbox entries) have the
wrong type.

| key | holds |
| --- | --- |
| `items` | `owner/repo#n` -> fingerprint (`core.fingerprint`): title, state, labels, assignees, comment ids (`max_comment_id`, `human_ids`, `mention_ids`), gates, cross-references, reviews, `_seen` |
| `seeded_sources` | sources that have had one complete run: `repo:owner/repo`, `extra:owner/repo#n`, `claim:owner/repo`, `notifications` |
| `seeded` | true once every configured source is in `seeded_sources` |
| `retry` | new items whose first fetch failed -> first failure time; retried until they get a baseline, for up to `baseline_days` |
| `unknown_streak` | source -> `{since, runs}` while it keeps failing; drives the escalation alert |
| `notifications.seen`, `claimable.seen` | ids already alerted, pruned after `retention_days` once GitHub stops returning them |
| `slack` | the optional Slack source's own seen ids and failure count |
| `outbox` | alerts not yet delivered to every destination: `{alert, pending, attempts}` |
| `last_complete`, `last_unknown` | when every GitHub check and the hook last succeeded (repo pace and Slack do not count), and what the last run could not check |

Alerts carry a source while a run is in progress. `state.hold_until_seeded` holds an alert until its
source is seeded, except `live` (a change against a saved baseline) and `slack` (which seeds
itself). A change to seeding belongs there, with a unit test in `tests/test_state.py`.

## Releasing (maintainers)

1. On a branch: bump the version in `pyproject.toml`, `__version__`, the README's `--pin` and
   SECURITY's verification example (tests check all four agree), and add a dated
   `## [X.Y.Z] - YYYY-MM-DD` section to `CHANGELOG.md`. Date it before the PR, so the merge needs
   no extra commit.
2. Open the PR. `main` requires a PR and the five CI checks (`hygiene` and the four `test (...)`
   jobs: Linux and macOS on Python 3.10 and 3.14), squash only. `gh pr merge N --squash --auto`
   merges when they pass. A change to the CI matrix renames those checks: update the `main`
   ruleset's required checks to the new names in the same PR, and since auto-merge only looks
   again when a check finishes, merge it with `gh pr merge N --squash --match-head-commit SHA`.
3. Tag the merged commit over HTTPS (the `release tags` ruleset protects `v*`; the API path skips
   the "creations being restricted" notice a `git push` of a tag prints):
   `gh api --method POST repos/OWNER/REPO/git/refs -f ref=refs/tags/vX.Y.Z -f sha="$(git rev-parse HEAD)"`.
4. `release.yml` checks that the tag matches both version sources and the CHANGELOG, builds once,
   uploads the files, then runs the whole suite against the built wheel and runs the sdist. It
   publishes to TestPyPI by trusted publishing and installs and runs the package from there, in a
   job that holds no publishing token. Then the `pypi` job waits for a maintainer to approve the
   `pypi` environment (on the run's page, or through the REST API's pending-deployments
   endpoint). Uploads go through `pypa/gh-action-pypi-publish`, which attaches PEP 740
   attestations. PyPI's trusted publisher is pinned to environment `pypi` (TestPyPI's to
   `testpypi`), so no other job can publish. Last, it creates the GitHub Release from the
   CHANGELOG section.
5. A failed release never moves a tag: fix forward with the next patch version.

