"""gh-upstream-watch: one read-only pass over your upstream work, then exit. Schedule it with
launchd, systemd or cron (--print-plist / --print-systemd / --print-cron)."""
import argparse
import json
import os
import shutil
import string
import sys
import time
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from . import __version__, core, github, hooks, notify, packs, slack, state

DEFAULTS = {"repos": [], "extras": [], "state": None, "notify": "auto", "webhook": None, "hook": None,
            "packs_dirs": [], "claim_groups": [], "recent_closed_days": 14, "retention_days": 30,
            "baseline_days": 90, "notification_repos": None, "login": None, "bots": [], "slack": {"enabled": False}}


def config_dir():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "gh-upstream-watch"


def default_state():
    return str(Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "gh-upstream-watch" / "state.json")


def log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M')} {msg}", file=sys.stderr, flush=True)


def build_parser():
    ap = argparse.ArgumentParser(
        prog="gh-upstream-watch",
        description="Read-only alerts for your upstream work: tells you the next action when a maintainer "
                    "gate, a competing PR, or a review moves. Every GitHub call is a GET.")
    ap.add_argument("command", nargs="?", default="run", choices=["run", "status", "init", "migrate"],
                    help="run (default): one pass; status: summarize the state file; init: write a starter "
                         "config; migrate: convert a v0 state file (--from) into --state")
    ap.add_argument("--version", action="version", version=f"gh-upstream-watch {__version__}")
    ap.add_argument("--config", help="JSON config file (default: $XDG_CONFIG_HOME/gh-upstream-watch/config.json)")
    ap.add_argument("--state", help="state file (default: $XDG_STATE_HOME/gh-upstream-watch/state.json)")
    ap.add_argument("--repos", nargs="+", metavar="OWNER/REPO", help="repos to search for items you are involved in")
    ap.add_argument("--extra", nargs="+", metavar="OWNER/REPO#N", help="specific items to watch as well")
    ap.add_argument("--login", help="your GitHub login (default: asked from `gh api user`)")
    ap.add_argument("--once", action="store_true", help="one pass (the only mode; accepted for scripts)")
    ap.add_argument("--dry-run", action="store_true", help="print alerts, save nothing, notify nothing but stdout")
    ap.add_argument("--json", action="store_true", help="alerts as JSON lines on stdout")
    ap.add_argument("--notify", choices=notify.BACKENDS, help="desktop backend (default auto; 'command' runs "
                    "$GH_UPSTREAM_WATCH_NOTIFY with the alert JSON on stdin)")
    ap.add_argument("--webhook", help="also POST each alert as JSON to this https URL (http only for localhost)")
    ap.add_argument("--hook", help="absolute path of a program: item JSON on stdin, alert JSONL on stdout")
    ap.add_argument("--packs-dir", action="append", metavar="DIR", help="extra rule-pack directory (repeatable)")
    ap.add_argument("--claim-group", action="append", metavar="GROUP", help="claim-board group to watch (repeatable)")
    ap.add_argument("--slack", action="store_true", default=None, help="enable the opt-in Slack source")
    ap.add_argument("--no-slack", dest="slack", action="store_false", help="disable the Slack source for this run")
    ap.add_argument("--from", dest="from_path", help="migrate: the v0 state file to read")
    ap.add_argument("--claim-repo", default="unknown/unknown", help="migrate: repo of v0 claim-board rows")
    ap.add_argument("--force", action="store_true", help="init/migrate: overwrite an existing file")
    ap.add_argument("--print-plist", action="store_true", help="print a launchd agent and exit")
    ap.add_argument("--print-systemd", action="store_true", help="print a systemd user service + timer and exit")
    ap.add_argument("--print-cron", action="store_true", help="print a crontab line and exit")
    ap.add_argument("--interval", type=int, default=30, help="minutes between runs, for the --print-* helpers")
    return ap


class ConfigError(Exception):
    """Bad config, arguments or setup: exit status 2."""


PLACEHOLDER = "owner/repo"
MAX_ATTEMPTS = 5  # a destination that keeps failing is dropped after this many runs


def gh_hint(err):
    """Turn a failed `gh api user` into the next step for a first-time user."""
    s = str(err)
    if "No such file" in s or "not found" in s.lower() and "exited" not in s:
        return "gh (the GitHub CLI) was not found: install it from https://cli.github.com, then run `gh auth login`"
    if "auth login" in s or "exited 4" in s or "401" in s:
        return "gh is not signed in: run `gh auth login` (and `gh auth refresh -s notifications` for notification alerts)"
    return s


