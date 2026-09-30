# Changelog

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
