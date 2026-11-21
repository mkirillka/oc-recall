#!/usr/bin/env python3
"""Install, inspect and remove the background index updater.

The updater is a plain periodic job that runs `oc-recall --sync --quiet`:
launchd on macOS, a systemd user timer on Linux, and a printed cron line as a
last resort (never edited automatically).

Imported by ``oc_recall``; everything here is side-effect free until an install
or uninstall function is called.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from shlex import quote as shlex_quote
from xml.sax.saxutils import escape as xml_escape

HOME = os.path.expanduser("~")
APP = "oc-recall"
LABEL = "local.oc-recall.index"

LAUNCHD_DIR = os.path.join(HOME, "Library", "LaunchAgents")
LAUNCHD_PLIST = os.path.join(LAUNCHD_DIR, f"{LABEL}.plist")
LAUNCHD_LOG_DIR = os.path.join(HOME, "Library", "Logs", APP)
LAUNCHD_LOG = os.path.join(LAUNCHD_LOG_DIR, "index.log")

SYSTEMD_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config"), "systemd", "user"
)
SYSTEMD_SERVICE = os.path.join(SYSTEMD_DIR, f"{APP}-index.service")
SYSTEMD_TIMER = os.path.join(SYSTEMD_DIR, f"{APP}-index.timer")


def script_path() -> str:
    """Absolute path of the CLI entry point (oc_recall.py next to this file)."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "oc_recall.py")


def command() -> list[str]:
    """Command the scheduler has to run."""
    return [sys.executable, script_path(), "--sync", "--quiet"]


def platform_kind() -> str:
    """Pick the scheduler available on this machine."""
    if sys.platform == "darwin" and shutil.which("launchctl"):
        return "launchd"
    if shutil.which("systemctl"):
        return "systemd"
    if shutil.which("crontab"):
        return "cron"
    return "none"


def launchd_plist(interval: int, command_line: list[str], log_path: str) -> str:
    """Render a launchd agent that runs the command every ``interval`` seconds."""
    escape = xml_escape
    arguments = "\n".join(
        f"        <string>{escape(part)}</string>" for part in command_line
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LABEL}</string>

    <key>ProgramArguments</key>
    <array>
{arguments}
    </array>

    <key>StartInterval</key>
    <integer>{int(interval)}</integer>

    <key>RunAtLoad</key>
    <true/>

    <key>ProcessType</key>
    <string>Background</string>

    <key>StandardOutPath</key>
    <string>{escape(log_path)}</string>
    <key>StandardErrorPath</key>
    <string>{escape(log_path)}</string>
