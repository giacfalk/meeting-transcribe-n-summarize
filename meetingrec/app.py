"""Tkinter GUI: one button to start/stop; transcription and summaries run in the background."""

import platform
import queue
import re
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

from . import __version__, config, files, meetings, osutil, summarize, tray
from .audio import DualChannelRecorder, device_names, repair_wav, soundcard_available
from .hotkey import GlobalHotkey
from .live import LiveTranscriber
from .settings_dialog import SettingsDialog
from .transcribe import (
    BACKEND,
    Transcriber,
    format_timestamp,
    format_transcript,
    to_srt,
    transcribe_recording,
)

# -- Path resolution (works both in dev and when frozen by PyInstaller) --
# PyInstaller 6 puts bundled data files in _internal/ (sys._MEIPASS), not next to the .exe.
if getattr(sys, "frozen", False):
    _HERE = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    _HERE = Path(__file__).resolve().parent.parent
LOGO = _HERE / "assets" / "logo.png"

# -- Look (Catppuccin Mocha) -----------------------------------------------
SANS, MONO   = osutil.FONTS
BG           = "#1e1e2e"   # base
BG_HEADER    = "#11111b"   # crust: the top strip
BG_PANEL     = "#181825"   # mantle: text panels (transcript, notes, log)
SURFACE      = "#313244"   # buttons / inputs
SURFACE_HI   = "#45475a"   # hover
ACCENT       = "#89b4fa"   # blue accent
BTN_START    = "#a6e3a1"   # green
BTN_STOP     = "#f38ba8"   # red
TEXT_MAIN    = "#cdd6f4"   # primary text
TEXT_SUB     = "#a6adc8"   # readable secondary text
TEXT_DIM     = "#7f849c"   # faint labels / hints
FONT_BODY    = (SANS, 10)
FONT_TIMER   = (SANS, 40, "bold")
FONT_STATUS  = (SANS, 10, "bold")
FONT_SECTION = (SANS, 10, "bold")
FONT_LOG     = (MONO, 9)
TITLE        = "Meeting Recorder"

# Clear, self-explaining names for the live_mode setting (main-window dropdown).
LIVE_LABELS = {
    "off":              "Off  (transcribe when I stop)",
    "transcript":       "Live transcript",
    "transcript+notes": "Live transcript + notes (on demand)",
    "auto-notes":       "Live transcript + auto notes",
}
LIVE_MODE_BY_LABEL = {label: mode for mode, label in LIVE_LABELS.items()}
# One-line explanation shown under the dropdown for the selected mode.
LIVE_HINTS = {
    "off":              "Nothing live. The transcript and summary are created when you "
                        "press Stop.",
    "transcript":       "A rough transcript appears below, live, as you talk. The clean "
                        "transcript + summary are still made at Stop.",
    "transcript+notes": "Live transcript below, plus a button to write running notes "
                        "(key points, decisions, actions) whenever you want.",
    "auto-notes":       "Live transcript below, with running notes that rewrite themselves "
                        "automatically every few minutes.",
}


