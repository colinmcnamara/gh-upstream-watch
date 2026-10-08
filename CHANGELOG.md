# Changelog

## [0.4.4] - 2026-10-08

- A semantic-router `/accept` from a maintainer who comments as MEMBER now alerts. The gate also
  passes once the bot's `accepted` label is on, since the bot applies it only after checking the
  commenter's access. Xunzhuo's `/accept` on #4555 never fired the gate before. MEMBER alone still
  does not count, because any org member can type `/accept`. A pack gate can name such a label with
  the new `label` key. The label counts only on issues you filed. A gate that first appears on a
  closed item is recorded without an alert.
- Review bodies and inline review comments are read for mentions. An inline reply naming you after
  your last word is an unanswered mention (semantic-router#4658). Inline comments cost one call a
  run, only on your own open PRs with a human review.
- `inbox` puts your own open item under what waits on you when a maintainer's last comment or
  COMMENTED review came after your last word, even with no @you (Switchyard#855): "maintainer
  replied after you". An approval does not count.
- The merge check behind maintainer touches reads up to 20 of a person's reviewed, merged PRs (was
  5). Switchyard maintainers' own merges sit as deep as 13th.

## [0.4.3] - 2026-10-08

- On Python 3.9 (stock macOS `python3`), the gh extension's error now names the two quick fixes,
  `brew install python` or `uv tool install gh-upstream-watch` (uv brings its own Python), before
  the option to pin v0.3.3.
- A maintainer whose org membership is private now counts as a maintainer. GitHub labels such a
  person CONTRIBUTOR, so `inbox` said "no maintainer touch yet" after one had commented. A CONTRIBUTOR's
  comment or review newer than the last labeled maintainer touch now counts if that person merged one
  of their 5 most recent reviewed PRs in the repo. It is still GET-only: one search plus up to 5 PR
  reads per person, only on your own open work (the only place `inbox` shows it), and none when a
  labeled maintainer spoke last. Each answer is kept in the state file (`mergers`) and asked again
  after a week. Rule-pack gates are unchanged.

## [0.4.2] - 2026-10-05

- Each GitHub Release carries signed build provenance for the wheel and sdist
  (`gh_upstream_watch-X.Y.Z.sigstore.json`; check a file with `gh attestation verify FILE --repo
  colinmcnamara/gh-upstream-watch`). The release's tests now run in their own job, away from the
  signing token. GitHub Releases are immutable from this release on.
- The repository has a Code of Conduct, issue and PR templates, an OpenSSF Scorecard workflow and
  badges, and a "Status and support" section in the README.

## [0.4.1] - 2026-10-05

Exit codes that match the README, from fact-checking the docs against the code:

- A bad `--hook` path (relative, or not executable) exits 2, a config error, instead of 1. If a
  script checks for 1 here, change it to 2.
- `migrate` with a missing or unreadable `--from` file exits 2 with a message, not a traceback.
- A state file from a newer version exits 2 and stays where it is (it is never moved aside).
- `migrate` writes the state file under the same lock a run holds: while a run is in progress it
  exits 3 and writes nothing.
- `init --schedule` on a machine with neither launchd nor systemd refuses an `--interval` cron
  cannot run (one that does not divide 60). `--interval`'s help now says `init --schedule` uses it.

## [0.4.0] - 2026-10-05

Quality and supply chain:

- **Python 3.10 or newer** (3.9 reached end of life in October 2025). On 3.9, install
  `gh-upstream-watch<0.4`.
- Releases carry PEP 740 attestations: PyPI shows each file's signed provenance from this
  repository's release workflow. The TestPyPI smoke test now runs in a job that holds no publishing
  token, and the release tests the exact wheel it publishes.
- A failed webhook delivery (a redirect or a 5xx) no longer leaks its socket.
- The gh extension says plainly when `python3` is older than 3.10 (stock macOS has 3.9).
- Tests refuse to run the real `gh`, `launchctl`, `systemctl`, notifiers or `claude` through
  `subprocess` or `os.system`, with tripwires for child processes, and run with a temporary home
  directory and no GitHub credentials (limits in CONTRIBUTING). Strict pytest (warnings are
  errors, a 30s timeout), a branch-coverage floor, mypy, a wider ruff rule set, workflow linting
  with zizmor, CodeQL (GitHub's code-scanning default setup), contract tests on real GitHub reply
  shapes, and tests against the built wheel and sdist. See CONTRIBUTING, "Quality gates".

## [0.3.3] - 2026-10-05

For a laptop that sleeps:

- Scheduled runs wait for the network: `--wait-network SECONDS` (the scheduler entries pass 180)
  waits for GitHub to answer before the run and skips the run, with one `offline:` log line and no
  state change, if it never does. Before, the first run after wake often met a network that was not
  up yet and called every source unknown.
- Runs every 15 minutes by default (was 30).
- The `stuck` alert is set in time, not runs: `escalate_after_hours` (default 3), so it means the same
  at any interval. A config that still sets `escalate_after_runs` keeps the old run-count rule.
- The launchd agent uses calendar slots (`:00`, `:15`, `:30`, `:45` by default) when the interval divides 60.
  launchd runs a slot missed during sleep once on wake; `StartInterval` did not, so a Mac could go
  hours after waking with no run. Regenerate it with `gh-upstream-watch init --schedule`.
- `init --schedule` checks `--interval` too (0 or a negative number used to get through).

## [0.3.2] - 2026-10-04

From a day of real use:

- `done OWNER/REPO#N`: you handled what waits on you there (a thank-you mention, say). It leaves
  `inbox` until a new reason, mention or review arrives.
- Packs can name CI checks that are red by design (`ci_by_design`): such a failure waits on a
  maintainer, not on you, and raises no `ci_failed`. The new bundled `vllm` pack lists
  `pre-run-check`, red on a first-time contributor's PR until a maintainer starts CI.
- Packs can quiet labels a bot flips back and forth (`quiet_labels`). semantic-router's
  `pr/needs-review` and `pr/needs-rebase` flips no longer alert; the bot adding `pr/needs-rebase`
  still does, once, as NEEDS REBASE.
- `inbox` and `done` read your rule packs (local files), so a broken pack now stops them with the
  same error `run` gives.

## [0.3.1] - 2026-10-04

- `approvals`: list your own repos, and a workflow run waiting on an environment you can approve (a
  release held at a protected `pypi` environment) alerts once per run, and again for a re-run, even
  on a seed run.
- CONTRIBUTING: the release steps as they now run (required checks on `main`, auto-merge, the
  TestPyPI check, the environment approval).

## [0.3.0] - 2026-10-04

Whose move is it: an inbox, CI on your own PRs, and milestones.

- `inbox`: everything waiting on you now (an accepted issue you have not claimed, an unanswered
  mention, red CI, a merge conflict, a draft, changes requested since your last reply), then your own open
  work that waits on them, with days waited, the last maintainer touch, and the time 9 in 10 of the repo's
  recent PRs took to merge, so "too early to nudge" is a number. Reads only the state file; `--json` for scripts.
- CI on your own open PRs: `ci_failed` (names the failing checks), `ci_passed`, and `ci_waiting`
  when GitHub Actions holds a fork's runs for a maintainer's approval. Commit statuses (Buildkite,
  DCO apps) count as well as check runs. A cancelled run is neither a failure nor a pass, and a CI
  lookup that fails leaves the item unknown for the run, never "passed".
- `conflict` when your open PR gets a merge conflict.
- `milestone` replaces `merged` for your own PR ("FIRST MERGE in owner/repo" the first time) and for
  a merged PR by someone else that names you.
- Your own replies on review threads no longer alert as `COMMENTED by @you`.
- Upgrading seeds the new checks quietly: no CI or conflict alert fires until a second reading differs.

## [0.2.3] - 2026-10-03

Resonance: fewer, better alerts, from two days of real use. Replaying the same live data, 0.2.2
raised 40 alerts and 0.2.3 raises 3.

- Mentions say who and what: `@maint mentioned you: "can you rebase on main?"`, linking to the
  comment. GitHub often gives no latest comment, so the thread's recent comments are searched.
- Quiet-title threads stay quiet for notifications too: GitHub calls every later post in a thread you
  were once named in a "mention", so a megathread alerts only when a comment really names you.
- One line per item per run: a notification about an item that also changed joins that item's
  alert. A notification's `key` is now `owner/repo#n`.
- No "referenced by" on quiet threads or on items closed more than 7 days ago. A PR that says it
  closes your item still always alerts.
- A network blip (connection reset, timeout, HTTP 502/503/504) gets one quick retry before the item
  is unknown for the run.
- Hardened by a four-model red team (Opus, Codex, Grok, Sonnet). Every case it cannot verify alerts:
  a discussion, a PR (whose reviews are not read), a full page of comments, a failed or capped
  lookup, or an update GitHub does not explain. Mentions are read since the last update already
  handled (not a fixed window), only strictly newer comments count, and an @you hidden in a quote,
  code or an HTML comment does not. A maintainer's mention is shown before a later troll's, with the
  other names listed; the snippet centers on the @you. Webhook text escapes `&`, `<` and `>` so a
  comment cannot ping a Slack channel or disguise a link. At most 20 mention lookups per run.
  Discussions and releases no longer share an issue's `owner/repo#n` key.
- Fixed (since 0.1.0): "Closes #5, #6" (or "and", "&") now counts as closing every listed item, not
  just the first, so a PR that closes your item always raises the competing-PR alert.

## [0.2.2] - 2026-10-02

Two bugs seen on a live watcher.

- Slack: the check asks Claude Code for structured output (`--json-schema`), so the answer is an
  object with an `items` array and never prose. Before, a model that wrapped its array in prose, or
  answered in prose only, made the Slack source unknown for that run (twice in about 30 hours). If
  `claude` does not support `--json-schema`, the check falls back to the text answer, which may now
  hold its array in exactly one ```json fence inside prose. Prose with no array is still a failure,
  and every item still needs its proof.
- terminal-notifier: the subtitle (the alert title) is escaped like the message when it starts with
  `[`, `(`, `{` or a quote. Unescaped, terminal-notifier exits 0 and shows nothing, so the pop-up was
  lost silently.

## [0.2.1] - 2026-10-01

Fixes from migrating a real watcher off the single-file script.

- Slack messages the old script recorded by channel name (`#channel:ts`) count as seen, so a
  migrated watcher does not repeat them. Before, the first Slack check after migrating could repeat
  every reply from the last `days`.
- `migrate` prints the step that keeps old history from arriving as a burst: one
  `--notify none --webhook '' --no-slack` run (with your `--config` or `--repos`) while the old job
  is still running. The old script read only the
  first 100 timeline events of each item, so the new one finds older references on busy threads.
  The README's migration section walks through the four steps.

## [0.2.0] - 2026-10-01

Craft: the output you read. Minor version because alert wording and the `--json` fields changed.

- Alerts that need you (gates with a call to action, competing PRs, reopens, assignments, asks,
  claims, escalations) come first, in stdout, desktop and webhook alike.
- More than three alerts in one run become one desktop banner ("6 alerts, 5 need action: ...");
  stdout and the webhook still get every alert.
- Natural wording: `1 new comment` / `2 new comments`, `APPROVED by @maint`, `closed` / `reopened`
  (a merged PR says `MERGED` only). A gate already answered is now kind `gate_done`.
- `--json` and webhook alerts gain `v` (1), `action` (needs you) and `ts` (ISO 8601 with offset);
  every field is documented in the README as a stable contract.
- `status` reads like a doctor: setup, schedule (launchd or systemd installed and loaded), state
  with "14 min ago", pending deliveries, and problems with their fix.
- Tests keep the README demo identical to the golden run and every alert kind documented.
- A review requesting changes is its own kind, `changes_requested`, and needs you.
- Batching applies to pop-up backends only; a `$GH_UPSTREAM_WATCH_NOTIFY` command still gets every
  alert. A banner that fails is retried as one banner next run, never as a burst of pop-ups.
- Fixed (since 0.1.0): a failing destination was retried twice per run and dropped after 3 runs
  instead of 5. Alerts an older version left in the outbox get `v`, `action` and `ts` on replay.
- `status` counts undelivered alerts and Slack failures as problems, and does not tell cron users
  on Linux to set up systemd.

## [0.1.3] - 2026-10-01

Structure: easier to change safely.

- Seeding is one function, `state.hold_until_seeded`, with its own unit tests (the logic that
  needed two fixes in 0.1.1).
- `check-pack FILE...` validates rule packs and prints what each gate and claim board trusts,
  which titles are quiet, and whether a pack replaces a bundled one.
- Scheduler entries (`--print-*`, `init --schedule`) live in `schedule.py`.
- One `hint()` gives the next step for every error, in `status`, escalation alerts and login failures.
- A test keeps the README honest: every command in it must parse and every config key must exist.
  It found the `state` key missing from the README's example config.
- CONTRIBUTING documents the state file and the `claimable.authorized_by` pack key.

## [0.1.2] - 2026-09-30

First-run and unattended-use improvements.

- `--dry-run` on a new install prints what the seed run holds back, marked
  `(preview, not sent while seeding)`.
- A source that stays unknown for `escalate_after_runs` runs in a row (default 6) sends one real
  alert with the fix when there is a known one, including when `gh` is signed out. It repeats only
  after the source recovers and fails again.
- `status` prints the fix for each unknown and how long each source has been failing.
- `init --schedule` installs and loads the scheduler: launchd on macOS, a systemd user timer where
  `systemctl` exists, otherwise it prints the cron line.
- `explain OWNER/REPO#N`: the packs, the live fingerprint, the saved one, and what a run would alert.
- `forget OWNER/REPO#N...`: drop items from the state.
- README opens with what the alerts look like.
- CI and release: checkout v7, setup-uv v10, gitleaks-action v3 (Node 24, SHA-pinned); Dependabot
  keeps the pins current; the release installs and runs the TestPyPI build before PyPI gets it.

## [0.1.1] - 2026-09-30

Fixes from a red-team review (Codex and Claude, with reproductions).

- Claim boards: only a board opened by, and rows posted by, an author the pack trusts
  (`claimable.authorized_by`, default OWNER and COLLABORATOR; the bundled vLLM Semantic Router pack
  adds MEMBER) are read, and a row's link must point into the watched repo. Before, anyone could open
  a newer issue with the board's title and send claim alerts.
- Seeding is per source. One check that keeps failing (a typo'd extra, a token without the
  notifications scope) no longer holds back every other alert, and an item with a saved baseline
  always alerts.
- A deleted or no-longer-visible item (404, 410) alerts once as `GONE` and is dropped, instead of
  keeping every later run incomplete.
- New comments are counted by id, so deleting a comment cannot hide a new one. Renaming an issue
  to a quiet title no longer silences it. An edit that turns a known reference into `Closes #n`
  raises the competing-PR alert. A `/assign` posted before the `/accept` no longer counts as done.
- A deleted account (`user: null`) no longer makes an item unknown forever.
- A new item whose first fetch fails is retried until it has a baseline.
- Rate-limited calls wait and retry. A state file with the wrong shape is quarantined. Config value
  types and `owner/repo` names are checked. `--print-cron` rejects intervals it cannot express; the
  printed plist escapes paths.
- Bidi and zero-width characters are stripped from alert text; `notify-send` bodies are escaped.
- `python -m gh_upstream_watch` works. README: launchd, systemd and cron load steps; `--pin` for the
  gh extension.
- Upgrading from 0.1.0: each repo already in the state counts as seeded. One edge is not covered:
  if 0.1.0 watched one issue of a repo as an extra and you now add that whole repo, its other items
  are treated as seeded, so an `/accept` already waiting on one of them alerts once.
- Release: every action pinned to a commit SHA; the tag must match both version sources and the
  CHANGELOG section is checked before any upload.

## [0.1.0] - 2026-09-30

First public release.

- One read-only pass per run over the issues and PRs you are involved in, recently closed ones,
  explicit extras, and unread notifications that ask for you. Every GitHub call is
  `gh api --method GET` through one function.
- Next-action alerts from local JSON rule packs: authorized gates (`/accept` then `/assign`),
  label transitions, quiet megathreads, claim boards, message wording. Bundled packs: `generic`,
  `vllm-semantic-router`. `--hook` for rules a pack cannot express.
- Reliability: full pagination; `incomplete_results`, failed pages and page caps make an item
  unknown instead of advancing state; per-item isolation; seeded only after a complete run;
  exclusive lock; atomic state writes; outbox before delivery; bounded seen stores;
  corrupt-state quarantine; repo-qualified keys.
- Notifiers: stdout (text or `--json`), terminal-notifier, notify-send, osascript,
  `$GH_UPSTREAM_WATCH_NOTIFY` command, `--webhook`.
- Optional Slack source through Claude Code, off by default, with strict JSON and a proof check.
- Delivery per destination with retry from the outbox; osascript text passed only as arguments;
  only github.com / slack.com links are opened; webhooks must be https (http only to localhost).
- `status` doctor; clear first-run errors for a missing or signed-out `gh` and for no repos.
- `migrate` from the v0 single-file state; `--print-plist`, `--print-systemd`, `--print-cron`.
