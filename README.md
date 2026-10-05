# gh-upstream-watch

Read-only alerts for your upstream work: tells you the next action when a maintainer gate, a
competing PR, or a review moves.

```text
acme/widgets#390 Widget spins forever: ACCEPTED by @maint: comment /assign now
acme/widgets#390 Widget spins forever: COMPETING PR #401 (by @monalisa) says Closes #390: check scope before /assign
acme/widgets#388 Document the retry flag: REOPENED: claim it
```

`uv tool install gh-upstream-watch && gh-upstream-watch init --repos OWNER/REPO --schedule` and it
runs every 30 minutes from then on.

If you contribute to projects you do not maintain, the important events are easy to miss in the
notification stream: a maintainer accepts your proposal and now expects you to claim it, someone
else opens a PR that closes the issue you are working on, your closed issue is reopened. This tool
polls the issues and PRs you are involved in, compares each one with the last run, and prints (and
optionally pops up) one line per change, worded as what to do next.

## What makes it different

1. **Next-action alerts.** Rule packs turn generic changes into instructions:
   `ACCEPTED by @maint: comment /assign now`,
   `COMPETING PR #401 (by @monalisa) says Closes #390: check scope before /assign`,
   `REOPENED: claim it`. A gate only counts when the commenter is authorized
   (a login allowlist, or a maintainer author association), so a drive-by `/accept` is ignored.
2. **Read-only by construction.** Every built-in GitHub call goes through one function that runs
   `gh api --method GET`. The tool itself never comments, labels, assigns or pushes. (A `--hook`,
   a `$GH_UPSTREAM_WATCH_NOTIFY` command or a `--webhook` you configure is your own code or
   endpoint and can do anything; see Trust below.)
3. **Never silently current.** A failed page, a timeout, or a search GitHub marks
   `incomplete_results` makes that item *unknown for this run*: its saved state is not advanced,
   so the change alerts on the next good run instead of being swallowed.
4. **Quiet by default.** The first run sends no alerts; it prints one "seed run" line to stderr. Bots are filtered from comment counts.
   Megathreads you list (for example `[Community]` issues) alert only on comments that `@`-name you.

## Demo

Two runs against recorded, synthetic GitHub data (`scripts/demo.sh`; no network, and the stand-in
`gh` refuses anything but GET). You are `@octocat`. The first run seeds; between the runs a
collaborator posts comment 101 (`/accept`, on the second page of comments; it alerts as the gate,
not again as "1 new comment"), a bot comments,
`@monalisa` opens a PR that says `Closes: #390`, your closed issue is reopened, your PR is approved
(and drops out of search results, but it is still open, so it stays watched), and someone names
you in a megathread, and a review request arrives. A mention in `acme/gadgets` is ignored because
notifications follow `--repos`. This is the real output:

```text
$ gh-upstream-watch --repos acme/widgets --packs-dir tests/fixtures/packs --notify none
2026-09-30 08:24 seed run: watching 4 item(s); 1 alert(s) suppressed. All sources seeded; alerts start next run.

$ gh-upstream-watch --repos acme/widgets --packs-dir tests/fixtures/packs --notify none
[2026-09-30 08:24] acme/widgets#388 Document the retry flag: REOPENED: claim it (https://github.com/acme/widgets/issues/388)
[2026-09-30 08:24] acme/widgets#390 Widget spins forever on an empty config: ACCEPTED by @maint: comment /assign now (https://github.com/acme/widgets/issues/390)
[2026-09-30 08:24] acme/widgets#390 Widget spins forever on an empty config: COMPETING PR #401 (by @monalisa) says Closes #390: check scope before /assign (https://github.com/acme/widgets/pull/401)
[2026-09-30 08:24] acme/widgets#395 [Community] Weekly sync thread: 1 comment naming you (https://github.com/acme/widgets/issues/395)
[2026-09-30 08:24] acme/widgets#14 Tighten lint config: someone requested your review (https://github.com/acme/widgets/pull/14)
[2026-09-30 08:24] acme/widgets#392 Add retry backoff: APPROVED by @maint (https://github.com/acme/widgets/pull/392)
```

