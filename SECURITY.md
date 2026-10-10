# Security

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting on this repository
(https://github.com/colinmcnamara/gh-upstream-watch/security/advisories/new, or the Security tab,
"Report a vulnerability") rather than a public issue. Expect an acknowledgement within a week.

## Supported versions

Only the latest release gets fixes. 0.3.x, the last line for Python 3.9, gets none.

## Model

- **Read-only.** The tool's GitHub access only ever runs `gh api --method GET <path>`: every call
  is built in one module (`github.py`), which has no way to take another method or a request body.
  Tests replay fixtures through a stand-in `gh` that refuses anything else.
- **Your token stays with gh.** Authentication is whatever `gh auth` holds. The tool never reads,
  stores or logs a token.
- **Watched repos cannot change your rules.** Rule packs load only from the bundled directory and
  local directories you configure, never from a repository being watched. Text from GitHub
  (titles, comments) is shown in alerts, never executed or used as a pattern.
- **Hooks, notifier commands and webhooks are trusted local config.** They run without a shell,
  from absolute paths or commands you configure, but they are your code: a hook runs with your
  permissions and a webhook receives every alert. Desktop notifiers get text only as arguments
  (the AppleScript is fixed), and only github.com / slack.com links are passed to be opened.
- **Slack output is untrusted.** When enabled, the model's answer must parse as the expected JSON
  and each item must carry its proof fields (a mention tag, or your own message's ts), or it is
  dropped. The proof is itself model output, so a prompt injection in Slack can forge it; the
  headless call is limited to Slack read tools, and only `https://*.slack.com/` links are opened.
- **State** (`~/.local/state/gh-upstream-watch/state.json`, mode 0600) holds issue titles, URLs,
  logins and seen ids. It is yours; nothing is sent anywhere except to the notifiers and the hook
  you configure (a hook gets each item's saved state on stdin).

## Verifying a release

Each release from 0.4.0 on is built once in GitHub Actions and published to PyPI by trusted
publishing (no stored token), after a maintainer approves the `pypi` environment. PyPI keeps a
signed PEP 740 attestation tying each file to this repository's `release.yml` run, shown as
"Provenance" on the file's page on pypi.org. To check a file yourself:

```sh
uvx pypi-attestations verify pypi --repository https://github.com/colinmcnamara/gh-upstream-watch \
  pypi:gh_upstream_watch-0.4.6-py3-none-any.whl
# OK: gh_upstream_watch-0.4.6-py3-none-any.whl
```

A file signed by any other repository fails with "provenance was signed by repository ...".

From 0.4.2 on, each GitHub Release also carries GitHub's signed build provenance for both files
(`gh_upstream_watch-X.Y.Z.sigstore.json`), made in the build job before anything is tested or
published. To check a downloaded file against it:

```sh
gh attestation verify gh_upstream_watch-X.Y.Z-py3-none-any.whl --repo colinmcnamara/gh-upstream-watch
```
