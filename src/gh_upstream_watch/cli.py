"""gh-upstream-watch: one read-only pass over your upstream work, then exit. Schedule it with
launchd, systemd or cron (--print-plist / --print-systemd / --print-cron)."""
import argparse
import json
import os
import shlex
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from . import __version__, core, github, hooks, notify, packs, schedule, slack, state

DEFAULTS = {"repos": [], "extras": [], "state": None, "notify": "auto", "webhook": None, "hook": None,
            "packs_dirs": [], "claim_groups": [], "recent_closed_days": 14, "retention_days": 30,
            "baseline_days": 90, "notification_repos": None, "login": None, "bots": [], "slack": {"enabled": False},
            "escalate_after_runs": 6}


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
    ap.add_argument("command", nargs="?", default="run", choices=["run", "status", "init", "migrate", "forget", "explain", "check-pack"],
                    help="run (default): one pass; status: summarize the state file; init: write a starter "
                         "config; migrate: convert a v0 state file (--from) into --state; forget KEY...: drop "
                         "items from the state; explain KEY: show what the tool sees for one item and why; check-pack FILE...: "
                         "validate rule packs and say what they do")
    ap.add_argument("keys", nargs="*", metavar="OWNER/REPO#N", help="forget/explain: the items; check-pack: the files")
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
    ap.add_argument("--schedule", action="store_true", help="init: also install and load the scheduler "
                    "(launchd on macOS, a systemd user timer on Linux)")
    ap.add_argument("--print-plist", action="store_true", help="print a launchd agent and exit")
    ap.add_argument("--print-systemd", action="store_true", help="print a systemd user service + timer and exit")
    ap.add_argument("--print-cron", action="store_true", help="print a crontab line and exit")
    ap.add_argument("--interval", type=int, default=30, help="minutes between runs, for the --print-* helpers")
    return ap


class ConfigError(Exception):
    """Bad config, arguments or setup: exit status 2."""


PLACEHOLDER = "owner/repo"
MAX_ATTEMPTS = 5  # a destination that keeps failing is dropped after this many runs


def hint(err):
    """The next step for an error, or '' when there is no better advice than the error itself."""
    s = str(err)
    if "No such file" in s or ("not found" in s.lower() and "exited" not in s and "HTTP" not in s):
        return "gh (the GitHub CLI) was not found: install it from https://cli.github.com, then run `gh auth login`"
    if "auth login" in s or "HTTP 401" in s or "exited 4" in s:
        return "gh is not signed in: run `gh auth login` (and `gh auth refresh -s notifications` for notification alerts)"
    if "notifications" in s and ("403" in s or "404" in s):
        return "the token cannot read notifications: run `gh auth refresh -s notifications`"
    if "(HTTP 404)" in s or "not found" in s.lower():
        return "not found: deleted, private to you, or a typo in repos/extras (`forget` drops it)"
    if "rate limit" in s or "(HTTP 429)" in s:
        return "rate limited: watch fewer repos, or schedule runs further apart"
    if "exceed" in s and "cap" in s:
        return "more than 1,000 search results: narrow repos"
    return ""


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


def fold(alerts):
    """One alert per item per run: a notification about an item that also alerts this run joins that
    alert (who asked first, then what changed) instead of arriving as a second line. Runs on what is
    being sent, after seeding, so a notification is never held back with a first-sight alert."""
    host = {}
    for al in alerts:
        if al["kind"] != "notification":
            host.setdefault(al["key"].lower(), al)
    out = []
    for al in alerts:
        h = host.get(al["key"].lower()) if al["kind"] == "notification" else None
        if h is None:
            out.append(al)
            continue
        h["message"] = f"{al['message']}; {h['message']}"
        if not h["action"]:  # the ask is what needs you: label the line, and link, as the ask
            h.update(kind=al["kind"], action=al["action"], url=al["url"] or h["url"])
    return out


def escalate(st, failed, errs, now, after):
    """One alert when a source has been unknown for `after` runs in a row; the streak ends on success."""
    streak = st.setdefault("unknown_streak", {})
    for src in [s for s in streak if s not in failed]:
        del streak[src]
    out = []
    for src in sorted(failed):
        s = streak.setdefault(src, {"since": now, "runs": 0})
        s["runs"] += 1
        if s["runs"] == after:
            since = time.strftime("%Y-%m-%d %H:%M", time.localtime(s["since"]))
            out.append(alert(now, "stuck", src, f"gh-upstream-watch: {src} not checkable since {since}",
                             hint(errs.get(src, "")) or errs.get(src, "")[:160], ""))
    return out