def load_config(a):
    """Precedence: CLI > environment > config file > defaults."""
    cfg = json.loads(json.dumps(DEFAULTS))
    path = a.config or os.environ.get("GH_UPSTREAM_WATCH_CONFIG") or str(config_dir() / "config.json")
    cfg["_path"] = path
    if os.path.exists(path):
        try:
            data = json.loads(Path(path).read_text())
        except ValueError as e:
            raise ConfigError(f"config {path}: invalid JSON: {e}")
        unknown = set(data) - set(DEFAULTS)
        if unknown:
            raise ConfigError(f"config {path}: unknown keys {sorted(unknown)}")
        cfg.update(data)
    elif a.config:
        raise ConfigError(f"config {path}: not found")
    if os.environ.get("GH_UPSTREAM_WATCH_REPOS"):
        cfg["repos"] = [r.strip() for r in os.environ["GH_UPSTREAM_WATCH_REPOS"].split(",") if r.strip()]
    cfg["state"] = os.environ.get("GH_UPSTREAM_WATCH_STATE") or cfg["state"]
    for key, val in (("repos", a.repos), ("extras", a.extra), ("state", a.state), ("notify", a.notify),
                     ("webhook", a.webhook), ("hook", a.hook), ("claim_groups", a.claim_group), ("login", a.login)):
        if val is not None:
            cfg[key] = val
    if a.slack is not None:
        cfg["slack"] = dict(cfg["slack"], enabled=a.slack)
    for key, default in DEFAULTS.items():
        val = cfg[key]
        if isinstance(default, list):
            ok = isinstance(val, list) and all(isinstance(x, str) for x in val)
        elif isinstance(default, int) and not isinstance(default, bool):
            ok = isinstance(val, int) and not isinstance(val, bool) and val > 0
        elif isinstance(default, dict):
            ok = isinstance(val, dict)
        elif key == "notification_repos":
            ok = val is None or isinstance(val, list) and all(isinstance(x, str) for x in val)
        else:
            ok = val is None or isinstance(val, str)
        if not ok:
            raise ConfigError(f"config {key}: {val!r} is not a valid value (default {default!r})")
    cfg["packs_dirs"] = cfg["packs_dirs"] + (a.packs_dir or [])
    for r in cfg["repos"]:
        if r.count("/") != 1 or not all(r.split("/")):
            raise ConfigError(f"repo {r!r}: expected owner/repo")
    cfg["state"] = os.path.expanduser(cfg["state"] or default_state())
    for e in cfg["extras"]:
        repo, _, n = e.partition("#")
        if repo.count("/") != 1 or not n.isdigit():
            raise ConfigError(f"--extra {e!r}: expected owner/repo#number")
    if cfg["webhook"] and not notify.webhook_ok(cfg["webhook"]):
        raise ConfigError("--webhook must be https:// (http:// only for localhost)")
    return cfg


def require_repos(cfg):
    if PLACEHOLDER in cfg["repos"] or not (cfg["repos"] or cfg["extras"]):
        raise ConfigError(f"no repos to watch (config {cfg['_path']}). Start with: "
                          "gh-upstream-watch init --repos OWNER/REPO, or pass --repos OWNER/REPO")


def alert(now, kind, key, title, message, url):
    return {"time": time.strftime("%Y-%m-%d %H:%M", time.localtime(now)), "kind": kind, "key": key,
            "title": title, "message": message, "url": url or ""}


