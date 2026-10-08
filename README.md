# gh-upstream-watch

[![PyPI](https://img.shields.io/pypi/v/gh-upstream-watch)](https://pypi.org/project/gh-upstream-watch/)
[![Python](https://img.shields.io/pypi/pyversions/gh-upstream-watch)](https://pypi.org/project/gh-upstream-watch/)
[![CI](https://github.com/colinmcnamara/gh-upstream-watch/actions/workflows/ci.yml/badge.svg)](https://github.com/colinmcnamara/gh-upstream-watch/actions/workflows/ci.yml)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/colinmcnamara/gh-upstream-watch/badge)](https://scorecard.dev/viewer/?uri=github.com/colinmcnamara/gh-upstream-watch)
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/15235/badge)](https://www.bestpractices.dev/projects/15235)
[![License: MIT](https://img.shields.io/pypi/l/gh-upstream-watch)](https://github.com/colinmcnamara/gh-upstream-watch/blob/main/LICENSE)

Read-only alerts for your upstream work: tells you the next action when a maintainer gate, a
competing PR, or a review moves.

```text
acme/widgets#390 Widget spins forever: ACCEPTED by @maint: comment /assign now
acme/widgets#390 Widget spins forever: COMPETING PR #401 (by @monalisa) says Closes #390: check scope before /assign
acme/widgets#388 Document the retry flag: REOPENED: claim it
```

If you contribute to projects you do not maintain, the important events are easy to miss in the
notification stream: a maintainer accepts your proposal and now expects you to claim it, someone
else opens a PR that closes the issue you are working on, your closed issue is reopened. This tool
polls the issues and PRs you are involved in, compares each one with the last run, and prints one
line per change, worded as what to do next, with a desktop banner (`--notify none` turns it off).

Out of the box it reports reopens, assignments, competing PRs, reviews, CI on your PRs, merge
conflicts and mentions. A project's maintainer commands, such as `/accept`, need a
[rule pack](#rule-packs): one ships for `vllm-project/semantic-router`, and writing one for your
project is a few lines of JSON.

```sh
gh auth login && gh auth refresh -s notifications   # once: the GitHub CLI, with notifications
uv tool install gh-upstream-watch                   # Python 3.10 or newer; no other dependencies
gh-upstream-watch init --repos OWNER/REPO --schedule  # then a run every 15 minutes
```

## What makes it different

1. **Next-action alerts.** Rule packs turn generic changes into instructions like the ones above.
   A gate only counts when the commenter is authorized (a login allowlist, or a maintainer author
   association), so a drive-by `/accept` is ignored.
2. **Read-only by construction.** Every GitHub call is built in one module (`github.py`) as
   `gh api --method GET`; the tool never comments, labels, assigns or pushes. A `--hook`, a
   `$GH_UPSTREAM_WATCH_NOTIFY` command or a `--webhook` you configure is your own code or endpoint
   and can do anything. The threat model, how to verify a release, and how to report a
   vulnerability are in [SECURITY](https://github.com/colinmcnamara/gh-upstream-watch/blob/main/SECURITY.md).
3. **Never silently current.** A failed page or a timeout makes that item *unknown for this run*:
   its saved state is not advanced, so the change alerts on the next good run instead of being
   swallowed. A search GitHub marks `incomplete_results` is unknown the same way, and seeds on a
   later complete run.
4. **Quiet by default.** The first run only records what it sees (one "seed run" line on stderr);
   the one exception is a release waiting for your approval, which cannot wait. Bots are filtered
   from comment counts, and megathreads you list (for example `[Community]` issues) alert only on
   comments that `@`-name you, or on a gate.

## Demo

Two runs against recorded, synthetic GitHub data, from a checkout (`scripts/demo.sh`; no network,
and the stand-in `gh` refuses anything but GET). You are `@octocat`. Between the runs a
collaborator accepts your issue, `@monalisa` opens a PR that closes it, your closed issue is
reopened, your PR is approved, someone names you in a megathread, and a review request arrives:

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

Alerts that need you come first; information (here, a review) follows.

## Install

Requires the [GitHub CLI](https://cli.github.com), signed in, and Python 3.10 or newer (on 3.9,
`pip install "gh-upstream-watch<0.4"`).

```sh
uv tool install gh-upstream-watch
# or
pipx install gh-upstream-watch
# or, as a gh extension (runs from a checkout with your python3, 3.10 or newer):
gh extension install colinmcnamara/gh-upstream-watch --pin v0.4.4   # then: gh upstream-watch --help
```

`--pin` holds the extension at a release tag; without it, `gh extension upgrade` runs whatever is on
the default branch. `python -m gh_upstream_watch` works too.

## Commands

```sh
gh-upstream-watch init --repos acme/widgets       # writes ~/.config/gh-upstream-watch/config.json
gh-upstream-watch --dry-run                       # one pass, print only, save nothing
gh-upstream-watch                                 # one pass: alerts, then save state
gh-upstream-watch status                          # what the state file knows, and how to fix what failed
gh-upstream-watch inbox                           # whose move it is on each open item (--json for scripts)
gh-upstream-watch done acme/widgets#390           # handled: out of "waiting on you" until something new happens
gh-upstream-watch explain acme/widgets#390        # what the tool sees for one item, and what it would alert
gh-upstream-watch forget acme/widgets#390         # stop watching an item (it seeds again if search finds it)
gh-upstream-watch check-pack my-pack.json         # validate a rule pack and say what it trusts
```

On a new install, `--dry-run` also prints what the seed run would hold back, marked
`(preview, not sent while seeding)`, so you can see what you will get before anything is saved.

### Run it on a schedule

`init --schedule` (with `--repos`, or on its own once a config exists) installs and loads the
scheduler: a launchd agent on macOS, a systemd user timer where `systemctl` exists, and otherwise
the cron line to add. `--interval N` sets the minutes between runs (default 15). Each scheduled
run gets only a `PATH`, not your shell's other variables: a `$GH_UPSTREAM_WATCH_NOTIFY` command
must be set in the plist, unit or cron line itself.

| scheduler | interval | logs |
| --- | --- | --- |
| launchd (macOS) | any; a divisor of 60 runs at fixed minutes (`:00`, `:15`, ...) | `~/Library/Logs/gh-upstream-watch.log` |
| systemd (Linux) | any | `journalctl --user -u gh-upstream-watch` |
| cron | a divisor of 60 (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30) | `~/.local/state/gh-upstream-watch/cron.log` |

To do it by hand, print an entry that points at the interpreter and script you ran:

```sh
# macOS: write the agent, then load it
gh-upstream-watch --print-plist > ~/Library/LaunchAgents/local.gh-upstream-watch.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.gh-upstream-watch.plist

# Linux: save the two printed units under ~/.config/systemd/user/, then
gh-upstream-watch --print-systemd
systemctl --user daemon-reload && systemctl --user enable --now gh-upstream-watch.timer

# cron: add the printed line with `crontab -e`
gh-upstream-watch --print-cron
```

### Sleep and missed runs

Scheduled runs pass `--wait-network 180`: right after wake they wait up to three minutes for
GitHub to answer, and if it never does, the run is skipped with one `offline:` log line and no
change to the state, so nothing is called unknown. A launchd slot missed during sleep runs once on
wake; the systemd timer and cron run within one interval of wake. The next run reads
notifications and changes since the last one, so a gap of up to `recent_closed_days` (14) loses
nothing. After a longer gap, an item opened and closed in between can be missed.

### What a run checks

- every open issue and PR in `repos` you are involved in (author, assignee, commenter, mentioned),
  closed ones updated in the last `recent_closed_days`, `extras` (`owner/repo#n`), and anything
  already watched that is still open. A closed item's saved state is kept for `baseline_days`
  (90) after it was last checked, so a reopen in that time alerts; after that it seeds quietly;
- unread GitHub notifications that mention you, request your review, or assign you, in the repos
  of `repos` and `extras` (`notification_repos` widens or narrows that). This needs the
  `notifications` scope: `gh auth refresh -s notifications` if they come back 403;
- claim boards defined by a pack (an issue that lists claimable tasks), for `claim_groups`. Only a
  board opened by, and rows posted by, an author the pack's `claimable.authorized_by` trusts are
  read (OWNER and COLLABORATOR by default); a row whose link points outside the watched repo links
  to the board instead;
- an item that is deleted or no longer visible to you (404 or 410) alerts once as `GONE` and is
  dropped from the watch list (one you listed in `extras` is still checked each run); it does not
  keep the run incomplete;
- on your own open PRs: CI (check runs, commit statuses, and a fork's workflow runs waiting for a
  maintainer to approve them) and merge conflicts. A merge of your PR is a `milestone`, and your
  first merge in a repo says so; so is a merged PR by someone else that names you (a roster PR);
- for each repo in `approvals` (your own, say): a workflow run waiting on an environment you can
  approve, such as a release held at a protected `pypi` environment. It alerts once per run (again
  for a re-run), even on a seed run.

### The inbox

`inbox` reads only the saved state, so it is instant and makes no GitHub calls. It lists what waits
on you (an accepted issue not yet claimed, an unanswered mention in a comment, a review or an inline
review comment, red CI, a merge conflict, a draft,
changes requested since your last reply, or a maintainer's reply after your last word), then what waits on them: your own open work, how long it
has waited, when a maintainer last touched it, and how long 9 in 10 of the repo's merged PRs took
from open to merge (from up to 100 most recently updated) (refreshed daily), so you can see when it is still too
early to nudge. A maintainer is an owner, member or collaborator, or a contributor who has merged a PR
there: GitHub labels a maintainer whose org membership is private a contributor.

### Failures and exit codes

A scheduled job's stderr is easy to miss, so when a source stays unknown for
`escalate_after_hours` (3 by default, at any run interval, and two runs at least, so one blip never
alerts), one real alert goes out through your notifiers, with the fix when there is a known one
(for example `gh auth refresh -s notifications`). It does not repeat until that source has
recovered and failed again. An older config's `escalate_after_runs` still works: it alerts after
that many failing runs in a row instead.

`status` is a doctor: it checks `gh` and your login, shows the config path, the packs applied to
each repo, the last complete run, and what the last run could not check.

| exit | meaning |
| --- | --- |
| 0 | every check completed (or the run was skipped offline under `--wait-network`) |
| 1 | something was unknown this run (logged as `unknown this run`; `gh` missing or signed out lands here, with the fix printed) |
| 2 | a config, argument or rule-pack error, including no repos configured |
| 3 | another run holds the state lock |

## Configuration

JSON, at `$XDG_CONFIG_HOME/gh-upstream-watch/config.json` (default `~/.config/...`); `init`
writes one. Precedence: command line, then environment (`GH_UPSTREAM_WATCH_CONFIG`,
`GH_UPSTREAM_WATCH_REPOS` comma list, `GH_UPSTREAM_WATCH_STATE`), then the file. Unknown keys are
errors. Every key, with its default (a test checks both against the code):

| key | default | meaning |
| --- | --- | --- |
| `repos` | `[]` | repos to search for issues and PRs you are involved in (`owner/repo`) |
| `extras` | `[]` | specific items to watch as well (`owner/repo#n`) |
| `state` | `null` | state file; `null` is `$XDG_STATE_HOME/gh-upstream-watch/state.json` (default `~/.local/state/...`), written mode 0600 |
| `notify` | `"auto"` | desktop notifier: `auto`, `terminal-notifier`, `notify-send`, `osascript`, `command` or `none` (see [Alerts](#alerts)) |
| `webhook` | `null` | an https URL that gets every alert as JSON (http only to localhost) |
| `hook` | `null` | absolute path of your own program, run for each watched item (see [Rule packs](#rule-packs)) |
| `packs_dirs` | `[]` | more rule-pack directories, read after the bundled packs and `~/.config/gh-upstream-watch/packs/` |
| `claim_groups` | `[]` | claim-board groups to watch, as defined by a pack's `claimable` |
| `recent_closed_days` | `14` | closed items updated within this many days are still searched |
| `retention_days` | `30` | how long seen ids are kept (pruned only after a complete run) |
| `baseline_days` | `90` | how long a closed item's saved state is kept, so a reopen still alerts |
| `notification_repos` | `null` | repos whose notifications count: `null` means the repos of `repos` and `extras`; `["*"]` means every repo; globs work |
| `login` | `null` | your GitHub login; `null` asks `gh api user` |
| `bots` | `[]` | more logins to treat as bots, besides `[bot]` accounts and a built-in few (`mergify`, `dependabot`, ...) |
| `escalate_after_hours` | `3` | how long a source may stay unknown before one alert says so |
| `approvals` | `[]` | your own repos: a run waiting on an environment you can approve alerts |
| `slack` | `{"enabled": false}` | the optional Slack source (see [Slack](#slack-optional-off-by-default)) |

## Alerts

Each alert goes to every destination you have, separately:

- **stdout,** always: one text line with the URL, or a JSON line with `--json`.
- **Desktop** (`notify`): `auto` runs `$GH_UPSTREAM_WATCH_NOTIFY` if set (a command that gets the
  alert JSON on stdin), else `terminal-notifier`, `notify-send` or `osascript`. On `osascript` the
  URL stays in the visible text, since those banners cannot open a link; text is passed as
  arguments, never as script. More than three alerts in one run become one banner ("6 alerts, 5
  need action: ...").
- **Webhook** (`--webhook URL`): a POST of `{"text": ..., "alert": ...}`, https only (http only to
  localhost). Only a 2xx answer counts; a redirect is a failure and is not followed.

A destination that fails keeps the alert in the outbox and tries again on later runs, five
attempts in all, without printing it again. Only `https://github.com/` links (and
`https://*.slack.com/` for Slack) are passed to a notifier to open; a link from comment text that
points elsewhere is replaced by the item's own URL.

A mention says who and what (`@maint mentioned you: "can you rebase on main?"`) and links to the
comment; when that lookup fails, it says "someone mentioned you" and links to the item. On a
quiet-title thread (a megathread), a mention alerts only when a comment really names you, or when
that lookup fails: GitHub keeps calling every later post in a thread you were once named in a
"mention". A notification
about an item that also changed this run joins that item's line instead of adding a second one.
"Referenced by" alerts stop on quiet threads and on items closed more than 7 days ago; a PR that
says it closes your item always alerts.

### `--json` and webhook fields

One JSON object per alert (the webhook sends it as `alert`). These fields are stable within
`"v": 1`; a change to them bumps `v`.

| field | meaning |
| --- | --- |
| `v` | shape version, `1` |
| `kind` | `gate`, `gate_done`, `competing_pr`, `reference`, `reopened`, `state`, `assigned`, `label_rule`, `labels`, `merged`, `milestone`, `review`, `changes_requested`, `ci_failed`, `ci_passed`, `ci_waiting`, `conflict`, `approval`, `comments`, `mentions`, `notification`, `claimable`, `gone`, `stuck`, `slack`, or a hook's own kind |
| `action` | `true` when it needs you to do something: `gate`, `competing_pr`, `reopened`, `assigned`, `label_rule`, `claimable`, `notification`, `mentions`, `slack`, `stuck`, `changes_requested`, `ci_failed`, `conflict`, `approval` |
| `key` | `owner/repo#n` for an item; otherwise `owner/repo TYPE ID` for a notification about something else (a discussion, a release), the source (`stuck`), `owner/repo run ID` (`approval`), or the Slack `channel:ts` |
| `title`, `message` | the human text; the text line is `title: message (url)` |
| `url` | a `https://github.com/` or `https://*.slack.com/` link, or empty; a hook's alerts carry its own |
| `ts` | ISO 8601 with the UTC offset, for machines |
| `time` | local `YYYY-MM-DD HH:MM`, for people |

## Rule packs

Packs are local JSON data, loaded from the bundled `packs/`, then
`~/.config/gh-upstream-watch/packs/`, then any `--packs-dir`. A pack with the same `id` replaces
an earlier one. Packs are never read from a watched repository: the repo you are watching must not
be able to decide what you are told. Bundled: `generic` (wording for every repo),
`vllm-semantic-router` (the `/accept` then `/assign` flow of `vllm-project/semantic-router`; its
`pr/needs-*` label flips are quiet, but the bot adding `pr/needs-rebase` alerts) and `vllm`
(`pre-run-check` is red by design on a first-time contributor's PR).

```json
{
  "id": "acme",
  "repos": ["acme/*"],
  "gates": [{
    "id": "accept",
    "comment": "^/accept\\b",
    "authorized_by": {"associations": ["OWNER", "COLLABORATOR"], "logins": ["maint"]},
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

To alert on your project's own command (say `/approve`), save a copy as
`~/.config/gh-upstream-watch/packs/myproject.json`, set `id`, `repos`, the gate's `comment` and
`alert`, and run `gh-upstream-watch check-pack` on it.

| key | meaning |
| --- | --- |
| `id` | unique name; a later pack with the same id replaces an earlier one |
| `repos` | globs (`owner/*`) this pack applies to; `*` packs apply first |
| `gates` | `{id, comment, authorized_by: {associations, logins}, alert, then?, alert_done?, label?}`: `comment` must start with `^`; `{actor}` in alerts |
| `label_transitions` | `{id, alert, has?, lacks?, assigned_to_me?}`: alerts when the condition becomes true |
| `quiet_titles` | regexes; matching items alert on comments only when they `@`-name you (gates still alert) |
| `quiet_labels` | regexes; adding or removing only these labels does not alert (label rules still see them) |
| `ci_by_design` | `{check name: why}`: checks red by design until a maintainer acts; such a failure is "CI waits for a maintainer", under waiting on them, not `ci_failed` |
| `messages` | wording for `assigned`, `reopened`, `competing_pr`, `reference` (`{number}`, `{author}`, `{kind}`, `{target}`) |
| `claimable` | `{search, title, comment_marker, section, row, alert, authorized_by?}`: a claim-board issue; `row` needs named groups `number`, `title`, `url`; `{group}` in `comment_marker` and `alert` |

A gate matches only at the start of a comment (not a quoted line further down). If a gate lists
`logins`, only those logins count. Otherwise only its `associations` count, `OWNER` and
`COLLABORATOR` by default; `MEMBER` is not a default because an organization member can have
read-only access. Author association alone cannot prove who may run a gate, so list the
maintainers' logins when you know them. A gate's `label` is one a repo's bot applies only after
checking the commenter's access (semantic-router's `accepted`): once it is on an issue you filed,
the latest gate comment counts, whoever wrote it. A gate on a closed item is recorded without an alert. On first sight of an item, gates are checked too: an
`/accept` already waiting alerts once. `then` is your own follow-up command; once you have posted
it after the gate comment, the alert drops the call to action. `quiet_titles` apply only when the
title was quiet before the change as well, so renaming an issue cannot silence it.

For anything a pack cannot express, `--hook /abs/path` runs your program once per watched item
that has a previous fingerprint (no shell, 30 s timeout), with `{"key", "repo", "number", "me",
"old", "new"}` on stdin; each stdout line `{"message": "...", "kind": "...", "url": "..."}` becomes
an alert. A failing hook makes that item unknown for the run, so nothing is lost.

## Reliability

- Full pagination for search, comments, reviews, timeline and notifications. (A mention's lookup
  reads one page of a thread's comments, then falls back to "someone mentioned you".) A search
  over GitHub's 1,000-result cap is reported as unknown with a warning.
- One failing item never loses the run; the state file is always saved.
- Each source (a repo's search, an extra, notifications, a claim board) seeds on its own first
  complete run, so one check that keeps failing never holds back the rest, and an item with a
  saved baseline always alerts. `status` lists the sources still seeding.
- New comments are found by comment id, not by count, so a deleted comment cannot hide a new one.
- A new item whose first fetch fails is retried on later runs until it has a baseline, for up to
  `baseline_days`.
- A network blip gets one quick retry before an item counts as unknown. Rate-limited calls wait
  60 seconds and retry, at most twice.
- State writes are atomic (temp file, fsync, rename), and a run holds an exclusive lock. A state
  file that cannot be read, or whose core keys have the wrong shape, is moved aside and the next
  run re-seeds quietly.
- Alerts are written to an outbox in the state file before delivery, so a crash in between
  delivers them on the next run (a duplicate is possible). A destination that fails five times
  drops that alert, with a log line.
- Seen-id stores are pruned after `retention_days`, only after a complete pass, and never for an
  id GitHub still returns.

Known limits: polling cannot see an event GitHub never exposes, or a state that changes twice
between polls. Notification asks use GitHub's `participating` filter, so an ask GitHub does not
count as participating is not seen there (the issue and PR checks still run).

## Slack (optional, off by default)

`--slack` (or `"slack": {"enabled": true}`) adds Slack mentions and replies in your threads. Read
this before enabling it:

- It needs [Claude Code](https://docs.anthropic.com/en/docs/claude-code) and the official Slack
  plugin, already signed in. Each check is a headless `claude -p` call (a second one only when an
  older `claude` rejects `--json-schema`) with a small model (`haiku` by default), no built-in
  tools (`--tools ""`), only the Slack search and read-thread tools allowed, no user or project
  settings, an empty temporary working directory, and a minimal environment (no `GH_TOKEN`). That
  costs money or plan usage; `every_minutes` (default 25) throttles it.
- Model output is untrusted. The check asks for structured output (`claude -p --json-schema`), an
  object whose `items` array holds the messages; with an older `claude`, the answer must hold one
  JSON array, bare or in a ```json fence. Each item must carry a Slack channel id, a message ts and
  the text, plus proof: the raw `<@you>` tag for a mention, or the ts of your own message for a
  thread reply. Items without proof, outside `channels` (when set), or older than `days` are
  dropped. The proof fields are themselves model output, so a prompt injection in a Slack message
  can forge them. The damage is limited because the model has no tools except two Slack read
  tools, and only `https://*.slack.com/` links are ever passed to a notifier.
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

`gh-upstream-watch migrate --from OLD_STATE.json --claim-repo owner/repo` converts a v0 state file
(one flat dict of fingerprints plus `_notifications`, `_slack`, `_claimable`) into the configured
state file. Fingerprints and seen ids are kept; Slack messages the old script keyed by channel name
still count as seen.

The old script read only the first 100 timeline events of each item, so on a busy thread the new
one finds references the old one never saw. To keep that history from arriving as one burst:

1. Leave the old scheduled job running.
2. Run `gh-upstream-watch --notify none --webhook '' --no-slack` once (with the same `--config` or
   `--repos` you use). It records that history without notifying anyone; the old job still alerts
   on anything new.
3. `gh-upstream-watch --dry-run` should now be as quiet as the old job.
4. Unload the old job and `gh-upstream-watch init --schedule`.

## Status and support

- **Beta, one maintainer.** Issues and pull requests are welcome; expect an answer within a week.
  Questions and bugs go to [issues](https://github.com/colinmcnamara/gh-upstream-watch/issues),
  vulnerabilities to private reporting
  ([SECURITY](https://github.com/colinmcnamara/gh-upstream-watch/blob/main/SECURITY.md)).
- **No telemetry.** The tool sends nothing anywhere except your own `gh` calls (reads only) and the
  notifiers, webhook, hook or Slack check you configure.
- **Versioning.** Semantic versioning. Until 1.0, a minor version may change behavior, and the
  CHANGELOG says how; the `--json` fields are stable within `"v": 1`. Only the latest release gets
  fixes.

Contributions follow [CONTRIBUTING](https://github.com/colinmcnamara/gh-upstream-watch/blob/main/CONTRIBUTING.md)
and the [Code of Conduct](https://github.com/colinmcnamara/gh-upstream-watch/blob/main/CODE_OF_CONDUCT.md).

## License

MIT. See [LICENSE](https://github.com/colinmcnamara/gh-upstream-watch/blob/main/LICENSE). Changes
by version: [CHANGELOG](https://github.com/colinmcnamara/gh-upstream-watch/blob/main/CHANGELOG.md).