def upgrade(al):
    """Alerts an older version left in the outbox get the v1 fields before they are delivered."""
    al.setdefault("v", 1)
    al.setdefault("action", al.get("kind") in core.ACTION_KINDS)
    if "ts" not in al:
        try:
            al["ts"] = datetime.strptime(al.get("time", ""), "%Y-%m-%d %H:%M").astimezone().isoformat(timespec="seconds")
        except ValueError:
            al["ts"] = ""
    return al


def alert(now, kind, key, title, message, url):
    """One alert. `action` says it needs you; `ts` is ISO 8601 with the offset; `v` versions the shape."""
    return {"v": 1, "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(now)),
            "ts": datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds"),
            "kind": kind, "action": kind in core.ACTION_KINDS, "key": key,
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
        """Deliver outbox entries; keep each one with the destinations that still failed. More than
        BATCH desktop alerts at once become one banner; stdout and the webhook still get every alert."""
        keep = []
        for e in entries:
            upgrade(e["alert"])
        # Only pop-up banners batch: a `command` backend is a program that expects every alert.
        waiting = [e for e in entries if "desktop" in e["pending"] and "desktop" in dests]
        batch = backend in notify.BANNERS and len(waiting) > notify.BATCH
        if batch:
            shown = notify.send_one("desktop", notify.summary([e["alert"] for e in waiting]), backend)
            for e in waiting:
                # Shown: done. Not shown: try the banner again next run, never one pop-up per alert now.
                e["pending"].remove("desktop")
                e["_retry_desktop"] = not shown
        for e in entries:
            e["pending"] = [d for d in e["pending"] if d in dests]
            pending = notify.deliver(e, backend, a.json, webhook)
            if e.pop("_retry_desktop", False):
                pending.append("desktop")
            if pending:
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
        complete, alerts, unknowns, failed, errs = True, [], [], set(), {}

        def unknown(what, e, source=None):
            nonlocal complete
            complete = False
            failed.add(source or what)
            errs[source or what] = str(e)
            unknowns.append(f"{what}: {e}"[:300])
            log(f"{what}: unknown this run, nothing marked seen: {e}")

        def source_of(key):
            """Alerts are held back per source until that source has had one complete run, so one
            check that keeps failing (a typo'd extra, a token without notifications) never blocks the rest."""
            return f"extra:{key}" if key in cfg["extras"] else f"repo:{key.partition('#')[0]}"

        try:
            me = cfg.get("login") or github.gh_get("user")["login"]
        except Exception as e:
            unknown("login", hint(e) or e)
            st["last_unknown"] = unknowns
            stuck = escalate(st, failed, errs, now, cfg["escalate_after_runs"])
            if not a.dry_run:
                kept, new = st["outbox"], [{"alert": al, "pending": list(dests)} for al in stuck]
                st["outbox"] = kept + new
                state.save(path, st)
                st["outbox"] = kept + flush(new)  # kept entries were already tried once this run
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
                       for x in core.notification_asks(seen, cfg["retention_days"], now, live, note_repos, me,
                                                       lambda r: packs.for_repo(rule_packs, r)["quiet_titles"])]
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
        alerts, held, newly = state.hold_until_seeded(st, alerts, sources, failed, old, cfg["extras"])
        alerts = fold(alerts)
        if newly:
            log(f"seed run: watching {len(items)} item(s); {len(held)} alert(s) suppressed. "
                + ("All sources seeded; alerts start next run." if st["seeded"] else
                   f"Still seeding: {', '.join(sorted(sources - set(st['seeded_sources'])))} (see `status`)."))
        if complete:
            st["last_complete"] = now
            state.prune(st, now, cfg["retention_days"], live)
        # Not seen by anyone if it only goes to a launchd log: one real alert per streak.
        alerts += escalate(st, failed, errs, now, cfg["escalate_after_runs"])
        alerts.sort(key=lambda al: not al["action"])  # what needs you first; stable within each group
        if a.dry_run:
            for al in alerts:
                notify.send_one("stdout", al, as_json=a.json)
            # What the seed run held back, so a first --dry-run shows what you will get.
            for al in held:
                notify.send_one("stdout", dict(al, title=f"(preview, not sent while seeding) {al['title']}"), as_json=a.json)
            return 0 if complete else 1
        # Written before delivery: a crash re-delivers instead of losing them, and a failed destination
        # stays pending for the next run.
        kept, new = st["outbox"], [{"alert": al, "pending": list(dests)} for al in alerts]
        st["outbox"] = kept + new
        state.save(path, st)
        st["outbox"] = kept + flush(new)  # kept entries were already tried once this run, at the top
        state.save(path, st)
    return 0 if complete else 1