def run(cfg, a, now=None):
    """One pass. Returns 0 when every check completed, 1 when something is unknown this run."""
    require_repos(cfg)
    now = now or time.time()
    rule_packs = packs.load([config_dir() / "packs", *cfg["packs_dirs"]])
    if cfg["hook"]:
        hooks.check(cfg["hook"])
    backend = "none" if a.dry_run else notify.resolve(cfg["notify"])
    webhook = None if a.dry_run else cfg["webhook"]
    dests = notify.destinations(backend, webhook)

    def flush(entries):
        """Deliver outbox entries; keep each one with the destinations that still failed."""
        keep = []
        for e in entries:
            e["pending"] = [d for d in e["pending"] if d in dests]
            if notify.deliver(e, backend, a.json, webhook):
                e["attempts"] = e.get("attempts", 0) + 1
                if e["attempts"] < MAX_ATTEMPTS:
                    keep.append(e)
                else:
                    log(f"dropping {e['pending']} for one alert after {MAX_ATTEMPTS} failed runs: {e['alert']['title']}")
        return keep

    path = cfg["state"]
    with state.lock(path):
        st = state.load(path)
        # Alerts a crashed run saved but did not deliver, and destinations that failed last time.
        if st["outbox"] and not a.dry_run:
            log(f"re-delivering {len(st['outbox'])} alert(s) from an earlier run")
            st["outbox"] = flush(st["outbox"])
            state.save(path, st)
        complete, alerts, unknowns, failed = True, [], [], set()

        def unknown(what, e, source=None):
            nonlocal complete
            complete = False
            failed.add(source or what)
            unknowns.append(f"{what}: {e}"[:300])
            log(f"{what}: unknown this run, nothing marked seen: {e}")

        def source_of(key):
            """Alerts are held back per source until that source has had one complete run, so one
            check that keeps failing (a typo'd extra, a token without notifications) never blocks the rest."""
            return f"extra:{key}" if key in cfg["extras"] else f"repo:{key.partition('#')[0]}"

        try:
            me = cfg.get("login") or github.gh_get("user")["login"]
        except Exception as e:
            unknown("login", gh_hint(e))
            st["last_unknown"] = unknowns
            if not a.dry_run:
                state.save(path, st)
            return 1

        old = st["items"]
        found = set()
        for repo in cfg["repos"]:
            try:
                found |= core.discover(repo, me, cfg["recent_closed_days"], now)
            except Exception as e:
                unknown(f"search {repo}", e, f"repo:{repo}")  # nothing is dropped: baselines below keep every old item
        # New items whose first fetch failed are retried until they get a baseline (or baseline_days pass).
        retry = {k: t for k, t in st.get("retry", {}).items() if now - t < cfg["baseline_days"] * 86400}
        watch = found | set(cfg["extras"]) | set(retry) | {k for k, v in old.items() if v.get("state") == "open"}
        items, gone = {}, set()
        for key in sorted(watch):
            repo, _, n = key.partition("#")
            rules = packs.for_repo(rule_packs, repo)
            try:
                fp = core.fingerprint(repo, int(n), me, rules, cfg["bots"])
            except github.NotFound as e:
                # Deleted, transferred, or no longer visible: say so once, then stop watching it.
                gone.add(key)
                retry.pop(key, None)
                if key in old:
                    alerts.append(("live", alert(now, "gone", key, f"{key} {old[key].get('title', '')[:50]}",
                                                         "GONE: deleted, moved, or no longer visible to you",
                                                         old[key].get("url"))))
                else:
                    log(f"{key}: not found ({e}); check --extra, or it was deleted")
                continue
            except Exception as e:
                unknown(key, e, source_of(key))
                if key in old:
                    items[key] = old[key]
                else:
                    retry.setdefault(key, now)
                continue
            retry.pop(key, None)
            found_alerts = core.changes(old.get(key), fp, me, rules)
            if cfg["hook"] and key in old:
                extra = hooks.run(cfg["hook"], {"key": key, "repo": repo, "number": int(n), "me": me, "old": old[key], "new": fp})
                if extra is None:
                    unknown(key, "--hook failed")
                    items[key] = old[key]
                    continue
                found_alerts += extra
            fp["_seen"] = now
            items[key] = fp
            for kind, msg, url in found_alerts:
                # Only first-sight alerts wait for seeding; a change against a saved baseline is always real.
                src = "live" if key in old else source_of(key)
                alerts.append((src, alert(now, kind, key, f"{key} {fp['title'][:50]}", msg, url or fp["url"])))
        # Baselines of items no longer searched (closed longer than recent_closed_days) are kept for
        # baseline_days, so a later reopen is compared with them and alerts instead of seeding quietly.
        for key, fp in old.items():
            if key not in items and key not in gone and now - fp.get("_seen", now) < cfg["baseline_days"] * 86400:
                items[key] = fp

        note_repos = cfg["notification_repos"]
        if note_repos is None:
            note_repos = cfg["repos"] + [e.partition("#")[0] for e in cfg["extras"]]
        seen, live = dict(st["notifications"]["seen"]), set()
        try:
            alerts += [("notifications", alert(now, **x))
                       for x in core.notification_asks(seen, cfg["retention_days"], now, live, note_repos)]
            st["notifications"]["seen"] = seen
        except Exception as e:
            unknown("notifications", e)

        for repo in cfg["repos"]:
            rules = packs.for_repo(rule_packs, repo)
            if not (rules["claimable"] and cfg["claim_groups"]):
                continue
            seen = dict(st["claimable"]["seen"])
            try:
                alerts += [(f"claim:{repo}", alert(now, **x))
                           for x in core.claimable_asks(seen, repo, rules, cfg["claim_groups"], now, live)]
                st["claimable"]["seen"] = seen
            except Exception as e:
                unknown(f"claim board {repo}", e, f"claim:{repo}")

        if cfg["slack"].get("enabled"):
            sst = json.loads(json.dumps(st["slack"]))
            try:
                alerts += [("slack", alert(now, **x)) for x in slack.check(cfg["slack"], sst, now)]
                st["slack"] = dict(sst, failures=0, last_error=None)
            except Exception as e:  # SlackError or a bug: either way GitHub alerts still go out
                # Slack has its own failure state; it never makes the GitHub pass incomplete.
                st["slack"]["failures"] = st["slack"].get("failures", 0) + 1
                st["slack"]["last_error"] = str(e)[:300]
                log(f"slack: unknown this run (failure {st['slack']['failures']}): {e}")

        st["items"], st["last_unknown"], st["retry"] = items, unknowns, retry
        # Slack seeds itself and "live" alerts diff a saved baseline; every other source seeds on its
        # first complete run.
        sources = ({f"repo:{r}" for r in cfg["repos"]} | {f"extra:{e}" for e in cfg["extras"]} | {"notifications"}
                   | {f"claim:{r}" for r in cfg["repos"] if packs.for_repo(rule_packs, r)["claimable"] and cfg["claim_groups"]})
        # State from 0.1.0 has only the global flag: everything it watched then counts as seeded, and a
        # repo added later still seeds quietly instead of alerting on its old /accepts.
        if "seeded_sources" not in st:
            had = {k.partition("#")[0] for k in old} | {k.partition("#")[0] for k in st["claimable"]["seen"]}
            st["seeded_sources"] = sorted(
                {s for s in sources if s == "notifications" or s.partition(":")[2].partition("#")[0] in had}
                - {f"extra:{e}" for e in cfg["extras"] if e not in old}) if st["seeded"] else []
        seeded = set(st["seeded_sources"])
        ok = lambda src: src in ("slack", "live") or src in seeded  # noqa: E731
        held = [al for src, al in alerts if not ok(src)]
        alerts = [al for src, al in alerts if ok(src)]
        newly = (sources - failed) - seeded
        seeded |= newly
        st["seeded_sources"], st["seeded"] = sorted(seeded), sources <= seeded
        if newly:
            log(f"seed run: watching {len(items)} item(s); {len(held)} alert(s) suppressed. "
                + ("All sources seeded; alerts start next run." if st["seeded"] else
                   f"Still seeding: {', '.join(sorted(sources - seeded))} (see `status`)."))
        if complete:
            st["last_complete"] = now
            state.prune(st, now, cfg["retention_days"], live)
        if a.dry_run:
            for al in alerts:
                notify.send_one("stdout", al, as_json=a.json)
            return 0 if complete else 1
        # Written before delivery: a crash re-delivers instead of losing them, and a failed destination
        # stays pending for the next run.
        st["outbox"] += [{"alert": al, "pending": list(dests)} for al in alerts]
        state.save(path, st)
        st["outbox"] = flush(st["outbox"])
        state.save(path, st)
    return 0 if complete else 1