The same two runs are a golden test (`tests/test_run.py`), and `tests/test_docs.py` keeps this
transcript identical to it. Alerts that need you come first; information (here, a review) follows.

## Install

Requires the [GitHub CLI](https://cli.github.com) (`gh auth login` done) and Python 3.9 or newer.
No other dependencies.

```sh
uv tool install gh-upstream-watch
# or
pipx install gh-upstream-watch
# or, as a gh extension (runs from a checkout with your python3):
gh extension install colinmcnamara/gh-upstream-watch --pin v0.1.1   # then: gh upstream-watch --help
```

`--pin` holds the extension at a release tag; without it, `gh extension upgrade` runs whatever is on
the default branch. `python -m gh_upstream_watch` works too.

## Use

```sh
gh-upstream-watch init --repos acme/widgets       # writes ~/.config/gh-upstream-watch/config.json
gh-upstream-watch --dry-run                       # one pass, print only, save nothing
gh-upstream-watch                                 # one pass: alerts, then save state
gh-upstream-watch status                          # what the state file knows, and how to fix what failed
gh-upstream-watch inbox                           # whose move it is on each open item (--json for scripts)
gh-upstream-watch done acme/widgets#390           # handled: off your inbox until something new happens
gh-upstream-watch explain acme/widgets#390        # what the tool sees for one item, and what it would alert
gh-upstream-watch forget acme/widgets#390         # stop watching an item (it seeds again if search finds it)
```

On a new install, `--dry-run` also prints what the seed run would hold back, marked
`(preview, not sent while seeding)`, so you can see what you will get before anything is saved.

`init --schedule` (with `--repos`, or on its own once a config exists) installs and loads the
scheduler for you: a launchd agent on macOS, a systemd user timer where `systemctl` exists, and
otherwise it prints the cron line to add. To do it by hand instead, the tool prints a ready
scheduler entry that points at
the interpreter and script you ran it with:

```sh
# macOS: write the agent, then load it
gh-upstream-watch --print-plist > ~/Library/LaunchAgents/local.gh-upstream-watch.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.gh-upstream-watch.plist

# Linux: save the two printed units under ~/.config/systemd/user/, then
gh-upstream-watch --print-systemd
systemctl --user daemon-reload && systemctl --user enable --now gh-upstream-watch.timer

# cron (intervals 1-59 minutes): add the printed line with `crontab -e`
gh-upstream-watch --print-cron
```

What one pass checks:

- every open issue and PR in `repos` you are involved in (author, assignee, commenter, mentioned),
  those closed in the last 14 days, `extras` (`owner/repo#n`), and anything already watched that
  is still open. The saved state of a closed item is kept for `baseline_days` (90) after it was
  last checked, so a reopen within that time alerts; after that it counts as new and seeds quietly;
- unread GitHub notifications that mention you, request your review, or assign you, in the watched
  repos (exact names; set `notification_repos` to `["*"]` for every repo, or to globs). This needs a
  token with the `notifications` scope: `gh auth refresh -s notifications` if they come back 403;
- claim boards defined by a pack (an issue that lists claimable tasks), for `claim_groups`. Only a
  board opened by, and rows posted by, an author the pack's `claimable.authorized_by` trusts are read
  (OWNER and COLLABORATOR by default), and a row's link must point into the watched repo;
- an item that is deleted or no longer visible to you (404 or 410) alerts once as `GONE` and is
  dropped from the watch list; it does not keep the run incomplete.
- on your own open PRs: CI (check runs, commit statuses, and a fork's workflow runs waiting for a
  maintainer to approve them) and merge conflicts. A merge of your PR is a `milestone`, and your
  first merge in a repo says so; so is a merged PR by someone else that names you (a roster PR);
- for each repo in `approvals` (your own, say): a workflow run waiting on an environment you can
  approve, such as a release held at a protected `pypi` environment. It alerts once per run (again
  for a re-run), even on a seed run, since it is waiting on you right now.

`inbox` reads only the saved state, so it is instant and makes no GitHub calls. It lists what waits
on you (an accepted issue not yet claimed, an unanswered mention, red CI, a merge conflict, a draft,
changes requested since your last reply), then what waits on them: your own open work, how long it has
waited, when a maintainer last touched it, and how long 9 in 10 of the repo's last 100 merged PRs
took from open to merge (refreshed daily), so you can see when it is still too early to nudge.

If a source stays unknown for `escalate_after_runs` runs in a row (6, about three hours at the
default interval), one real alert goes out through your notifiers, with the fix when there is a known
one (for example `gh auth refresh -s notifications`), since a scheduled job's stderr is easy to
miss. It does not repeat until that source has recovered and failed again.

Exit status: 0 when every check completed, 1 when something was unknown this run (logged as
`unknown this run`; `gh` missing or signed out also lands here, with the fix printed), 2 for a
config, argument or rule-pack error (including no repos configured), 3 when another run holds the
state lock. `status` is a doctor: it checks `gh` and your login, shows the config path, the packs
applied to each repo, the last complete run, and what the last run could not check.

## Configuration

JSON, at `$XDG_CONFIG_HOME/gh-upstream-watch/config.json` (default `~/.config/...`). Precedence:
command line, then environment (`GH_UPSTREAM_WATCH_CONFIG`, `GH_UPSTREAM_WATCH_REPOS` comma list,
`GH_UPSTREAM_WATCH_STATE`), then the file. Unknown keys are errors.

```json
{
  "repos": ["acme/widgets", "acme/gadgets"],
  "extras": ["octo-org/octo-repo#12"],
  "state": null,
  "notify": "auto",
  "webhook": null,
  "hook": null,
  "packs_dirs": [],
  "claim_groups": [],
  "recent_closed_days": 14,
  "retention_days": 30,
  "baseline_days": 90,
  "notification_repos": null,
  "login": "octocat",
  "bots": [],
  "escalate_after_runs": 6,
  "approvals": [],
  "slack": {"enabled": false}
}
```

`state: null` keeps the state file at `$XDG_STATE_HOME/gh-upstream-watch/state.json`
(default `~/.local/state/...`); it is written mode 0600.

A mention says who and what (`@maint mentioned you: "can you rebase on main?"`) and links to the
comment. On a quiet-title thread (a megathread), a mention alerts only when a comment really names
you: GitHub keeps calling every later post in a thread you were once named in a "mention". A
notification about an item that also changed this run joins that item's line instead of adding a
second one. "Referenced by" alerts stop on quiet threads and on items closed more than 7 days ago; a
PR that says it closes your item always alerts. A network blip gets one quick retry before an item
counts as unknown.

Notifications: stdout always (text with the URL, or `--json` for JSON lines). When more than three
alerts arrive in one run, the desktop gets one banner ("6 alerts, 5 need action: ...") instead of
six; stdout and the webhook still get every alert. `notify: auto` uses
`$GH_UPSTREAM_WATCH_NOTIFY` (a command that gets the alert JSON on stdin) if set, else
`terminal-notifier`, `notify-send`, or `osascript` (macOS; the URL is kept in the visible text,
since those banners cannot open a link; the text is passed as arguments, never as script).
`--webhook URL` also POSTs `{"text": ..., "alert": ...}`; it must be https (http only to
localhost), and only a 2xx answer counts; a redirect is a failure and is not followed. Each alert is delivered to each destination separately; a destination that fails
stays in the outbox and is retried on later runs (up to 5), without printing the alert again.
Only `https://github.com/` links (and `https://*.slack.com/` for Slack) are passed to a notifier
to open; a link from comment text that points elsewhere is replaced by the item's own URL.

### `--json` and webhook fields

One JSON object per alert (the webhook sends it as `alert`). These fields are stable within
`"v": 1`; a change to them bumps `v`.

| field | meaning |
| --- | --- |
| `v` | shape version, `1` |
| `kind` | `gate` (call to action), `gate_done`, `competing_pr`, `reference`, `reopened`, `state`, `assigned`, `label_rule`, `labels`, `merged`, `milestone`, `review`, `changes_requested`, `ci_failed` (call to action), `ci_passed`, `ci_waiting`, `conflict` (call to action), `approval` (call to action), `comments`, `mentions`, `notification`, `claimable`, `gone`, `stuck`, `slack`, or a hook's own kind |
| `action` | `true` when it needs you to do something (gates, competing PRs, reopens, requested changes, assignments, asks, claims, escalations) |
| `key` | `owner/repo#n` (a notification about a repo rather than an item: `owner/repo`), the source for `stuck` |
| `title`, `message` | the human text; the text line is `title: message (url)` |
| `url` | a `https://github.com/` or `https://*.slack.com/` link, or empty |
| `ts` | ISO 8601 with the UTC offset, for machines |
| `time` | local `YYYY-MM-DD HH:MM`, for people |

## Rule packs

Packs are local JSON data, loaded from the bundled `packs/`, then
`~/.config/gh-upstream-watch/packs/`, then any `--packs-dir`. A pack with the same `id` replaces
an earlier one. Packs are never read from a watched repository: the repo you are watching must not
be able to decide what you are told. Bundled: `generic` (wording for every repo),
`vllm-semantic-router` (the `/accept` then `/assign` flow of `vllm-project/semantic-router`; its
`pr/needs-*` label flips are quiet, but the bot adding `pr/needs-rebase` alerts) and `vllm` (`pre-run-check` is red by design on a first-time
contributor's PR).

```json
{
  "id": "acme",
  "repos": ["acme/*"],
  "gates": [{
    "id": "accept",
    "comment": "^/accept\\b",
    "authorized_by": {"associations": ["MEMBER", "OWNER"], "logins": []},
    "then": "^/assign\\b",
    "alert": "ACCEPTED by @{actor}: comment /assign now",
    "alert_done": "ACCEPTED by @{actor}"
  }],
  "label_transitions": [{"id": "stuck", "assigned_to_me": true, "has": ["accepted"], "lacks": ["in-progress"],
                         "alert": "in-progress label missing"}],
  "quiet_titles": ["^\\[Community\\]"],
  "quiet_labels": ["^pr/needs-"],
  "ci_by_design": {"pre-run-check": "red until a maintainer starts CI"},
  "messages": {"reopened": "REOPENED: claim it",
               "competing_pr": "COMPETING PR #{number} (by @{author}) says Closes #{target}: check scope"}
}
```

A gate matches only at the start of a comment (not a quoted line further down). If a gate lists
`logins`, only those logins count. Otherwise only its `associations` count, `OWNER` and
`COLLABORATOR` by default; `MEMBER` is not a default because an organization member can have
read-only access. Author association alone cannot prove who may run a gate, so add the
maintainers' logins to your copy of a pack when you know them. On first sight of an item, gates
are checked too: an `/accept` already waiting alerts once. `then` is your own follow-up command; once
you have posted it after the gate comment, the alert drops the call to action. `quiet_titles` apply
only when the title was quiet before the change as well, so renaming an issue cannot silence it.
`quiet_labels` are labels a bot flips back and forth: adding or removing them alone does not alert
(label rules still see them). `ci_by_design` names checks that fail on purpose until a maintainer
acts; such a failure is "CI waits for a maintainer", under waiting on them, not `ci_failed`. See `CONTRIBUTING.md` for the full schema and how to add a pack with a fixture.

For anything a pack cannot express, `--hook /abs/path` runs your program once per
watched item that has a previous fingerprint (no shell, 30 s timeout) with `{"key", "repo", "number", "me", "old", "new"}` on
stdin; each stdout line `{"message": "...", "kind": "...", "url": "..."}` becomes an alert.

## Reliability

- Full pagination for search, comments, reviews, timeline and notifications. A search over
  GitHub's 1,000-result cap is reported as unknown with a warning (no partitioning yet).
- One failing item never loses the run; the state file is always saved.
- The first run is silent. Each source (a repo's search, an extra, notifications, a claim board)
  seeds on its own first complete run, so one check that keeps failing never holds back the rest,
  and an item with a saved baseline always alerts. `status` lists the sources still seeding.
- New comments are found by comment id, not by count, so a deleted comment cannot hide a new one.
- A new item whose first fetch fails is retried on later runs until it has a baseline.
- Rate-limited calls wait 60 seconds and retry, at most twice.
- State writes are atomic (temp file, fsync, rename) under an exclusive lock. A corrupt state file
  is moved aside and the next run re-seeds quietly.
- Alerts are written to an outbox in the state file before delivery; a crash in between
  re-delivers them on the next run (at least once; a duplicate is possible, a loss is not).
- The state file is written mode 0600 (it holds titles and URLs from private repos).
- Seen-id stores are pruned after `retention_days`, only after a complete pass, and never for an
  id GitHub still returns.

Known limits: polling cannot see an event GitHub never exposes, or a state that changes twice
between polls. Notification asks use GitHub's `participating` filter, so an ask GitHub does not
count as participating is not seen there (the issue and PR checks still run).

## Trust

The tool only reads GitHub. Three things you configure are trusted local code or endpoints:
`--hook` runs a program with your permissions, `$GH_UPSTREAM_WATCH_NOTIFY` runs a command, and
`--webhook` sends every alert (titles, logins, URLs) to that URL. Rule packs are data, but only
load packs you have read. A failing hook makes that item unknown for the run, so nothing is lost.

## Slack (optional, off by default)

`--slack` (or `"slack": {"enabled": true}`) adds Slack mentions and replies in your threads. Read
this before enabling it:

- It needs [Claude Code](https://docs.anthropic.com/en/docs/claude-code) and the official Slack
  plugin, already signed in. Each check is one headless `claude -p` call with a small model
  (`haiku` by default), no built-in tools (`--tools ""`), only the Slack search and read-thread
  tools allowed, no user or project settings, an empty temporary working directory, and a minimal
  environment (no `GH_TOKEN`). That call costs money or plan usage; `every_minutes` (default 25)
  throttles it.
- Model output is untrusted. The check asks for structured output (`claude -p --json-schema`), an
  object whose `items` array holds the messages; with an older `claude` the text answer must be a JSON
  array, bare or in one ```json fence. Each item must carry a
  Slack channel id, a message ts and the text, plus proof: the raw `<@you>` tag for a mention, or
  the ts of your own message for a thread reply. Items without proof, outside `channels` (when
  set), or older than `days` are dropped. Be clear about what that proves: the proof fields are
  themselves model output, so a prompt injection in a Slack message can forge them. The damage is
  limited because the model has no tools except two Slack read tools, and only
  `https://*.slack.com/` links are ever passed to a notifier.
- Slack failures are counted in their own state (`status` shows them) and never make the GitHub
  pass incomplete or block it.
- Nothing is shipped: `user`, `query` and `channels` come from your config. The query is a prompt
  with `{user}` and `{days}` placeholders; it must tell the model to output the fields above:

```json
"slack": {
  "enabled": true,
  "user": "UEXAMPLE1",
  "days": 14,
  "every_minutes": 25,
  "channels": [],
  "query": "You are a read-only Slack checker for user {user}. Search for messages from the last {days} days that mention <@{user}>, and replies to threads {user} posted in. Output ONLY a JSON array; each element: {{\"channel\": \"#name\", \"channel_id\": \"C...\", \"ts\": \"...\", \"author\": \"...\", \"kind\": \"mention\" or \"thread_reply\", \"your_ts\": \"ts of {user}'s own message in the thread, else empty\", \"evidence\": \"the raw mention text, e.g. <@{user}|name>, else empty\", \"text\": \"first 120 characters\", \"link\": \"permalink or empty\"}}. If there is nothing, output []."
}
```

## Migrating from the single-file script

`gh-upstream-watch migrate --from OLD_STATE.json --state NEW_STATE.json --claim-repo owner/repo`
converts a v0 state file (one flat dict of fingerprints plus `_notifications`, `_slack`,
`_claimable`). Fingerprints and seen ids are kept; Slack messages the old script keyed by channel
name still count as seen.

The old script read only the first 100 timeline events of each item, so on a busy thread the new
one finds references the old one never saw. To keep that history from arriving as one burst:

1. Leave the old scheduled job running.
2. Run `gh-upstream-watch --state NEW_STATE.json --notify none --webhook '' --no-slack` once (add the
   same `--config` or `--repos` you use). It records that history without notifying anyone; the old job still alerts on anything new.
3. `gh-upstream-watch --dry-run` should now be as quiet as the old job.
4. Unload the old job and `gh-upstream-watch init --schedule`.

## License

MIT. See `LICENSE`.
