"""OS-specific paths and helpers, exercised for every OS on whichever OS runs the tests."""

from pathlib import Path

import pytest

from meetingrec import config, hotkey, osutil, tray


def _as(monkeypatch, name):
    monkeypatch.setattr(osutil, "WINDOWS", name == "windows")
    monkeypatch.setattr(osutil, "MACOS", name == "macos")
    monkeypatch.setattr(osutil, "LINUX", name == "linux")


def test_config_dir_per_os(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _as(monkeypatch, "windows")
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    assert osutil.config_dir() == tmp_path / "roaming" / "MeetingRecorder"
    _as(monkeypatch, "macos")
    assert osutil.config_dir() == tmp_path / "Library" / "Application Support" / "MeetingRecorder"
    _as(monkeypatch, "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert osutil.config_dir() == tmp_path / ".config" / "MeetingRecorder"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert osutil.config_dir() == tmp_path / "xdg" / "MeetingRecorder"


def test_documents_dir_per_os(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _as(monkeypatch, "windows")
    monkeypatch.setenv("OneDrive", str(tmp_path / "OneDrive - Org"))
    assert osutil.documents_dir() == tmp_path / "OneDrive - Org" / "Documents"
    monkeypatch.delenv("OneDrive")
    monkeypatch.delenv("OneDriveCommercial", raising=False)
    assert osutil.documents_dir() == tmp_path / "Documents"
    _as(monkeypatch, "macos")
    assert osutil.documents_dir() == tmp_path / "Documents"
    _as(monkeypatch, "linux")
    monkeypatch.setattr(osutil.subprocess, "run", lambda *a, **k: type(
        "R", (), {"stdout": str(tmp_path / "Dokumente") + "\n"})())
    assert osutil.documents_dir() == tmp_path / "Dokumente"
    assert config.default_output_dir() == tmp_path / "Dokumente" / "Meeting Recorder" / "recordings"


def test_hotkey_spec_for_pynput():
    assert hotkey.to_pynput("ctrl+alt+r") == "<ctrl>+<alt>+r"
    assert hotkey.to_pynput("Shift+Win+F9") == "<shift>+<cmd>+<f9>"
    assert hotkey.to_pynput("cmd+space") == "<cmd>+<space>"
    with pytest.raises(ValueError):
        hotkey.to_pynput("hyper+r")


def test_recording_badge():
    Image = pytest.importorskip("PIL.Image")
    logo = Image.open(Path(__file__).parent.parent / "assets" / "logo.png")
    badge = tray.recording_badge(logo)
    assert badge.size == logo.size and badge.mode == "RGBA"
    s = badge.size[0]
    r, g, b, a = badge.getpixel((int(s * 0.76), int(s * 0.76)))   # centre of the dot
    assert r > 200 and g < 80 and a == 255
    assert logo.getpixel((int(s * 0.76), int(s * 0.76))) != badge.getpixel(
        (int(s * 0.76), int(s * 0.76)))
