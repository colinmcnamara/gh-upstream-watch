# Changelog

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
