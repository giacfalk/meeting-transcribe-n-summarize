"""Smoke tests: the GUI and the settings dialog build and work with an empty profile."""

import importlib.util
import json

import pytest

tk = pytest.importorskip("tkinter")


@pytest.fixture(scope="module")
def tk_root():
    """One Tcl interpreter for all GUI tests: creating and destroying several in one
    process is unreliable on Windows ("invalid command name tcl_findLibrary")."""
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"no display available ({exc})")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def app(tk_root, tmp_path, monkeypatch):
    from meetingrec import config
    from meetingrec.app import App

    profile = tmp_path / "profile"
    # Keep these GUI tests self-contained: no tray icon, no polling for calls
    # (both have their own tests).
    config.save_settings({**config.defaults(), "tray_icon": False, "meeting_prompt": False},
                         profile / "settings.json")
    monkeypatch.setattr(config, "app_dir", lambda: profile)
    monkeypatch.setattr(config, "default_output_dir", lambda: tmp_path / "recordings")
    errors: list = []
    monkeypatch.setattr("tkinter.messagebox.showerror", lambda *a, **k: errors.append(a))

    window = tk.Toplevel(tk_root)   # the app works on any top-level window
    instance = App(window)
    instance.test_errors = errors
    instance.test_profile = profile
    window.update()
    try:
        yield instance
    finally:
        for closer in (instance._tray, instance._watcher, instance._hotkey):
            if closer is not None:
                closer.stop()
        instance._close_prompt()
        if window.winfo_exists():
            window.destroy()


def test_app_starts_with_fresh_profile(app):
    log = app._log_box.get("1.0", "end")
    assert "Meeting Recorder v" in log
    settings = json.loads((app.test_profile / "settings.json").read_text(encoding="utf-8"))
    assert settings["whisper_model"] == "base"
    assert "Saving to:" in app._dir_var.get()
    assert app._pending_btn.cget("text") == "Process pending"

    log_file = (app.test_profile / "meetingrec.log").read_text(encoding="utf-8")
    assert "Meeting Recorder v" in log_file and "Started on" in log_file
    # Error dialogs are suppressed in tests, so check what they would have said.
    if importlib.util.find_spec("faster_whisper"):
        assert "transcription: faster_whisper" in log_file
        assert not [e for e in app.test_errors if "transcription backend" in str(e)]


def test_settings_dialog_validates_and_saves(app):
    app._open_settings()
    dlg = app._settings_win
    dlg.v_hotkey.set("ctrl+hyper+r")
    dlg.save()
    assert "Hotkey" in dlg._error.get() and dlg.winfo_exists()   # stays open on error

    dlg.v_hotkey.set("ctrl+alt+f9")
    dlg.v_language.set("it")
    dlg.v_apps.set("teams, zoom ,, meet")
    dlg.v_timeout.set("300")
    dlg.save()
    app.root.update()
    saved = json.loads((app.test_profile / "settings.json").read_text(encoding="utf-8"))
    assert saved["hotkey"] == "ctrl+alt+f9" and saved["language"] == "it"
    assert saved["meeting_apps"] == ["teams", "zoom", "meet"]
    assert saved["silence_timeout"] == 300
    assert saved["_help"]                                   # untouched keys survive
    assert app.settings["language"] == "it"                 # applied immediately


def test_meeting_prompt_offers_and_remembers_no(app):
    app._call_started("Zoom")
    assert app._prompt_win is not None and app._prompt_app == "Zoom"
    app.root.tk.call(app._prompt_win.protocol("WM_DELETE_WINDOW"))   # = "Not now"
    assert app._prompt_win is None and "Zoom" in app._declined
    app._call_started("Zoom")                               # same call: don't ask again
    assert app._prompt_win is None
    app._call_ended("Zoom")
    assert "Zoom" not in app._declined                      # next call asks again
