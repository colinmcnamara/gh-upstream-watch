"""Delivery. Each alert goes to stdout (text with the URL, or JSONL) and to each configured
destination separately; a destination that fails is retried on the next run."""
import html
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request

BACKENDS = ("auto", "none", "terminal-notifier", "notify-send", "osascript", "command")
# osascript gets the text only as arguments to a fixed script, so no title can change the script.
OSASCRIPT = ["osascript", "-e", "on run argv", "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
             "-e", "end run", "--"]
OPENABLE = re.compile(r"^https://(github\.com/|[\w-]+\.slack\.com/)")


# C0/C1 controls, plus bidi and zero-width format characters that can reorder or hide what is shown.
CONTROL = re.compile("[\x00-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is a failure: an SSO page answering 200 is not delivery, and the body never follows to another host."""
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def text(alert):
    """One line; control characters from titles anyone can write never reach the terminal or log."""
    return CONTROL.sub(" ", f"[{alert['time']}] {alert['title']}: {alert['message']} ({alert['url']})")


def resolve(backend):
    """'auto' picks the command in $GH_UPSTREAM_WATCH_NOTIFY, else the first desktop notifier found."""
    if backend != "auto":
        return backend
    if os.environ.get("GH_UPSTREAM_WATCH_NOTIFY"):
        return "command"
    for name in ("terminal-notifier", "notify-send"):
        if shutil.which(name):
            return name
    return "osascript" if sys.platform == "darwin" else "none"


def webhook_ok(url):
    """https anywhere; plain http only to this machine."""
    u = urllib.parse.urlparse(url or "")
    return bool(u.hostname) and (u.scheme == "https" or (u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1", "::1")))


def desktop_argv(backend, alert):
    title, msg, url = (CONTROL.sub(" ", alert[k]) for k in ("title", "message", "url"))
    if backend == "terminal-notifier":
        # terminal-notifier treats a message starting with [ ( { or a quote as an option; escape it.
        safe = "\\" + msg if msg[:1] in "[({\"'" else msg
        argv = ["terminal-notifier", "-title", "gh-upstream-watch", "-subtitle", title, "-message", safe]
        return argv + (["-open", url] if OPENABLE.match(url or "") else [])
    if backend == "notify-send":
        # Most notification daemons render the body as markup: escape it so a title cannot become a link.
        return ["notify-send", "--app-name=gh-upstream-watch", "--", title, html.escape(f"{msg}\n{url}")]
    if backend == "osascript":
        # osascript cannot open a link on click, so the URL stays in the text you can see.
        return OSASCRIPT + [title, f"{msg} {url}"]
    return None


def destinations(backend, webhook):
    return ["stdout"] + (["desktop"] if backend not in ("none", None) else []) + (["webhook"] if webhook else [])


def send_one(dest, alert, backend="none", as_json=False, webhook=None):
    """Deliver to one destination. True on success; a failure is logged, never raised."""
    try:
        if dest == "stdout":
            print(json.dumps(alert, sort_keys=True) if as_json else text(alert), flush=True)
        elif dest == "desktop" and backend == "command":
            subprocess.run(shlex.split(os.environ["GH_UPSTREAM_WATCH_NOTIFY"]), input=json.dumps(alert),
                           text=True, timeout=30, check=True, capture_output=True)
        elif dest == "desktop":
            subprocess.run(desktop_argv(backend, alert), timeout=30, check=True, capture_output=True)
        elif dest == "webhook":
            body = json.dumps({"text": text(alert), "alert": alert}).encode()
            req = urllib.request.Request(webhook, data=body, headers={"Content-Type": "application/json"})
            with _OPENER.open(req, timeout=10) as r:
                if not 200 <= r.status < 300:
                    raise OSError(f"webhook answered {r.status}")
        return True
    except Exception as e:  # a broken notifier must not lose the run
        print(f"notify: {dest}{' (' + backend + ')' if dest == 'desktop' else ''} failed, will retry next run: {e}",
              file=sys.stderr)
        return False


def deliver(entry, backend="none", as_json=False, webhook=None):
    """Try every destination still pending for an outbox entry; returns what is still pending."""
    entry["pending"] = [d for d in entry["pending"] if not send_one(d, entry["alert"], backend, as_json, webhook)]
    return entry["pending"]