def status(cfg):
    """A doctor: gh and login, config, repos and their packs, and what the last run could not check."""
    gh = shutil.which(github.gh_binary())
    print(f"gh: {gh or 'NOT FOUND (install from https://cli.github.com)'}")
    if gh:
        try:
            print(f"login: {cfg.get('login') or github.gh_get('user')['login']}")
        except Exception as e:
            print(f"login: FAILED: {gh_hint(e)}")
    print(f"config: {cfg['_path']}{'' if os.path.exists(cfg['_path']) else ' (missing: run gh-upstream-watch init --repos OWNER/REPO)'}")
    try:
        rule_packs = packs.load([config_dir() / "packs", *cfg["packs_dirs"]])
        for repo in cfg["repos"] or ["(none)"]:
            print(f"repo {repo}: packs {', '.join(packs.for_repo(rule_packs, repo)['ids'])}")
    except packs.PackError as e:
        print(f"packs: ERROR {e}")
    path = cfg["state"]
    if not os.path.exists(path):
        print(f"state: {path} (none yet; the first run seeds quietly)")
        return 0
    st = state.load(path)
    last = st.get("last_complete")
    print(f"state: {path}")
    print(f"seeded {st['seeded']}, last complete run {time.strftime('%Y-%m-%d %H:%M', time.localtime(last)) if last else 'never'}")
    if not st["seeded"]:
        print(f"seeded so far: {', '.join(st.get('seeded_sources') or []) or 'nothing'} (the rest seed on their first complete run)")
    if st.get("retry"):
        print(f"waiting for a first baseline: {', '.join(sorted(st['retry']))}")
    open_items = sum(1 for v in st["items"].values() if v.get("state") == "open")
    print(f"items: {len(st['items'])} ({open_items} open); notifications seen: {len(st['notifications']['seen'])}; "
          f"claim rows seen: {len(st['claimable']['seen'])}; outbox: {len(st['outbox'])}")
    for u in st.get("last_unknown") or []:
        print(f"unknown last run: {u}")
    if st["slack"]:
        print(f"slack: failures {st['slack'].get('failures', 0)}, last error {st['slack'].get('last_error')}")
    return 0