def ago(t, now=None):
    """'14 min ago' style, for status."""
    d = int((now or time.time()) - t)
    if d < 120:
        return f"{d} s ago"
    if d < 7200:
        return f"{d // 60} min ago"
    return f"{d // 3600} h ago" if d < 172800 else f"{d // 86400} days ago"


def status(cfg):
    """A doctor: setup, schedule, the last run, what is watched, and what needs fixing, with the fix."""
    def row(label, value):
        print(f"  {label:<13} {value}")

    when = lambda t: f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(t))} ({ago(t)})"  # noqa: E731
    print("setup")
    gh = shutil.which(github.gh_binary())
    row("gh", gh or "NOT FOUND: install it from https://cli.github.com")
    if gh:
        try:
            row("login", "@" + (cfg.get("login") or github.gh_get("user")["login"]))
        except Exception as e:
            row("login", f"FAILED: {hint(e) or e}")
    row("config", cfg["_path"] + ("" if os.path.exists(cfg["_path"]) else " (missing: gh-upstream-watch init --repos OWNER/REPO)"))
    try:
        rule_packs = packs.load([config_dir() / "packs", *cfg["packs_dirs"]])
        for repo in cfg["repos"] or ["(none)"]:
            row("repo", f"{repo} (packs: {', '.join(packs.for_repo(rule_packs, repo)['ids'])})")
    except packs.PackError as e:
        row("packs", f"ERROR {e}")
    row("schedule", schedule.describe())
    path = cfg["state"]
    print("state")
    row("file", path)
    if not os.path.exists(path):
        row("runs", "none yet; the first run seeds quietly")
        return 0
    st = state.load(path)
    last = st.get("last_complete")
    row("last complete", when(last) if last else "never")
    if st["seeded"]:
        row("seeded", "yes")
    else:
        row("seeded", f"not yet; done: {', '.join(st.get('seeded_sources') or []) or 'nothing'}")
    open_items = sum(1 for v in st["items"].values() if v.get("state") == "open")
    row("watching", f"{len(st['items'])} items ({open_items} open); {len(st['notifications']['seen'])} notifications "
                    f"and {len(st['claimable']['seen'])} claim rows already alerted")
    if st.get("retry"):
        row("waiting", f"first baseline for {', '.join(sorted(st['retry']))}")
    if st["outbox"]:
        row("undelivered", f"{len(st['outbox'])} alert(s) pending for " +
            ", ".join(sorted({d for e in st["outbox"] for d in e["pending"]})) + "; retried next run")
    if st["slack"]:
        row("slack", f"{st['slack'].get('failures', 0)} failure(s) in a row" +
            (f", last: {st['slack']['last_error']}" if st["slack"].get("last_error") else ""))
    problems = st.get("last_unknown") or []
    streak = st.get("unknown_streak") or {}
    slack_failing = st["slack"].get("failures", 0) > 0
    print("problems" if problems or streak or st["outbox"] or slack_failing else "problems: none")
    if st["outbox"]:
        row("undelivered", "see above; check the notifier or webhook, then the next run retries")
    if slack_failing:
        row("slack", "failing; see above")
    for u in problems:
        row("unknown", u)
        if hint(u):
            row("  fix", hint(u))
    for src, s in sorted(streak.items()):
        row("failing", f"{src}: {s['runs']} run(s) in a row, since {when(s['since'])}")
    return 0


def init(a):
    path = Path(a.config or config_dir() / "config.json")
    if path.exists() and a.schedule and not a.force and not a.repos:
        return schedule.install(a)  # config already there: just schedule it
    if path.exists() and not a.force:
        raise ConfigError(f"{path} exists; use --force to overwrite")
    if a.schedule and not a.repos:
        raise ConfigError("init --schedule needs --repos, so the scheduled runs have something to watch")
    starter = {"repos": a.repos or [PLACEHOLDER], "extras": [], "notify": "auto", "claim_groups": [],
               "slack": {"enabled": False}}
    try:
        starter["login"] = a.login or github.gh_get("user")["login"]
    except Exception as e:
        print(f"note: could not read your login ({hint(e) or e}); it will be asked of gh on each run", file=sys.stderr)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(starter, indent=2) + "\n")
    print(f"wrote {path}; {'edit repos, then ' if not a.repos else ''}run: gh-upstream-watch --dry-run")
    return schedule.install(a) if a.schedule else 0