def make_button(parent, text, command, bg, fg, font, active_bg=None, **kw):
    """A flat colored button. macOS ignores button colors, so there it's a clickable label."""
    if not osutil.MACOS:
        return tk.Button(parent, text=text, command=command, font=font, bg=bg, fg=fg,
                         activebackground=active_bg or bg, activeforeground=fg,
                         relief=tk.FLAT, cursor="hand2", bd=0, **kw)
    label = tk.Label(parent, text=text, font=font, bg=bg, fg=fg, cursor="hand2", **kw)
    label.bind("<Button-1>", lambda _e: command())
    return label


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(TITLE)
        self.root.configure(bg=BG)
        self.root.resizable(True, True)
        self.root.minsize(440, 520)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        if osutil.MACOS:
            self.root.tk.createcommand("tk::mac::Quit", self._on_close)   # Cmd+Q

        self.settings, setting_warnings = config.load_settings()
        self._settings_mtime = self._mtime()

        self._recording   = False
        self._start_time: datetime | None = None
        self._timer_id    = None
        self._recorder: DualChannelRecorder | None = None
        self._recording_wav: Path | None = None   # file being recorded right now
        self._trigger_app: str | None = None      # meeting app whose call we're recording
        self._errors_seen = 0
        self._warned_silent_mic = False
        self._hotkey: GlobalHotkey | None = None
        self._tray: tray.TrayIcon | None = None
        self._told_about_tray = False
        self._watcher: meetings.MeetingWatcher | None = None
        self._declined: set[str] = set()          # apps whose call we were told not to record
        self._prompt_win: tk.Toplevel | None = None
        self._prompt_app: str | None = None
        self._prompt_timer = None
        self._settings_win: SettingsDialog | None = None
        self._transcriber = Transcriber(log=self._log_from_thread)

        # Live ("near real time") draft transcription, embedded in the main window.
        self._live: LiveTranscriber | None = None
        self._live_frame: tk.Frame | None = None
        self._notes_frame: tk.Frame | None = None
        self._live_text: scrolledtext.ScrolledText | None = None
        self._live_notes: scrolledtext.ScrolledText | None = None
        self._live_notes_btn = None
        self._live_notes_running = False
        self._live_notes_timer = None
        self._live_has_text = False

        # Background work. Stopping a recording drops its .wav on this queue; one
        # worker thread transcribes (.wav) or summarizes (.txt) jobs one at a time,
        # so a new recording can begin while earlier ones are still processing.
        self._job_queue: queue.Queue = queue.Queue()
        self._pending: set = set()           # paths queued or in progress
        self._pending_lock = threading.Lock()

        self._build()
        self._log(f"Meeting Recorder v{__version__}")
        audio_ok = soundcard_available()
        self._log(f"Started on {platform.platform()}, Python {platform.python_version()}; "
                  f"transcription: {BACKEND or 'none'}; audio capture: "
                  f"{'ok' if audio_ok else 'unavailable'}", window=False)
        for w in setting_warnings:
            self._log(w)
        if BACKEND is None:
            self._log("ERROR: no transcription backend (pip install faster-whisper).")
            messagebox.showerror(TITLE, "No transcription backend found.\n\n"
                                 "Install one with:  pip install faster-whisper")
        if not audio_ok:
            self._log("ERROR: audio capture is unavailable (the 'soundcard' package "
                      "could not be loaded).")
            messagebox.showerror(TITLE, "Audio capture is unavailable: the 'soundcard' "
                                 "package could not be loaded.")
        self._report_summary_backend()
        self._apply_hotkey()
        self._apply_tray()
        self._apply_meeting_watch()

        self._worker = threading.Thread(target=self._process_worker, daemon=True)
        self._worker.start()
        self._refresh_status()               # reflect any leftover files

        # Pick up edits to settings.json when the user comes back to the window.
        self.root.bind("<FocusIn>", lambda _e: self._reload_settings())
        self.root.bind("<Unmap>", self._on_unmap)

    # ---- UI construction ------------------------------------------------

    def _build(self):
        self._setup_styles()

        # ---- header strip -------------------------------------------------
        header = tk.Frame(self.root, bg=BG_HEADER)
        header.pack(fill=tk.X)
        bar = tk.Frame(header, bg=BG_HEADER)
        bar.pack(fill=tk.X, padx=16, pady=10)
        tk.Label(bar, text="●", font=(SANS, 11), bg=BG_HEADER, fg=ACCENT).pack(side=tk.LEFT)
        tk.Label(bar, text=TITLE, font=(SANS, 12, "bold"), bg=BG_HEADER,
                 fg=TEXT_MAIN).pack(side=tk.LEFT, padx=(7, 0))
        self._status_var = tk.StringVar(value="Ready")
        self._status_lbl = tk.Label(bar, textvariable=self._status_var, font=FONT_STATUS,
                                     bg=BG_HEADER, fg=TEXT_SUB)
        self._status_lbl.pack(side=tk.RIGHT)

        # ---- body ---------------------------------------------------------
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill=tk.BOTH, expand=True, padx=18)

        self._timer_var = tk.StringVar(value="00:00")
        tk.Label(body, textvariable=self._timer_var, font=FONT_TIMER,
                 bg=BG, fg=TEXT_MAIN).pack(pady=(14, 8))

        self._btn = make_button(body, "Start Recording", self._toggle, BTN_START, BG,
                                (SANS, 13, "bold"), width=22, height=2)
        self._btn.pack(pady=(0, 16))

        # model + live selectors (aligned in a two-column grid)
        controls = tk.Frame(body, bg=BG)
        controls.pack(fill=tk.X)
        controls.columnconfigure(1, weight=1)
        tk.Label(controls, text="Model", font=FONT_BODY, bg=BG,
                 fg=TEXT_SUB).grid(row=0, column=0, sticky="w", padx=(0, 10), pady=3)
        self._model_var = tk.StringVar(value=self.settings["whisper_model"])
        self._model_box = ttk.Combobox(controls, textvariable=self._model_var, state="readonly",
                                       font=FONT_BODY)
        self._set_model_choices()
        self._model_box.grid(row=0, column=1, sticky="ew", pady=3)
        self._model_box.bind("<<ComboboxSelected>>", self._on_model_changed)
        tk.Label(controls, text="Live", font=FONT_BODY, bg=BG,
                 fg=TEXT_SUB).grid(row=1, column=0, sticky="w", padx=(0, 10), pady=3)
        self._live_var = tk.StringVar(
            value=LIVE_LABELS.get(self.settings["live_mode"], LIVE_LABELS["off"]))
        self._live_box = ttk.Combobox(controls, textvariable=self._live_var, state="readonly",
                                      font=FONT_BODY, values=list(LIVE_LABELS.values()))
        self._live_box.grid(row=1, column=1, sticky="ew", pady=3)
        self._live_box.bind("<<ComboboxSelected>>", self._on_live_mode_changed)

        self._live_hint_var = tk.StringVar(value=LIVE_HINTS.get(self.settings["live_mode"], ""))
        tk.Label(body, textvariable=self._live_hint_var, font=(SANS, 8), bg=BG, fg=TEXT_DIM,
                 wraplength=460, justify="left", anchor="w").pack(fill=tk.X, pady=(3, 10))

        # ---- live area (embedded; shown only when a live mode is on) ------
        self._build_live_area(body)

        # ---- bottom bar: reserved at the bottom (side=BOTTOM) so it is never
        #      pushed off-window by the expanding live area above it. -------
        bottom = tk.Frame(body, bg=BG)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)

        self._queue_var = tk.StringVar(value="")
        self._queue_lbl = tk.Label(bottom, textvariable=self._queue_var, font=FONT_BODY,
                                   bg=BG, fg=TEXT_SUB)
        self._queue_lbl.pack(pady=(2, 4))
        btn_row = tk.Frame(bottom, bg=BG)
        btn_row.pack(pady=(0, 8))
        self._pending_btn = self._small_button(btn_row, "Process pending", self._process_pending)
        self._small_button(btn_row, "Open folder", self._open_folder)
        self._small_button(btn_row, "Settings", self._open_settings)
        self._ontop_var = tk.BooleanVar(value=False)
        tk.Checkbutton(btn_row, text="On top", variable=self._ontop_var,
                       command=self._toggle_ontop, font=(SANS, 9), bg=BG, fg=TEXT_DIM,
                       selectcolor=BG, activebackground=BG, activeforeground=TEXT_DIM,
                       relief=tk.FLAT, bd=0).pack(side=tk.LEFT, padx=(8, 0))
        self._log_box = scrolledtext.ScrolledText(
            bottom, font=FONT_LOG, bg=BG_PANEL, fg=TEXT_SUB, insertbackground=TEXT_MAIN,
            relief=tk.FLAT, height=5, state=tk.DISABLED, padx=10, pady=6)
        self._log_box.pack(fill=tk.X)
        self._dir_var = tk.StringVar(value=self._saving_to_text())
        tk.Label(bottom, textvariable=self._dir_var, font=(SANS, 8), bg=BG_PANEL, fg=TEXT_DIM,
                 anchor="w").pack(fill=tk.X)
        tk.Frame(bottom, bg=BG, height=10).pack()

        self._apply_live_layout(resize=True)

    def _build_live_area(self, body):
        self._live_frame = tk.Frame(body, bg=BG)
        tk.Label(self._live_frame, text="Live transcript  (draft)", font=FONT_SECTION,
                 bg=BG, fg=TEXT_MAIN).pack(anchor="w")
        self._live_text = scrolledtext.ScrolledText(
            self._live_frame, font=(SANS, 10), bg=BG_PANEL, fg=TEXT_MAIN,
            insertbackground=TEXT_MAIN, relief=tk.FLAT, wrap="word", height=6,
            state=tk.DISABLED, padx=10, pady=8, spacing1=1, spacing3=2)
        self._live_text.pack(fill=tk.BOTH, expand=True, pady=(3, 10))
        self._live_text.tag_configure("placeholder", foreground=TEXT_DIM)

        self._notes_frame = tk.Frame(self._live_frame, bg=BG)
        nhdr = tk.Frame(self._notes_frame, bg=BG)
        nhdr.pack(fill=tk.X)
        tk.Label(nhdr, text="Notes", font=FONT_SECTION, bg=BG, fg=TEXT_MAIN).pack(side=tk.LEFT)
        self._live_notes_btn = make_button(nhdr, "Generate now", self._live_notes_now,
                                           SURFACE, TEXT_MAIN, (SANS, 9),
                                           active_bg=SURFACE_HI, padx=10, pady=3)
        self._live_notes_btn.pack(side=tk.RIGHT)
        self._live_notes = scrolledtext.ScrolledText(
            self._notes_frame, font=(SANS, 10), bg=BG_PANEL, fg=TEXT_MAIN, relief=tk.FLAT,
            wrap="word", height=6, state=tk.DISABLED, padx=10, pady=8, spacing1=1, spacing3=3)
        self._live_notes.pack(fill=tk.BOTH, expand=True, pady=(3, 10))
        self._configure_notes_tags()

    def _small_button(self, parent, text, command):
        btn = make_button(parent, text, command, SURFACE, TEXT_MAIN, (SANS, 10),
                          active_bg=SURFACE_HI, padx=10, pady=4)
        btn.pack(side=tk.LEFT, padx=3)
        return btn

    def _setup_styles(self):
        """Dark theme for the ttk widgets (comboboxes here, the Settings dialog too)."""
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")      # the one built-in theme that fully recolors
        except tk.TclError:
            pass
        style.configure("TCombobox", fieldbackground=SURFACE, background=SURFACE,
                        foreground=TEXT_MAIN, arrowcolor=TEXT_MAIN, bordercolor=SURFACE,
                        lightcolor=SURFACE, darkcolor=SURFACE, relief="flat", padding=5)
        style.map("TCombobox", fieldbackground=[("readonly", SURFACE)],
                  background=[("readonly", SURFACE)], foreground=[("readonly", TEXT_MAIN)],
                  selectbackground=[("readonly", SURFACE)],
                  selectforeground=[("readonly", TEXT_MAIN)])
        self.root.option_add("*TCombobox*Listbox.background", BG_PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT_MAIN)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", BG)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT_MAIN)
        style.configure("TCheckbutton", background=BG, foreground=TEXT_MAIN)
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("TButton", background=SURFACE, foreground=TEXT_MAIN,
                        bordercolor=SURFACE, focuscolor=SURFACE, relief="flat", padding=6)
        style.map("TButton", background=[("active", SURFACE_HI)])
        style.configure("TEntry", fieldbackground=SURFACE, foreground=TEXT_MAIN,
                        bordercolor=SURFACE, insertcolor=TEXT_MAIN, padding=4)
        style.configure("TSpinbox", fieldbackground=SURFACE, foreground=TEXT_MAIN,
                        bordercolor=SURFACE, arrowcolor=TEXT_MAIN, padding=4)
        style.configure("TNotebook", background=BG, bordercolor=SURFACE)
        style.configure("TNotebook.Tab", background=BG_PANEL, foreground=TEXT_SUB,
                        padding=(12, 5))
        style.map("TNotebook.Tab", background=[("selected", SURFACE)],
                  foreground=[("selected", TEXT_MAIN)])
        style.configure("TSeparator", background=SURFACE)

    def _set_model_choices(self):
        choices = list(config.WHISPER_MODELS)
        if self.settings["whisper_model"] not in choices:
            choices.append(self.settings["whisper_model"])   # custom name from settings.json
        self._model_box.config(values=choices)

    def _saving_to_text(self) -> str:
        return f"Saving to: {self._output_dir()}"

    def _output_dir(self) -> Path:
        return config.output_dir(self.settings)

    # ---- Settings -------------------------------------------------------

    @staticmethod
    def _mtime() -> float:
        try:
            return config.settings_path().stat().st_mtime
        except OSError:
            return 0.0

    def _reload_settings(self):
        mtime = self._mtime()
        if mtime == self._settings_mtime:
            return
        self._settings_mtime = mtime
        old = self.settings
        self.settings, setting_warnings = config.load_settings()
        for w in setting_warnings:
            self._log(w)
        self._log("Settings updated.")
        self._model_var.set(self.settings["whisper_model"])
        self._live_var.set(LIVE_LABELS.get(self.settings["live_mode"], LIVE_LABELS["off"]))
        self._live_hint_var.set(LIVE_HINTS.get(self.settings["live_mode"], ""))
        self._set_model_choices()
        self._dir_var.set(self._saving_to_text())
        if self.settings["live_mode"] != old["live_mode"]:
            self._apply_live_layout(resize=True)
        if self.settings["hotkey"] != old["hotkey"]:
            self._apply_hotkey()
        if (self.settings["summary_backend"], self.settings["summary_model"]) != \
                (old["summary_backend"], old["summary_model"]):
            self._report_summary_backend()
        self._apply_tray()
        self._apply_meeting_watch()
        self._refresh_status()

    def _open_settings(self):
        if self._settings_win is not None and self._settings_win.winfo_exists():
            self._settings_win.lift()
            return
        self._settings_win = SettingsDialog(self.root, self.settings,
                                            on_saved=self._reload_settings)

    def _report_summary_backend(self):
        reason = summarize.unavailable_reason(self.settings)
        if reason:
            self._log(f"Summaries off: {reason}.")
        else:
            model = self.settings["summary_model"]
            self._log(f"Summaries via {self.settings['summary_backend']}"
                      + (f" ({model})" if model else "") + ".")

    def _apply_hotkey(self):
        if self._hotkey:
            self._hotkey.stop()
            self._hotkey = None
        spec = self.settings["hotkey"].strip()
        if not spec:
            return
        try:
            hk = GlobalHotkey(spec, lambda: self.root.after(0, self._toggle))
        except ValueError as exc:
            self._log(f"Hotkey '{spec}' ignored: {exc}.")
            return
        if hk.start():
            self._hotkey = hk
            self._log(f"Hotkey {spec} starts/stops recording.")
        else:
            self._log(f"Hotkey {spec} unavailable: {hk.error}.")

    def _apply_tray(self):
        want = self.settings["tray_icon"] and tray.supported() and LOGO.exists()
        if want and self._tray is None:
            later = self.root.after
            try:
                self._tray = tray.TrayIcon(
                    LOGO,
                    on_show=lambda: later(0, self._show_window),
                    on_toggle=lambda: later(0, self._toggle),
                    on_process_pending=lambda: later(0, self._process_pending),
                    on_open_folder=lambda: later(0, self._open_folder),
                    on_quit=lambda: later(0, self._on_close),
                    is_recording=lambda: self._recording,
                )
                self._tray.start()
                self.root.after(1500, self._check_tray)
            except Exception as exc:
                self._tray = None
                self._log(f"Tray icon unavailable: {exc}")
        elif not want and self._tray is not None:
            self._tray.stop()
            self._tray = None
            self._show_window()

    def _check_tray(self):
        if self._tray is not None and self._tray.error:
            self._log(f"Tray icon unavailable: {self._tray.error}")
            self._tray = None

    def _apply_meeting_watch(self):
        want = self.settings["meeting_prompt"] and meetings.supported()
        if want and self._watcher is None:
            self._watcher = meetings.MeetingWatcher(
                lambda: self.settings["meeting_apps"],
                on_start=lambda app: self.root.after(0, self._call_started, app),
                on_end=lambda app: self.root.after(0, self._call_ended, app),
            )
            self._watcher.start()
        elif not want and self._watcher is not None:
            self._watcher.stop()
            self._watcher = None

    # ---- Window / tray --------------------------------------------------

    def _show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _on_unmap(self, event):
        if (event.widget is self.root and self.settings["minimize_to_tray"]
                and self._tray is not None and self._tray.running
                and self.root.state() == "iconic"):
            self.root.withdraw()
            if not self._told_about_tray:
                self._told_about_tray = True
                self._tray.notify("Still running here. Click the icon to show the window.")

    def _update_tray(self, status: str = ""):
        if self._tray is not None:
            try:
                self._tray.update(status)
            except Exception:
                pass

    # ---- Meeting prompts ------------------------------------------------

    def _call_started(self, app: str):
        if self._recording or app in self._declined:
            return
        self._prompt(f"{app} is using the microphone",
                     "Record this meeting? Remember to ask everyone for their consent first.",
                     "Record", lambda: self._start(trigger=app),
                     on_decline=lambda: self._declined.add(app), app=app)
        if self._tray is not None:
            self._tray.notify(f"{app} is using the microphone. Record this meeting?")

    def _call_ended(self, app: str):
        self._declined.discard(app)
        if self._prompt_app == app and not self._recording:
            self._close_prompt()       # the call ended before anyone answered
        if self._recording and self._trigger_app == app:
            self._prompt(f"{app} stopped using the microphone",
                         "The call seems to have ended. Stop recording?",
                         "Stop recording", self._stop, app=app)

    def _prompt(self, title: str, message: str, action_text: str, action,
                on_decline=None, app: str | None = None, timeout_ms: int = 60_000):
        """A small always-on-top question in the corner of the screen."""
        self._close_prompt()
        win = tk.Toplevel(self.root)
        win.title(TITLE)
        win.configure(bg=BG)
        win.resizable(False, False)
        win.attributes("-topmost", True)
        tk.Label(win, text=title, font=(SANS, 11, "bold"), bg=BG, fg=TEXT_MAIN,
                 wraplength=320, justify="left").pack(anchor="w", padx=16, pady=(14, 4))
        tk.Label(win, text=message, font=FONT_BODY, bg=BG, fg=TEXT_DIM,
                 wraplength=320, justify="left").pack(anchor="w", padx=16)
        row = tk.Frame(win, bg=BG)
        row.pack(anchor="e", padx=16, pady=14)

        def decline():
            self._close_prompt()
            if on_decline:
                on_decline()

        def accept():
            self._close_prompt()
            action()

        self._small_button(row, "Not now", decline)
        make_button(row, action_text, accept, BTN_START, BG, (SANS, 10, "bold"),
                    padx=12, pady=4).pack(side=tk.LEFT, padx=3)
        win.protocol("WM_DELETE_WINDOW", decline)
        win.update_idletasks()
        osutil.set_dark_titlebar(win)
        x = win.winfo_screenwidth() - win.winfo_reqwidth() - 24
        y = win.winfo_screenheight() - win.winfo_reqheight() - 72
        win.geometry(f"+{x}+{y}")
        self._prompt_win, self._prompt_app = win, app
        self._prompt_timer = self.root.after(timeout_ms, decline)

    def _close_prompt(self):
        if self._prompt_timer:
            self.root.after_cancel(self._prompt_timer)
            self._prompt_timer = None
        if self._prompt_win is not None:
            self._prompt_win.destroy()
            self._prompt_win = self._prompt_app = None

    # ---- Recording toggle -----------------------------------------------

    def _toggle(self):
        if not self._recording:
            self._start()
        else:
            self._stop()

    def _start(self, trigger: str | None = None):
        if self._recording:
            return
        self._reload_settings()
        started = datetime.now()
        wav = files.new_recording_path(self._output_dir(), started)
        s = self.settings

        live = self._make_live_transcriber(s)
        recorder = DualChannelRecorder(wav, silence_threshold=s["silence_threshold"],
                                       microphone=s["microphone"],
                                       system_audio=s["system_audio"],
                                       on_audio=live.feed_stereo if live else None)
        try:
            recorder.start()
        except OSError as exc:
            self._log(f"Could not start recording: {exc}")
            return

        self._recording, self._start_time = True, started
        self._recorder, self._recording_wav = recorder, wav
        self._live = live
        self._trigger_app = trigger
        self._errors_seen = 0
        self._warned_silent_mic = False
        self._btn.config(text="Stop Recording", bg=BTN_STOP, activebackground=BTN_STOP)
        self._set_status("Recording")
        mic, system = device_names(s["microphone"], s["system_audio"])
        self._log(f"Started at {started.strftime('%H:%M:%S')}  (mic: {mic}; system: {system})")
        self._update_tray("recording")
        if live is not None:
            live.start()
            self._apply_live_layout()
            self._clear_live(recording=True)
            self._log(f"Live {LIVE_LABELS.get(s['live_mode'], s['live_mode'])} is on "
                      f"(draft via '{s['live_model'] or 'base'}'; final transcript at stop).")
            if s["live_mode"] == "auto-notes":
                self._schedule_live_notes()
        self._tick()

    def _stop(self):
        """Stop recording and hand the file off for background transcription.

        Returns to the idle state immediately so a new recording can begin
        right away, even while previous recordings are still transcribing.
        """
        if not self._recording:
            return
        self._recording = False
        self._trigger_app = None
        if self._timer_id:
            self.root.after_cancel(self._timer_id)
            self._timer_id = None
        self._timer_var.set("00:00")
        self.root.title(TITLE)

        recorder, wav = self._recorder, self._recording_wav
        live = self._live
        self._recorder = self._recording_wav = self._live = None
        if self._live_notes_timer:
            self.root.after_cancel(self._live_notes_timer)
            self._live_notes_timer = None
        # Reserve the path so a "Process pending" scan can't grab the file
        # before _finalize has closed it and queued it.
        with self._pending_lock:
            self._pending.add(wav)

        self._btn.config(text="Start Recording", bg=BTN_START, activebackground=BTN_START)
        self._set_status("Ready")
        self._update_tray()
        self._refresh_status()
        threading.Thread(target=self._finalize, args=(recorder, wav, live), daemon=True).start()

    def _finalize(self, recorder: DualChannelRecorder, wav: Path, live=None):
        """Stop the audio streams, close the .wav and queue it (worker thread)."""
        frames = recorder.stop()
        if live is not None:
            try:
                live.stop()      # transcribe the last few seconds into the live draft
            except Exception as exc:
                self._log_from_thread(f"Live transcription stop error: {exc}")
        if frames:
            self._job_queue.put(wav)
            self._log_from_thread(f"Saved {wav.name} ({recorder.duration_seconds:.0f}s) "
                                  "- queued for transcription.")
        else:
            wav.unlink(missing_ok=True)
            with self._pending_lock:
                self._pending.discard(wav)
            self._log_from_thread("No audio captured.")
        self.root.after(0, self._refresh_status)

    # ---- Live transcription ---------------------------------------------

    def _make_live_transcriber(self, s: dict):
        """A LiveTranscriber for this recording, or None if live mode is off."""
        if s["live_mode"] == "off":
            return None
        if BACKEND is None:
            self._log("Live transcription needs a transcription backend; staying off.")
            return None
        return LiveTranscriber(
            model=s["live_model"] or "base", device=s["device"],
            language=s["language"].strip() or None,
            interval=max(2, int(s["live_interval"])),
            silence_threshold=s["silence_threshold"],
            on_segments=self._on_live_segments, log=self._log_from_thread)

    def _on_live_mode_changed(self, _event=None):
        mode = LIVE_MODE_BY_LABEL.get(self._live_var.get(), "off")
        changed = mode != self.settings["live_mode"]
        self.settings = {**self.settings, "live_mode": mode}
        try:
            config.update_setting("live_mode", mode)
            self._settings_mtime = self._mtime()
        except OSError as exc:
            self._log(f"Could not save settings: {exc}")
        self._live_hint_var.set(LIVE_HINTS.get(mode, ""))
        self._apply_live_layout(resize=changed)
        self._log(f"Live mode: {LIVE_LABELS.get(mode, mode)}"
                  + ("" if mode == "off" else " - starts with the next recording."))

    def _apply_live_layout(self, resize: bool = False):
        """Show or hide the embedded live transcript / notes to match the mode."""
        mode = self.settings["live_mode"]
        show = mode != "off"
        notes = mode in ("transcript+notes", "auto-notes")
        if show:
            if not self._live_frame.winfo_manager():
                # side=TOP fills the space between the controls above and the
                # (already reserved) bottom bar below.
                self._live_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
            if notes and not self._notes_frame.winfo_manager():
                self._notes_frame.pack(fill=tk.BOTH, expand=True)
            elif not notes:
                self._notes_frame.pack_forget()
            if not self._recording and not self._live_has_text:
                self._clear_live(recording=False)
        else:
            self._live_frame.pack_forget()
        if resize:
            w, h = (460, 540) if not show else (560, 780) if notes else (560, 620)
            self.root.geometry(f"{w}x{h}")

    def _clear_live(self, recording: bool):
        """Empty the transcript (with a placeholder when idle) and the notes."""
        if self._live_text is not None and self._live_text.winfo_exists():
            self._live_text.config(state=tk.NORMAL)
            self._live_text.delete("1.0", tk.END)
            if not recording:
                self._live_text.insert("1.0", "The live transcript will appear here as you "
                                       "talk, once you start recording.", "placeholder")
            self._live_text.config(state=tk.DISABLED)
        self._live_has_text = False
        self._set_live_notes("")

    def _on_live_segments(self, segs):
        self.root.after(0, self._append_live_segments, segs)

    def _append_live_segments(self, segs):
        if self._live_text is None or not self._live_text.winfo_exists():
            return
        self._live_text.config(state=tk.NORMAL)
        if not self._live_has_text:          # drop the placeholder on the first real text
            self._live_text.delete("1.0", tk.END)
            self._live_has_text = True
        for s in segs:
            who = f"{s.speaker}: " if s.speaker else ""
            self._live_text.insert(tk.END, f"[{format_timestamp(s.start)}]  {who}{s.text}\n")
        self._live_text.see(tk.END)
        self._live_text.config(state=tk.DISABLED)

    def _schedule_live_notes(self):
        interval = max(60, int(self.settings["live_notes_interval"]))
        self._live_notes_timer = self.root.after(interval * 1000, self._auto_live_notes)

    def _auto_live_notes(self):
        self._live_notes_timer = None
        if self._live is None:
            return
        self._live_notes_now()
        if self._live is not None:
            self._schedule_live_notes()

    def _live_notes_now(self):
        if self._live is None or self._live_notes_running:
            return
        reason = summarize.unavailable_reason(self.settings)
        if reason:
            self._set_live_notes(f"Notes unavailable: {reason}.")
            return
        text = self._live.transcript_text()
        if not text.strip():
            self._set_live_notes("Nothing transcribed yet.")
            return
        self._live_notes_running = True
        if self._live_notes_btn is not None and self._live_notes_btn.winfo_exists():
            self._live_notes_btn.config(text="Working...")
        threading.Thread(target=self._live_notes_worker,
                         args=(text, dict(self.settings)), daemon=True).start()

    def _live_notes_worker(self, text: str, settings: dict):
        try:
            notes, markdown = summarize.summarize(text, settings), True
        except Exception as exc:
            notes, markdown = f"Notes failed: {exc}", False
        self.root.after(0, self._finish_live_notes, notes, markdown)

    def _finish_live_notes(self, notes: str, markdown: bool = True):
        self._live_notes_running = False
        if self._live_notes_btn is not None and self._live_notes_btn.winfo_exists():
            self._live_notes_btn.config(text="Generate now")
        self._set_live_notes(notes, markdown=markdown)

    def _set_live_notes(self, text: str, markdown: bool = False):
        if self._live_notes is None or not self._live_notes.winfo_exists():
            return
        if markdown and text.strip():
            self._render_markdown(self._live_notes, text)
            return
        self._live_notes.config(state=tk.NORMAL)
        self._live_notes.delete("1.0", tk.END)
        if text:
            self._live_notes.insert(tk.END, text + "\n")
        self._live_notes.config(state=tk.DISABLED)

    # -- lightweight Markdown rendering for the notes panel ---------------
    def _configure_notes_tags(self):
        n = self._live_notes
        n.tag_configure("h1", font=(SANS, 13, "bold"), foreground=TEXT_MAIN,
                        spacing1=8, spacing3=4)
        n.tag_configure("h2", font=(SANS, 10, "bold"), foreground=ACCENT, spacing1=7, spacing3=2)
        n.tag_configure("bullet", lmargin1=16, lmargin2=30, spacing3=2)
        n.tag_configure("bold", font=(SANS, 10, "bold"))

    def _render_markdown(self, widget, text: str):
        widget.config(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        for raw in text.splitlines():
            line = raw.rstrip()
            if line.startswith("### "):
                widget.insert(tk.END, line[4:] + "\n", "h2")
            elif line.startswith("## "):
                widget.insert(tk.END, line[3:] + "\n", "h2")
            elif line.startswith("# "):
                widget.insert(tk.END, line[2:] + "\n", "h1")
            elif line.lstrip().startswith(("- ", "* ")):
                widget.insert(tk.END, "•  ", "bullet")
                self._insert_inline(widget, line.lstrip()[2:], "bullet")
                widget.insert(tk.END, "\n", "bullet")
            else:
                self._insert_inline(widget, line, None)
                widget.insert(tk.END, "\n")
        widget.see("1.0")
        widget.config(state=tk.DISABLED)

    def _insert_inline(self, widget, text: str, base_tag):
        """Insert text into a Text widget, rendering **bold** spans."""
        base = (base_tag,) if base_tag else ()
        pos = 0
        for m in re.finditer(r"\*\*(.+?)\*\*", text):
            if m.start() > pos:
                widget.insert(tk.END, text[pos:m.start()], base)
            widget.insert(tk.END, m.group(1), base + ("bold",))
            pos = m.end()
        if pos < len(text):
            widget.insert(tk.END, text[pos:], base)

    # ---- Background worker ----------------------------------------------

    def _process_worker(self):
        """Process queued jobs one at a time, for the life of the app.

        A .wav job is transcribed (and then queues its transcript for a
        summary); a .txt job is summarized.
        """
        while True:
            job = self._job_queue.get()
            try:
                if job is None:
                    break
                if job.suffix == ".wav":
                    self._do_transcribe(job)
                else:
                    self._do_summarize(job)
            except Exception as exc:
                self._log_from_thread(f"ERROR processing {job.name}: {exc} "
                                      "- use 'Process pending' to retry.")
            finally:
                if job is not None:
                    with self._pending_lock:
                        self._pending.discard(job)
                self._job_queue.task_done()
                self.root.after(0, self._refresh_status)

    def _do_transcribe(self, wav: Path):
        s = self.settings   # snapshot; the UI thread may swap in reloaded settings
        if repair_wav(wav):
            self._log_from_thread(f"Repaired the header of {wav.name} (interrupted recording).")
        self._log_from_thread(f"Transcribing {wav.name}...")
        segments = transcribe_recording(
            wav, self._transcriber, model=s["whisper_model"], device=s["device"],
            language=s["language"].strip() or None, speaker_labels=s["speaker_labels"],
            mic_label=s["mic_label"], others_label=s["others_label"],
            silence_threshold=s["silence_threshold"],
        )

        started = files.parse_stamp(wav)
        txt = wav.with_suffix(".txt")
        with open(txt, "w", encoding="utf-8") as f:
            if started:
                f.write(f"Started    : {started.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Transcribed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 60 + "\n\n")
            f.write((format_transcript(segments) or "(no speech detected)") + "\n")
        if s["write_srt"] and segments:
            wav.with_suffix(".srt").write_text(to_srt(segments), encoding="utf-8")
        if not s["keep_audio"]:
            wav.unlink(missing_ok=True)

        if not segments:
            # Nothing to summarize; a short note keeps it from showing up as pending.
            out = files.summary_path(txt)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("# No speech detected\n\nThe recording contained no "
                           "recognizable speech.\n", encoding="utf-8")
            self._log_from_thread(f"No speech detected in {wav.name}.")
            return
        self._log_from_thread(f"[OK] Transcript: {txt.name}")
        if not summarize.unavailable_reason(self.settings):
            self._enqueue(txt)

    def _do_summarize(self, txt: Path):
        s = self.settings
        if files.has_summary(txt) or summarize.unavailable_reason(s):
            return
        transcript = txt.read_text(encoding="utf-8")
        if not transcript.strip():
            return
        self._log_from_thread(f"Summarizing {txt.name}...")
        try:
            text = summarize.summarize(transcript, s, cwd=txt.parent)
        except summarize.SummaryError as exc:
            self._log_from_thread(f"Summary failed for {txt.name}: {exc}")
            return
        out = files.summary_path(txt)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        self._log_from_thread(f"[OK] Summary: {files.SUMMARY_SUBDIR}/{out.name}")

    # ---- Pending files (manual transcription / summarization) -----------

    def _pending_files(self) -> list[Path]:
        out = self._output_dir()
        jobs = files.find_untranscribed(out, exclude={self._recording_wav})
        if not summarize.unavailable_reason(self.settings):
            jobs += files.find_unsummarized(out)
        return jobs

    def _process_pending(self):
        """Queue untranscribed recordings and unsummarized transcripts."""
        self._reload_settings()
        added = sum(1 for job in self._pending_files() if self._enqueue(job))
        self._log(f"Queued {added} pending file(s) for processing." if added
                  else "Nothing pending to process.")
        self._refresh_status()

    def _enqueue(self, path: Path) -> bool:
        """Add a job unless it is already queued / in progress."""
        with self._pending_lock:
            if path in self._pending:
                return False
            self._pending.add(path)
        self._job_queue.put(path)
        return True

    # ---- Timer ----------------------------------------------------------

    def _tick(self):
        if not (self._recording and self._start_time and self._recorder):
            return
        elapsed = int((datetime.now() - self._start_time).total_seconds())
        mm, ss = divmod(elapsed, 60)
        self._timer_var.set(f"{mm:02d}:{ss:02d}")
        self.root.title(f"● REC {mm:02d}:{ss:02d} - {TITLE}")   # visible in the taskbar
        self._update_tray(f"recording {mm:02d}:{ss:02d}")

        for err in self._recorder.errors[self._errors_seen:]:
            self._log(f"Not recording {err}")
        self._errors_seen = len(self._recorder.errors)
        if not self._warned_silent_mic and self._recorder.mic_is_digital_silence():
            self._warned_silent_mic = True
            self._log("WARNING: the microphone is sending pure silence (muted or not "
                      "connected?). Check your default input, or choose a microphone "
                      "in Settings.")

        timeout = self.settings["silence_timeout"]
        if timeout and self._recorder.seconds_since_sound >= timeout:
            self._log(f"No audio for {timeout}s - stopping automatically.")
            self._stop()
            return
        self._timer_id = self.root.after(1000, self._tick)

    # ---- Helpers --------------------------------------------------------

    def _set_status(self, text: str):
        self._status_var.set(text)
        color = {"Recording": BTN_STOP, "Ready": TEXT_SUB}.get(text, ACCENT)
        self._status_lbl.config(fg=color)

    def _refresh_status(self):
        """Update the queue label and the 'pending' button count (main thread)."""
        with self._pending_lock:
            pending_set = set(self._pending)
        active = len(pending_set)
        self._queue_var.set(f"Processing... ({active} in queue)" if active else "")
        waiting = sum(1 for f in self._pending_files() if f not in pending_set)
        self._pending_btn.config(
            text=f"Process pending ({waiting})" if waiting else "Process pending")

    def _log(self, msg: str, window: bool = True):
        now = datetime.now()
        if window:
            self._log_box.config(state=tk.NORMAL)
            self._log_box.insert(tk.END, f"{now:%H:%M:%S}  {msg}\n")
            self._log_box.see(tk.END)
            self._log_box.config(state=tk.DISABLED)
        try:   # also keep a log file for troubleshooting (one older copy is kept)
            path = config.log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > 1_000_000:
                path.replace(path.with_name(path.name + ".1"))
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{now:%Y-%m-%d %H:%M:%S}  {msg}\n")
        except OSError:
            pass

    def _log_from_thread(self, msg: str):
        self.root.after(0, lambda: self._log(msg))

    def _on_model_changed(self, _event=None):
        size = self._model_var.get()
        self.settings = {**self.settings, "whisper_model": size}
        try:
            config.update_setting("whisper_model", size)
            self._settings_mtime = self._mtime()
        except OSError as exc:
            self._log(f"Could not save settings: {exc}")
        self._log(f"Model changed to '{size}' (applies to new transcriptions)")

    def _open_folder(self):
        try:
            out = self._output_dir()
            out.mkdir(parents=True, exist_ok=True)
            osutil.open_path(out)
        except Exception as exc:
            self._log(f"Could not open folder: {exc}")

    def _toggle_ontop(self):
        self.root.attributes("-topmost", self._ontop_var.get())

    def _on_close(self):
        with self._pending_lock:
            active = len(self._pending)
        busy = []
        if self._recording:
            busy.append("a recording is in progress")
        if active:
            busy.append(f"{active} file(s) are being processed")

        if busy:
            self._show_window()
            if not messagebox.askokcancel(
                "Quit Meeting Recorder?",
                "Currently " + " and ".join(busy) + ".\n\n"
                "Recordings are saved to disk as you go, so nothing is lost - you can "
                "finish unprocessed files later with 'Process pending'.\n\nQuit now?",
            ):
                return

        # Close an in-progress recording so it survives as a pending .wav.
        if self._recording and self._recorder:
            self._recording = False
            if self._timer_id:
                self.root.after_cancel(self._timer_id)
            if not self._recorder.stop() and self._recording_wav:
                self._recording_wav.unlink(missing_ok=True)
        if self._live is not None:
            try:
                self._live.stop()
            except Exception:
                pass
            self._live = None
        for closer in (self._hotkey, self._tray, self._watcher):
            if closer is not None:
                closer.stop()
        self._close_prompt()
        self.root.destroy()


# -- Entry point -----------------------------------------------------------
def set_window_icon(root: tk.Tk) -> None:
    """The app icon for the main window and every dialog it opens."""
    try:
        if osutil.WINDOWS and (_HERE / "icon.ico").exists():
            root.iconbitmap(default=str(_HERE / "icon.ico"))
        elif LOGO.exists():
            root._icon_image = tk.PhotoImage(file=str(LOGO))   # keep a reference
            root.iconphoto(True, root._icon_image)
    except Exception:
        pass


def main():
    root = tk.Tk()
    set_window_icon(root)
    App(root)                       # _build sizes the window to the current live mode
    osutil.set_dark_titlebar(root)
    root.mainloop()
