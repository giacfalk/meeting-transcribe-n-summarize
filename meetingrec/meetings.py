"""Notice when a meeting app starts using the microphone, so the app can offer to record.

Nothing is recorded automatically: the watcher only reports which apps are
using the mic.
    Windows  the per-app microphone usage that Windows keeps in the registry
             (CapabilityAccessManager), which is what drives the mic icon in the taskbar
    Linux    PulseAudio / PipeWire recording streams, via `pactl`
    macOS    not supported yet
"""

import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

from .osutil import LINUX, WINDOWS

_CONSENT_KEY = (r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
                r"\ConsentStore\microphone")


def supported() -> bool:
    return WINDOWS or LINUX


def matches(app: str, patterns: list[str]) -> bool:
    """Whether an app name matches any pattern (case-insensitive substring, "*" = any)."""
    name = app.lower()
    return any(p == "*" or (p and p.lower() in name) for p in patterns)


# -- Windows ---------------------------------------------------------------
def _windows_entries():
    """(subkey name, packaged?, last start, last stop) for every app in the consent store."""
    import winreg
    for sub, packaged in (("", True), ("NonPackaged", False)):
        try:
            path = _CONSENT_KEY + ("\\" + sub if sub else "")
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            continue
        with key:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(key, i)
                except OSError:
                    break
                i += 1
                if name == "NonPackaged":
                    continue
                try:
                    with winreg.OpenKey(key, name) as app:
                        start = winreg.QueryValueEx(app, "LastUsedTimeStart")[0]
                        stop = winreg.QueryValueEx(app, "LastUsedTimeStop")[0]
                except OSError:
                    continue
                yield name, packaged, start, stop


def _windows_app_name(entry: str, packaged: bool) -> str:
    if packaged:   # "MSTeams_8wekyb3d8bbwe" -> "MSTeams"
        return entry.split("_")[0]
    return Path(entry.replace("#", "\\")).stem   # "C:#...#Zoom.exe" -> "Zoom"


def _windows_in_use(entries, own_exes=()) -> set[str]:
    own = {e.replace("\\", "#").lower() for e in own_exes if e}
    return {_windows_app_name(name, packaged) for name, packaged, start, stop in entries
            if start and not stop and name.lower() not in own}


# -- Linux -----------------------------------------------------------------
def _pactl_source_outputs() -> list[dict]:
    """Recording streams as dicts of their properties."""
    try:
        out = subprocess.run(["pactl", "-f", "json", "list", "source-outputs"],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip().startswith("["):
            return [s.get("properties", {}) for s in json.loads(out.stdout)]
        out = subprocess.run(["pactl", "list", "source-outputs"],   # pactl < 16: no JSON
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    streams: list[dict] = []
    for line in out.stdout.splitlines():
        if line.startswith("Source Output #"):
            streams.append({})
        m = re.match(r'\s+([\w.]+) = "(.*)"$', line)
        if m and streams:
            streams[-1][m.group(1)] = m.group(2)
    return streams


def _linux_in_use(streams: list[dict], own_pid: int) -> set[str]:
    apps = set()
    for props in streams:
        if props.get("application.process.id") == str(own_pid):
            continue
        name = props.get("application.name") or props.get("application.process.binary")
        if name:
            apps.add(name)
    return apps


def apps_using_microphone() -> set[str]:
    """Names of the other apps that are using the microphone right now."""
    if WINDOWS:
        # From source, Windows logs the base interpreter rather than the venv's python.exe.
        own = (sys.executable, getattr(sys, "_base_executable", ""))
        return _windows_in_use(_windows_entries(), own)
    if LINUX:
        return _linux_in_use(_pactl_source_outputs(), os.getpid())
    return set()


# -- Watcher ---------------------------------------------------------------
class MeetingWatcher:
    """Polls every few seconds; calls on_start(app) / on_end(app) (on its own thread)
    when an app matching `patterns()` starts or stops using the microphone."""

    def __init__(self, patterns, on_start, on_end, interval: float = 3.0,
                 probe=apps_using_microphone):
        self._patterns = patterns
        self._on_start, self._on_end = on_start, on_end
        self._interval = interval
        self._probe = probe
        self._active: set[str] = set()
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def poll(self) -> None:
        try:
            now = {a for a in self._probe() if matches(a, self._patterns())}
        except Exception:
            return
        for app in sorted(now - self._active):
            self._on_start(app)
        for app in sorted(self._active - now):
            self._on_end(app)
        self._active = now

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll()
            self._stop.wait(self._interval)