def forget(cfg, a):
    """Drop items (and their retry and claim-row entries) from the state, so they stop being watched
    unless search finds them again, in which case they seed quietly."""
    if not a.keys:
        raise ConfigError("forget needs one or more OWNER/REPO#N")
    with state.lock(cfg["state"]):
        st = state.load(cfg["state"])
        for key in a.keys:
            hit = [st[s].pop(key, None) is not None for s in ("items", "retry", "unknown_streak") if s in st]
            hit.append(st["claimable"]["seen"].pop(key, None) is not None)
            print(f"{key}: {'forgotten' if any(hit) else 'not in the state'}")
            if key in cfg["extras"]:
                print(f"  note: {key} is still listed in extras ({cfg['_path']}); remove it there too")
        state.save(cfg["state"], st)
    return 0


def explain(cfg, a):
    """Read-only: the packs, the live fingerprint, the saved one, and what a run would alert, for one item."""
    if len(a.keys) != 1 or a.keys[0].count("/") != 1 or not a.keys[0].partition("#")[2].isdigit():
        raise ConfigError("explain needs exactly one OWNER/REPO#N")
    key = a.keys[0]
    repo, _, n = key.partition("#")
    rules = packs.for_repo(packs.load([config_dir() / "packs", *cfg["packs_dirs"]]), repo)
    me = cfg.get("login") or github.gh_get("user")["login"]
    print(f"{key}: packs {', '.join(rules['ids'])}; you are @{me}")
    print(f"  gates: {', '.join(g['id'] for g in rules['gates']) or 'none'}; quiet titles: "
          f"{', '.join(r.pattern for r in rules['quiet_titles']) or 'none'}")
    try:
        fp = core.fingerprint(repo, int(n), me, rules, cfg["bots"])
    except github.GHError as e:
        print(f"  live: unknown ({e})" + (f"\n  fix: {hint(e)}" if hint(e) else ""))
        return 1
    saved = state.load(cfg["state"])["items"].get(key) if os.path.exists(cfg["state"]) else None
    print(f"  live: {fp['state']}, {'PR' if fp['pr'] else 'issue'} \"{fp['title'][:60]}\", "
          f"{fp['human_comments']} human comment(s), {fp['mentions_me']} naming you, "
          f"gates {sorted(fp['gates']) or 'none'}, {len(fp['xrefs'])} reference(s)")
    if saved is None:
        print("  saved: none; a run would treat it as new (first sight: only gates alert, then it seeds)")
    else:
        print(f"  saved: last checked {time.strftime('%Y-%m-%d %H:%M', time.localtime(saved.get('_seen', 0)))}")
    found = core.changes(saved, fp, me, rules)
    for kind, msg, _ in found:
        print(f"  would alert: [{kind}] {msg}")
    if not found:
        print("  would alert: nothing")
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
    # Same config and repos as this command, and every notifier off (a webhook too) for the quiet run.
    argv = ["--state", dest] + (["--config", a.config] if a.config else []) + (["--repos", *a.repos] if a.repos else [])
    print("next, while the old job is still running: gh-upstream-watch " + " ".join(shlex.quote(x) for x in argv)
          + " --notify none --webhook '' --no-slack\n  This records history the old script could not see (it read only the first 100 timeline"
          "\n  events of each item), so it does not arrive as a burst of alerts. The old job keeps alerting"
          "\n  on anything new meanwhile. Then compare with --dry-run and switch the scheduler over.")
    return 0


def main(argv=None):
    a = build_parser().parse_args(argv)
    if a.print_plist or a.print_systemd or a.print_cron:
        if not (1 <= a.interval < 60 and 60 % a.interval == 0 if a.print_cron else 1 <= a.interval <= 1440):
            log("error: --interval must divide 60 for cron (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30), 1-1440 otherwise")
            return 2
        return schedule.printers(a)
    try:
        if a.command == "init":
            return init(a)
        if a.command == "check-pack":
            if not a.keys:
                raise ConfigError("check-pack needs one or more pack files")
            for f in a.keys:
                print("\n".join(packs.check(f)))
            return 0
        cfg = load_config(a)
        if a.command == "status":
            return status(cfg)
        if a.command == "migrate":
            return migrate(cfg, a)
        if a.command == "forget":
            return forget(cfg, a)
        if a.command == "explain":
            return explain(cfg, a)
        return run(cfg, a)
    except state.Locked as e:
        log(f"skipped: {e}")
        return 3
    except (packs.PackError, ConfigError) as e:
        log(f"error: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
