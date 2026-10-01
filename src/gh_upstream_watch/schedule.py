"""Scheduler entries: print them (--print-plist / --print-systemd / --print-cron) or install and
load them (init --schedule). Each one runs `gh-upstream-watch --once` with the interpreter and script
that printed it."""
import os
import shutil
import string
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from . import github


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


def render(a):
    """The scheduler files, as text: plist, service, timer, cron."""
    exe = os.path.realpath(sys.argv[0])
    prog = [sys.executable, exe] if os.path.basename(exe) == "gh-upstream-watch" else [sys.executable, "-m", "gh_upstream_watch.cli"]
    if a.config:
        prog += ["--config", os.path.abspath(a.config)]
    prog.append("--once")
    fields = {"program": " ".join(prog), "interval_seconds": a.interval * 60, "interval_minutes": a.interval,
              "path": minimal_path(), "home": str(Path.home()),
              "program_args": "\n".join(f"    <string>{xml_escape(p)}</string>" for p in prog)}
    esc = {k: xml_escape(str(v)) for k, v in fields.items() if k != "program_args"}
    return {"plist": template("launchd.plist.template").substitute(fields, **esc),
            "service": template("systemd/gh-upstream-watch.service").substitute(fields),
            "timer": template("systemd/gh-upstream-watch.timer").substitute(fields),
            "cron": template("cron.txt").substitute(fields)}


def printers(a):
    r = render(a)
    if a.print_plist:
        print(r["plist"], end="")
    if a.print_systemd:
        print("# ~/.config/systemd/user/gh-upstream-watch.service")
        print(r["service"])
        print("# ~/.config/systemd/user/gh-upstream-watch.timer")
        print(r["timer"], end="")
    if a.print_cron:
        print(r["cron"], end="")
    return 0


PLIST = "Library/LaunchAgents/local.gh-upstream-watch.plist"


def install(a, run=subprocess.run):
    """Write and load the scheduler for this machine: launchd on macOS, a systemd user timer where
    systemctl exists, otherwise print the cron line to add by hand."""
    r = render(a)
    if sys.platform == "darwin":
        p = Path.home() / PLIST
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(r["plist"])
        domain = f"gui/{os.getuid()}"
        run(["launchctl", "bootout", domain, str(p)], capture_output=True)  # an older copy, if loaded
        run(["launchctl", "bootstrap", domain, str(p)], check=True, capture_output=True)
        print(f"scheduled every {a.interval} min: {p} (loaded; logs in ~/Library/Logs/gh-upstream-watch.log)")
    elif shutil.which("systemctl"):
        d = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"
        d.mkdir(parents=True, exist_ok=True)
        (d / "gh-upstream-watch.service").write_text(r["service"])
        (d / "gh-upstream-watch.timer").write_text(r["timer"])
        run(["systemctl", "--user", "daemon-reload"], check=True, capture_output=True)
        run(["systemctl", "--user", "enable", "--now", "gh-upstream-watch.timer"], check=True, capture_output=True)
        print(f"scheduled every {a.interval} min: {d}/gh-upstream-watch.timer (enabled; logs: journalctl --user -u gh-upstream-watch)")
    else:
        print("no launchd or systemd here; add this line with `crontab -e`:\n" + r["cron"], end="")
    return 0
