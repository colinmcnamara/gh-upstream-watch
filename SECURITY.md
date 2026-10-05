# Security

## Model

- **Read-only.** The tool's built-in GitHub access only ever runs `gh api --method GET <path>`; the one function that
  talks to GitHub (`github.gh_get`) builds that command itself and has no way to take a method
  or a request body. Tests replay fixtures through a stand-in `gh` that refuses anything else.
- **Your token stays with gh.** Authentication is whatever `gh auth` holds. The tool never reads,
  stores or logs a token.
- **Watched repos cannot change your rules.** Rule packs load only from the bundled directory and
  local directories you configure, never from a repository being watched. Text from GitHub
  (titles, comments) is shown in alerts, never executed or used as a pattern.
- **Hooks, notifier commands and webhooks are trusted local config.** They run without a shell,
  from absolute paths or commands you configure, but they are your code: a hook runs with your
  permissions and a webhook receives every alert. Desktop notifiers get text only as arguments
  (the AppleScript is fixed), and only github.com / slack.com links are passed to be opened.
- **Slack output is untrusted.** When enabled, the model's answer must be strict JSON and each item
  must carry checkable proof, or it is dropped. The headless call is limited to Slack read tools.
- **State** (`~/.local/state/gh-upstream-watch/state.json`) holds issue titles, URLs, logins and
  seen ids. It is yours; nothing is sent anywhere except to the notifiers you configure.

## Verifying a release

Each release from 0.4.0 on is built once in GitHub Actions and published to PyPI by trusted
publishing (no stored token), after a maintainer approves the `pypi` environment. PyPI keeps a
signed PEP 740 attestation tying each file to this repository's `release.yml` run: see the file's
"Provenance" on pypi.org, or `https://pypi.org/integrity/gh-upstream-watch/<version>/<file>/provenance`.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting on this repository (Security tab, "Report a
vulnerability") rather than a public issue. Expect an acknowledgement within a week.
