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


def test_live_mode_persists_and_wires_up(app, monkeypatch):
    import meetingrec.app as appmod
    from meetingrec.transcribe import Segment

    monkeypatch.setattr(appmod, "BACKEND", "faster_whisper")

    class FakeRecorder:
        def __init__(self, path, **kw):
            self.on_audio = kw.get("on_audio")
            self.errors: list = []
            self.duration_seconds = 0.0
            self.seconds_since_sound = 0.0

        def start(self):
            pass

        def mic_is_digital_silence(self, after_seconds=5.0):
            return False

        def stop(self):
            return 0          # "no audio": skip the real transcription worker in this test

    class FakeLive:
        def __init__(self, **kw):
            self.on_segments = kw["on_segments"]
            self.started = self.stopped = False

        def feed_stereo(self, left, right):
            pass

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

        def transcript_text(self):
            return "[00:00] hi"

    monkeypatch.setattr(appmod, "DualChannelRecorder", FakeRecorder)
    monkeypatch.setattr(appmod, "LiveTranscriber", FakeLive)
    # _stop runs _finalize in a background thread (covered by the audio/worker
    # tests); stub it so this test makes no Tk calls off the main thread.
    monkeypatch.setattr(app, "_finalize", lambda *a, **k: None)

    app._live_var.set("Live transcript")
    app._on_live_mode_changed()
    saved = json.loads((app.test_profile / "settings.json").read_text(encoding="utf-8"))
    assert saved["live_mode"] == "transcript"

    app._start()
    app.root.update()
    assert isinstance(app._live, FakeLive) and app._live.started
    assert app._recorder.on_audio == app._live.feed_stereo
    assert app._live_frame.winfo_manager() == "pack"   # live area embedded + shown

    app._append_live_segments([Segment(0.0, 1.0, "hello world")])
    app.root.update()
    assert "hello world" in app._live_text.get("1.0", "end")

    app._stop()
    app.root.update()
    assert app._live is None            # live is torn down on stop


def test_live_layout_follows_mode_and_notes_render_markdown(app):
    from meetingrec.app import LIVE_LABELS

    app._live_var.set(LIVE_LABELS["off"])
    app._on_live_mode_changed()
    app.root.update()
    assert app._live_frame.winfo_manager() == ""              # off: hidden

    app._live_var.set(LIVE_LABELS["transcript+notes"])
    app._on_live_mode_changed()
    app.root.update()
    assert app._live_frame.winfo_manager() == "pack"          # shown
    assert app._notes_frame.winfo_manager() == "pack"         # notes shown

    app._live_var.set(LIVE_LABELS["transcript"])
    app._on_live_mode_changed()
    app.root.update()
    assert app._notes_frame.winfo_manager() == ""             # notes hidden again

    app._set_live_notes("# Title\n## Section\n- point **x**\n", markdown=True)
    app.root.update()
    text = app._live_notes.get("1.0", "end")
    assert "Title" in text and "Section" in text
    assert "#" not in text and "**" not in text               # markup stripped
    assert "•  point x" in text                          # bullet rendered


def test_meeting_prompt_offers_and_remembers_no(app):
    app._call_started("Zoom")
    assert app._prompt_win is not None and app._prompt_app == "Zoom"
    app.root.tk.call(app._prompt_win.protocol("WM_DELETE_WINDOW"))   # = "Not now"
    assert app._prompt_win is None and "Zoom" in app._declined
    app._call_started("Zoom")                               # same call: don't ask again
    assert app._prompt_win is None
    app._call_ended("Zoom")
    assert "Zoom" not in app._declined                      # next call asks again