</dict>
</plist>
"""


def systemd_service(command_line: list[str]) -> str:
    """Render the systemd user service unit."""
    quoted = " ".join(shlex_quote(part) for part in command_line)
    return (
        "[Unit]\nDescription=oc-recall index updater\n\n"
        f"[Service]\nType=oneshot\nExecStart={quoted}\n"
    )


def systemd_timer(interval: int) -> str:
    """Render the systemd user timer unit."""
    return (
        "[Unit]\nDescription=Run the oc-recall index updater periodically\n\n"
        "[Timer]\n"
        f"OnBootSec={min(interval, 300)}\n"
        f"OnUnitActiveSec={int(interval)}\n"
        "Persistent=true\n\n"
        "[Install]\nWantedBy=timers.target\n"
    )


def cron_line(command_line: list[str], interval: int) -> str:
    """Render a crontab line; printed for the user, never installed silently."""
    minutes = max(int(interval // 60), 1)
    quoted = " ".join(shlex_quote(part) for part in command_line)
    return f"*/{minutes} * * * * {quoted}"


def _run(args: list[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


def install_agent(interval: int = 3600, dry_run: bool = False) -> dict:
    """Create the scheduler job. Returns {'kind', 'path', 'message'}."""
    kind = platform_kind()
    command_line = command()

    if kind == "launchd":
        if not dry_run:
            os.makedirs(LAUNCHD_DIR, exist_ok=True)
            os.makedirs(LAUNCHD_LOG_DIR, exist_ok=True)
            with open(LAUNCHD_PLIST, "w", encoding="utf-8") as fh:
                fh.write(launchd_plist(interval, command_line, LAUNCHD_LOG))
            _run(["launchctl", "unload", LAUNCHD_PLIST])
            ok, output = _run(["launchctl", "load", LAUNCHD_PLIST])
            if not ok:
                return {
                    "kind": kind, "path": LAUNCHD_PLIST,
                    "message": f"plist written but launchctl load failed: {output}",
                }
        return {
            "kind": kind, "path": LAUNCHD_PLIST,
            "message": f"launchd agent, every {interval // 60} min ({LAUNCHD_PLIST})",
        }

    if kind == "systemd":
        units = [(SYSTEMD_SERVICE, systemd_service(command_line)),
                 (SYSTEMD_TIMER, systemd_timer(interval))]
        if not dry_run:
            os.makedirs(SYSTEMD_DIR, exist_ok=True)
            for path, text in units:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(text)
            _run(["systemctl", "--user", "daemon-reload"])
            ok, output = _run(["systemctl", "--user", "enable", "--now",
                               os.path.basename(SYSTEMD_TIMER)])
            if not ok:
                return {
                    "kind": kind, "path": SYSTEMD_TIMER,
                    "message": f"units written but systemctl enable failed: {output}",
                }
        return {
            "kind": kind, "path": SYSTEMD_TIMER,
            "message": f"systemd user timer, every {interval // 60} min ({SYSTEMD_TIMER})",
        }

    if kind == "cron":
        return {
            "kind": kind, "path": "crontab",
            "message": "add this line with `crontab -e`:\n  "
                       + cron_line(command_line, interval),
        }

    return {"kind": "none", "path": "", "message": "no scheduler found; run --sync manually"}


def uninstall_agent(dry_run: bool = False) -> dict:
    """Remove the scheduler job created by ``install_agent``."""
    kind = platform_kind()
    removed = []
    if kind == "launchd":
        if os.path.exists(LAUNCHD_PLIST):
            if not dry_run:
                _run(["launchctl", "unload", LAUNCHD_PLIST])
                os.remove(LAUNCHD_PLIST)
            removed.append(LAUNCHD_PLIST)
    elif kind == "systemd":
        if os.path.exists(SYSTEMD_TIMER):
            if not dry_run:
                _run(["systemctl", "--user", "disable", "--now",
                      os.path.basename(SYSTEMD_TIMER)])
                for path in (SYSTEMD_TIMER, SYSTEMD_SERVICE):
                    if os.path.exists(path):
                        os.remove(path)
                _run(["systemctl", "--user", "daemon-reload"])
            removed.append(SYSTEMD_TIMER)
    if removed:
        return {"kind": kind, "path": removed[0], "message": f"removed {removed[0]}"}
    return {"kind": kind, "path": "", "message": "updater is not installed"}


def agent_status() -> dict:
    """Describe the current state of the updater."""
    kind = platform_kind()
    if kind == "launchd":
        if not os.path.exists(LAUNCHD_PLIST):
            return {"kind": kind, "installed": False, "message": "not installed"}
        ok, output = _run(["launchctl", "list", LABEL])
        state = "loaded" if ok else "not loaded"
        return {"kind": kind, "installed": True,
                "message": f"launchd agent {state} ({LAUNCHD_PLIST})"}
    if kind == "systemd":
        if not os.path.exists(SYSTEMD_TIMER):
            return {"kind": kind, "installed": False, "message": "not installed"}
        ok, _ = _run(["systemctl", "--user", "is-enabled", os.path.basename(SYSTEMD_TIMER)])
        return {"kind": kind, "installed": True,
                "message": f"systemd timer {'enabled' if ok else 'disabled'} ({SYSTEMD_TIMER})"}
    return {"kind": kind, "installed": False, "message": "no scheduler available on this system"}


if __name__ == "__main__":
    print(install_agent()["message"])