def init(a):
    path = Path(a.config or config_dir() / "config.json")
    if path.exists() and not a.force:
        raise ConfigError(f"{path} exists; use --force to overwrite")
    starter = {"repos": a.repos or [PLACEHOLDER], "extras": [], "notify": "auto", "claim_groups": [],
               "slack": {"enabled": False}}
    try:
        starter["login"] = a.login or github.gh_get("user")["login"]
    except Exception as e:
        print(f"note: could not read your login ({gh_hint(e)}); it will be asked of gh on each run", file=sys.stderr)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(starter, indent=2) + "\n")
    print(f"wrote {path}; {'edit repos, then ' if not a.repos else ''}run: gh-upstream-watch --dry-run")
    return 0


def migrate(cfg, a):
    if not a.from_path:
        raise ConfigError("migrate needs --from PATH (the v0 state file)")
    dest = cfg["state"]
    if os.path.exists(dest) and not a.force:
        raise ConfigError(f"{dest} exists; use --force to overwrite")
    v0 = json.loads(Path(a.from_path).expanduser().read_text())
    if "schema" in v0:
        raise ConfigError(f"{a.from_path} is already schema {v0['schema']}")
    st = state.migrate_v0(v0, time.time(), a.claim_repo)
    state.save(dest, st)
    print(f"migrated {len(st['items'])} items, {len(st['notifications']['seen'])} notification ids, "
          f"{len(st['claimable']['seen'])} claim rows, {len(st['slack'].get('seen', {}))} Slack ids -> {dest}")
    return 0


def template(name):
    here = Path(__file__).resolve().parent
    if (here / "contrib" / name).exists():
        return string.Template((here / "contrib" / name).read_text())
    raise SystemExit(f"template {name} not found")


def minimal_path():
    """Just the directories of the tools a scheduled run needs, not your whole shell PATH."""
    dirs = []
    for tool in (github.gh_binary(), "terminal-notifier", "notify-send", "claude"):
        found = shutil.which(tool)
        if found and os.path.dirname(found) not in dirs:
            dirs.append(os.path.dirname(found))
    return ":".join(dirs + [d for d in ("/usr/local/bin", "/usr/bin", "/bin") if d not in dirs])


def printers(a):
    exe = os.path.realpath(sys.argv[0])
    prog = [sys.executable, exe] if os.path.basename(exe) == "gh-upstream-watch" else [sys.executable, "-m", "gh_upstream_watch.cli"]
    if a.config:
        prog += ["--config", os.path.abspath(a.config)]
    prog.append("--once")
    fields = {"program": " ".join(prog), "interval_seconds": a.interval * 60, "interval_minutes": a.interval,
              "path": minimal_path(), "home": str(Path.home()),
              "program_args": "\n".join(f"    <string>{xml_escape(p)}</string>" for p in prog)}
    if a.print_plist:
        esc = {k: xml_escape(str(v)) for k, v in fields.items() if k != "program_args"}
        print(template("launchd.plist.template").substitute(fields, **esc), end="")
    if a.print_systemd:
        print("# ~/.config/systemd/user/gh-upstream-watch.service")
        print(template("systemd/gh-upstream-watch.service").substitute(fields))
        print("# ~/.config/systemd/user/gh-upstream-watch.timer")
        print(template("systemd/gh-upstream-watch.timer").substitute(fields), end="")
    if a.print_cron:
        print(template("cron.txt").substitute(fields), end="")
    return 0


def main(argv=None):
    a = build_parser().parse_args(argv)
    if a.print_plist or a.print_systemd or a.print_cron:
        if not (1 <= a.interval < 60 and 60 % a.interval == 0 if a.print_cron else 1 <= a.interval <= 1440):
            log("error: --interval must divide 60 for cron (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30), 1-1440 otherwise")
            return 2
        return printers(a)
    try:
        if a.command == "init":
            return init(a)
        cfg = load_config(a)
        if a.command == "status":
            return status(cfg)
        if a.command == "migrate":
            return migrate(cfg, a)
        return run(cfg, a)
    except state.Locked as e:
        log(f"skipped: {e}")
        return 3
    except (packs.PackError, ConfigError) as e:
        log(f"error: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
