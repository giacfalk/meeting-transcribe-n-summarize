"""Smoke test: the GUI builds and starts with an empty profile."""

import json

import pytest

tk = pytest.importorskip("tkinter")


def test_app_starts_with_fresh_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("OneDrive", str(tmp_path / "onedrive"))
    monkeypatch.setattr("tkinter.messagebox.showerror", lambda *a, **k: None)
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available")
    from meetingrec.app import App

    try:
        app = App(root)
        root.update()
        log = app._log_box.get("1.0", "end")
        assert "Meeting Recorder v" in log
        settings_file = tmp_path / "appdata" / "MeetingRecorder" / "settings.json"
        assert json.loads(settings_file.read_text(encoding="utf-8"))["whisper_model"] == "base"
        assert "Saving to:" in app._dir_var.get()
        assert app._pending_btn.cget("text") == "Process pending"
    finally:
        root.destroy()
