"""The only door to GitHub. Every call is `gh api --method GET`; nothing here can write.

Any failure (non-zero exit, bad JSON, timeout, a truncated search, a page cap) raises GHError,
so callers treat the thing they were checking as unknown for this run instead of empty.
"""
import json
import os
import subprocess
import time

TIMEOUT = 60
MAX_PAGES = 30  # 3,000 comments or timeline events per item; beyond that the item stays unknown
SEARCH_CAP = 1000  # GitHub search never returns more than this


class GHError(Exception):
    pass


class Incomplete(GHError):
    """GitHub answered, but not with everything (incomplete_results, result cap, page cap)."""


class NotFound(GHError):
    """404 or 410: deleted, transferred, or no longer visible to this token."""


RATE_LIMITED = ("rate limit", "(HTTP 429)")
# A network blip, not an answer: one quick retry before the item is called unknown for this run.
TRANSIENT = ('Get "http', "i/o timeout", "context deadline exceeded", "TLS handshake timeout", "connection reset",
             "unexpected EOF", "timed out after", "(HTTP 502)", "(HTTP 503)", "(HTTP 504)")
# No route to GitHub yet (just woke, Wi-Fi joining): wait, do not call anything unknown.
# A TLS or certificate error is not offline: it would never clear, so the run reports it.
OFFLINE = ("error connecting to", "no such host", "dial tcp", "network is unreachable", "connection refused",
           "no route to host", "i/o timeout", "context deadline exceeded", "TLS handshake timeout", "connection reset",
           "timed out after", "HTTP 502", "HTTP 503", "HTTP 504", "EOF")
RETRY_WAIT = 60  # search allows 30 requests a minute; one wait covers a burst


def gh_binary():
    return os.environ.get("GH_UPSTREAM_WATCH_GH") or "gh"


def _run(argv, timeout=TIMEOUT):
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise GHError(f"{argv[4]}: {e}")
    if proc.returncode != 0:
        raise GHError(f"{argv[4]}: gh exited {proc.returncode}: {proc.stderr.strip()[:200]}")
    return proc.stdout


def gh_get(path, params=None):
    """GET one API path. Params go through `-f k=v`, which gh sends as the query string on GET,
    so search queries are encoded by gh instead of by hand."""
    argv = [gh_binary(), "api", "--method", "GET", path]
    for k, v in sorted((params or {}).items()):
        argv += ["-f", f"{k}={v}"]
    for attempt in range(3):
        try:
            out = _run(argv)
            break
        except GHError as e:
            s = str(e)
            if "(HTTP 404)" in s or "(HTTP 410)" in s:
                raise NotFound(s)
            if attempt < 2 and any(r in s for r in RATE_LIMITED):
                # ponytail: fixed wait, not x-ratelimit-reset (gh api hides headers unless -i)
                time.sleep(RETRY_WAIT)
            elif attempt == 0 and any(t in s for t in TRANSIENT):
                time.sleep(2)
            else:
                raise
    try:
        return json.loads(out)
    except ValueError:
        raise GHError(f"{path}: gh returned invalid JSON")


def paginate(path, params=None, per_page=100):
    """Every page of a list endpoint. One failed page fails the whole list."""
    out = []
    for page in range(1, MAX_PAGES + 1):
        batch = gh_get(path, dict(params or {}, per_page=per_page, page=page))
        if not isinstance(batch, list):
            raise GHError(f"{path}: expected a list, got {type(batch).__name__}")
        out.extend(batch)
        if len(batch) < per_page:
            return out
    raise Incomplete(f"{path}: more than {MAX_PAGES} pages")


def search_issues(q, per_page=100, **params):
    """Every result of an issue search, or Incomplete when GitHub says it is partial."""
    items = []
    for page in range(1, SEARCH_CAP // per_page + 1):
        r = gh_get("search/issues", dict(params, q=q, per_page=per_page, page=page))
        if r.get("incomplete_results"):
            raise Incomplete(f"search {q!r}: GitHub returned incomplete_results")
        if r.get("total_count", 0) > SEARCH_CAP:
            # ponytail: no query partitioning in v0.1; narrow the repo list if this ever fires.
            raise Incomplete(f"search {q!r}: {r['total_count']} results exceed the {SEARCH_CAP} cap; "
                             "narrow --repos (results beyond the cap are invisible)")
        items.extend(r.get("items", []))
        if len(r.get("items", [])) < per_page or len(items) >= r.get("total_count", 0):
            # A short page is only the end if it accounts for every result GitHub counted.
            if len(items) != r.get("total_count"):
                raise Incomplete(f"search {q!r}: got {len(items)} of {r.get('total_count')} results")
            return items
    raise Incomplete(f"search {q!r}: page cap")


def html_url(api_url):
    return (api_url or "").replace("https://api.github.com/repos/", "https://github.com/").replace("/pulls/", "/pull/")


def wait_online(max_wait):
    """True once GitHub answers, or on an error that is not about the network (the run reports it).
    `rate_limit` costs nothing against the limit and goes through gh's own proxy and auth."""
    deadline, pause = time.monotonic() + max_wait, 5
    while True:
        try:
            _run([gh_binary(), "api", "--method", "GET", "rate_limit"],
                 timeout=max(5, min(TIMEOUT, deadline - time.monotonic())))
            return True
        except GHError as e:
            if not any(t in str(e) for t in OFFLINE):
                return True
        left = deadline - time.monotonic()
        if left <= 5:
            return False
        time.sleep(min(pause, left - 5))
        pause = min(pause * 2, 30)
