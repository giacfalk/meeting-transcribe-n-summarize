"""The few things that differ between Windows, macOS and Linux."""

import os
import subprocess
import sys
from pathlib import Path

WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"
LINUX = sys.platform.startswith("linux")

# (sans-serif, monospace) UI fonts; Tk falls back to its default if one is missing.
if WINDOWS:
    FONTS = ("Segoe UI", "Consolas")
elif MACOS:
    FONTS = ("Helvetica Neue", "Menlo")
else:
    FONTS = ("DejaVu Sans", "DejaVu Sans Mono")


def config_dir() -> Path:
    """Per-user folder for settings.json."""
    home = Path.home()
    if WINDOWS:
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
    elif MACOS:
        base = home / "Library" / "Application Support"
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else home / ".config"
    return base / "MeetingRecorder"


def documents_dir() -> Path:
    """The user's Documents folder (OneDrive-synced on Windows when set up)."""
    home = Path.home()
    if WINDOWS:
        onedrive = os.environ.get("OneDrive") or os.environ.get("OneDriveCommercial")
        return (Path(onedrive) / "Documents") if onedrive else home / "Documents"
    if LINUX:
        try:   # localized names such as ~/Dokumente
            out = subprocess.run(["xdg-user-dir", "DOCUMENTS"], capture_output=True,
                                 text=True, timeout=5).stdout.strip()
            if out and Path(out) != home:
                return Path(out)
        except (OSError, subprocess.SubprocessError):
            pass
    return home / "Documents"


def open_path(path: Path) -> None:
    """Open a folder or file with the system's default app."""
    if WINDOWS:
        os.startfile(str(path))
    elif MACOS:
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def open_in_text_editor(path: Path) -> None:
    if WINDOWS:
        subprocess.Popen(["notepad.exe", str(path)])
    elif MACOS:
        subprocess.Popen(["open", "-t", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
