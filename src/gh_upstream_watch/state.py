"""State file: schema v1, one writer at a time, atomic saves, bounded seen stores, v0 migration."""
import calendar
import contextlib
import fcntl
import json
import os
import sys
import time

SCHEMA = 1


class NewerState(Exception):
    """The state file is from a newer version. Not corrupt: never quarantined, the user upgrades."""


class Locked(Exception):
    pass


def empty():
    return {"schema": SCHEMA, "seeded": False, "items": {}, "notifications": {"seen": {}},
            "claimable": {"seen": {}}, "approvals": {"seen": {}}, "done": {},
            "slack": {}, "outbox": [], "last_complete": None, "unknown_streak": {}, "pace": {}, "mergers": {}}


def load(path):
    """The saved state, a fresh one if there is none, or a quarantined-then-fresh one if it is corrupt.
    A fresh state re-seeds silently, so corruption costs one quiet run, never a flood."""
    if not os.path.exists(path):
        return empty()
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("not an object")
        if "schema" not in data:
            print(f"state: {path} is v0; migrating in memory", file=sys.stderr)
            return migrate_v0(data, time.time())
        if not isinstance(data["schema"], int):
            raise ValueError("schema is not a number")
        if data["schema"] > SCHEMA:
            raise NewerState(f"state: {path} has schema {data['schema']}; this version reads {SCHEMA}. Upgrade.")
        st = {**empty(), **data}
        # Valid JSON in the wrong shape (a hand edit, a bad restore) would crash every run: quarantine it too.
        for k, v in empty().items():
            if v is not None and not isinstance(v, bool) and not isinstance(st[k], type(v)):
                raise ValueError(f"{k} is {type(st[k]).__name__}, not {type(v).__name__}")
        if not all(isinstance(st[k].get("seen", {}), dict) for k in ("notifications", "claimable", "approvals")):
            raise ValueError("seen is not an object")
        if not all(isinstance(v, dict) for v in st["items"].values()) or not all(
                isinstance(e, dict) and isinstance(e.get("alert"), dict) and isinstance(e.get("pending"), list)
                for e in st["outbox"]):
            raise ValueError("an item or outbox entry is not an object")
        return st
    except ValueError as e:
        aside = f"{path}.corrupt-{int(time.time())}"
        os.replace(path, aside)
        print(f"state: {path} is unreadable ({e}); moved to {aside}, re-seeding quietly", file=sys.stderr)
        return empty()


def save(path, data):
    """tmp + fsync + rename + fsync(dir): a crash leaves the old file or the new one, never half of one."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}"
    # 0600: the state holds titles and URLs from private repos.
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    fd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextlib.contextmanager
def lock(path):
    """Exclusive, non-blocking: a second run while one is in progress exits instead of racing."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd = os.open(f"{path}.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Locked(f"{path} is locked by another run")
        yield
    finally:
        os.close(fd)


ALWAYS = ("slack", "live")  # Slack seeds itself; "live" alerts diff a saved baseline


def hold_until_seeded(st, alerts, sources, failed, old_items, extras):
    """Split (source, alert) pairs into (send, held) and record which sources are now seeded.

    A source (repo:R, extra:R#N, claim:R, notifications) seeds on its first run without a failure;
    until then its alerts are held, so a first sight never floods. One source that keeps failing
    holds back only its own alerts. Returns (send, held, newly_seeded)."""
    if "seeded_sources" not in st:
        # 0.1.0 state has only the global flag: what it already watched counts as seeded, and a
        # repo added later still seeds quietly instead of alerting on its old /accepts.
        had = {k.partition("#")[0] for k in old_items} | {k.partition("#")[0] for k in st["claimable"]["seen"]}
        st["seeded_sources"] = sorted(
            {s for s in sources if s == "notifications" or s.partition(":")[2].partition("#")[0] in had}
            - {f"extra:{e}" for e in extras if e not in old_items}) if st["seeded"] else []
    seeded = set(st["seeded_sources"])
    send = [al for src, al in alerts if src in ALWAYS or src in seeded]
    held = [al for src, al in alerts if not (src in ALWAYS or src in seeded)]
    newly = (set(sources) - set(failed)) - seeded
    seeded |= newly
    st["seeded_sources"], st["seeded"] = sorted(seeded), set(sources) <= seeded
    return send, held, newly


def _epoch(iso):
    try:
        return calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        return None


def prune(st, now, retention_days, live=()):
    """Drop seen ids older than the retention window that GitHub no longer returns (`live`).
    Call only after a complete pass, so a failed run never forgets what it has not re-confirmed,
    and an id that is still being returned is never forgotten, so it never re-alerts."""
    cutoff = now - retention_days * 86400
    n = st["notifications"]["seen"]
    for k in [k for k, v in n.items() if k not in live and (_epoch(v) or now) < cutoff]:
        del n[k]
    for store in (st["claimable"]["seen"], st["approvals"]["seen"], st["slack"].get("seen", {})):
        for k in [k for k, v in store.items() if k not in live and isinstance(v, (int, float)) and v < cutoff]:
            del store[k]


def migrate_v0(old, now, claim_repo="unknown/unknown"):
    """v0 (one flat dict: 'owner/repo#n' fingerprints plus _notifications/_slack/_claimable) to v1.
    Fingerprints and seen ids are kept, so nothing re-alerts and nothing is re-seeded."""
    st = empty()
    old = dict(old)
    notes = dict(old.pop("_notifications", {}))
    seeded = bool(notes.pop("_seeded", False))
    st["notifications"]["seen"] = notes
    slack = old.pop("_slack", {})
    st["slack"] = {"last": slack.get("last", 0), "seeded": "last" in slack,
                   "seen": {k: _slack_ts(k, now) for k in slack.get("seen", {})}}
    claim = old.pop("_claimable", {})
    # v0 keyed claimable rows by bare number; v1 keys are repo-qualified. The v0 claim board lived
    # in one repo, passed as claim_repo, so rows keep their identity instead of re-alerting.
    st["claimable"]["seen"] = {f"{claim_repo}#{k}": now for k in claim.get("seen", {})}
    for key, v0 in old.items():
        fp = dict(v0)
        accepted, asked = fp.pop("accepted", False), fp.pop("assign_asked", False)
        fp["gates"] = {"accept": {"by": "", "done": bool(asked)}} if accepted else {}
        st["items"][key] = fp
    st["seeded"] = bool(st["items"]) or seeded
    return st


def _slack_ts(key, now):
    try:
        return float(key.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return now
