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
from pathlib import PureWindowsPath

from .osutil import LINUX, WINDOWS

_CONSENT_KEY = (r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
                r"\ConsentStore\microphone")


def supported() -> bool:
    return WINDOWS or LINUX


def capture_api_ok() -> bool:
    """On Windows, whether the Core Audio session API (pycaw/comtypes/psutil) is
    importable. When it is not -- e.g. a packaged build that forgot to bundle them --
    detection silently falls back to the stale registry method and can miss meetings,
    so the app logs a heads-up. Always True off Windows, which uses another method."""
    if not WINDOWS:
        return True
    try:
        import comtypes  # noqa: F401
        import psutil  # noqa: F401
        from pycaw.pycaw import IAudioSessionManager2  # noqa: F401
        return True
    except Exception:
        return False


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
    return PureWindowsPath(entry.replace("#", "\\")).stem   # "C:#...#Zoom.exe" -> "Zoom"


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


# -- Windows: active capture sessions (Core Audio) -------------------------
# Newer Windows builds (26xxx) stopped updating the CapabilityAccessManager
# registry timestamps in real time, so the registry can no longer say what is
# using the mic *right now*. The audio session API -- what drives the taskbar
# mic indicator -- still can, so it is the primary signal; the registry above
# is kept as a fallback for older builds (or if pycaw/comtypes is unavailable).
_AUDIO_SESSION_ACTIVE = 1   # AudioSessionStateActive


def _capture_apps_from_sessions(sessions, own_pid: int) -> set[str]:
    """App names with an ACTIVE capture session, from (pid, state, name) triples."""
    apps = set()
    for pid, state, name in sessions:
        if state != _AUDIO_SESSION_ACTIVE or not pid or pid == own_pid or not name:
            continue
        apps.add(name[:-4] if name.lower().endswith(".exe") else name)
    return apps


def _windows_capture_sessions():
    """(pid, state, process-name) for every microphone capture session, or None
    if the audio session API can't be reached (then the caller falls back)."""
    try:
        import comtypes
        import psutil
        from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
        from pycaw.constants import CLSID_MMDeviceEnumerator
        from pycaw.pycaw import IAudioSessionControl2, IAudioSessionManager2
    except Exception:
        return None
    comtypes.CoInitialize()
    try:
        enum = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator,
                                         comtypes.CLSCTX_INPROC_SERVER)
        try:   # every active capture device (eCapture = 1, DEVICE_STATE_ACTIVE = 1)
            collection = enum.EnumAudioEndpoints(1, 1)
            devices = [collection.Item(i) for i in range(collection.GetCount())]
        except Exception:
            devices = [enum.GetDefaultAudioEndpoint(1, 1)]
        sessions = []
        for device in devices:
            try:
                mgr = device.Activate(IAudioSessionManager2._iid_, comtypes.CLSCTX_ALL,
                                      None).QueryInterface(IAudioSessionManager2)
                session_enum = mgr.GetSessionEnumerator()
            except Exception:
                continue
            for i in range(session_enum.GetCount()):
                try:
                    control = session_enum.GetSession(i)
                    state = control.GetState()
                    pid = control.QueryInterface(IAudioSessionControl2).GetProcessId()
                    name = psutil.Process(pid).name() if pid else ""
                except Exception:
                    continue
                sessions.append((pid, state, name))
        return sessions
    except Exception:
        return None
    finally:
        comtypes.CoUninitialize()


def _windows_capture_apps(own_pid: int):
    sessions = _windows_capture_sessions()
    return None if sessions is None else _capture_apps_from_sessions(sessions, own_pid)


def apps_using_microphone() -> set[str]:
    """Names of the other apps that are using the microphone right now."""
    if WINDOWS:
        apps = _windows_capture_apps(os.getpid())
        if apps is not None:
            return apps
        # Fallback: the consent-store registry (older Windows builds).
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
