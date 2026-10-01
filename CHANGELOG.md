# Changelog

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
