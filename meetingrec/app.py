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

from . import __version__, config, files, i18n, meetings, osutil, summarize, tray
from .audio import DualChannelRecorder, device_names, repair_wav, soundcard_available
from .hotkey import GlobalHotkey
from .i18n import t
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
from .welcome import WelcomeDialog

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
LEFT_W       = 400   # width of the controls column in the landscape (live) layout
_CATCHUP_MIN_SECONDS = 5   # don't bother catching up on less than this much already recorded

# The live_mode options (main-window dropdown). The mode ids are stable; the label
# and hint shown for each are translated (see i18n keys app.live.label/.hint.*).
_LIVE_KEYS = {
    "off": "off",
    "transcript": "transcript",
    "transcript+notes": "transcript_notes",
    "auto-notes": "auto_notes",
}


def live_label(mode: str) -> str:
    return t(f"app.live.label.{_LIVE_KEYS.get(mode, 'off')}")


def live_hint(mode: str) -> str:
    return t(f"app.live.hint.{_LIVE_KEYS.get(mode, 'off')}")


def live_labels() -> list:
    return [live_label(mode) for mode in _LIVE_KEYS]


def live_mode_by_label(label: str) -> str:
    return next((mode for mode in _LIVE_KEYS if live_label(mode) == label), "off")


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
        self.root.minsize(LEFT_W + 16, 540)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        if osutil.MACOS:
            self.root.tk.createcommand("tk::mac::Quit", self._on_close)   # Cmd+Q

        first_run = not config.settings_path().exists()   # before load_settings creates it
        self.settings, setting_warnings = config.load_settings()
        i18n.set_language(self.settings["ui_language"])   # the app's own text, before any t()
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
            messagebox.showerror(TITLE, t("app.msg.no_backend"))
        if not audio_ok:
            self._log("ERROR: audio capture is unavailable (the 'soundcard' package "
                      "could not be loaded).")
            messagebox.showerror(TITLE, t("app.msg.no_audio"))
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

        if first_run:   # one-time setup wizard the first time the app runs
            self.root.after(250, self._show_welcome)

    # ---- UI construction ------------------------------------------------

    def _build(self):
        self._setup_styles()

        # ---- header strip (full width) -----------------------------------
        header = tk.Frame(self.root, bg=BG_HEADER)
        header.pack(fill=tk.X)
        bar = tk.Frame(header, bg=BG_HEADER)
        bar.pack(fill=tk.X, padx=16, pady=10)
        tk.Label(bar, text="●", font=(SANS, 11), bg=BG_HEADER, fg=ACCENT).pack(side=tk.LEFT)
        tk.Label(bar, text=TITLE, font=(SANS, 12, "bold"), bg=BG_HEADER,
                 fg=TEXT_MAIN).pack(side=tk.LEFT, padx=(7, 0))
        self._status_var = tk.StringVar(value=t("app.status.ready"))
        self._status_lbl = tk.Label(bar, textvariable=self._status_var, font=FONT_STATUS,
                                     bg=BG_HEADER, fg=TEXT_SUB)
        self._status_lbl.pack(side=tk.RIGHT)

        # ---- main: a fixed-width control column on the left; the live
        #      transcript / notes panels fill the right (in live modes). ----
        main = tk.Frame(self.root, bg=BG)
        main.pack(fill=tk.BOTH, expand=True)

        left = tk.Frame(main, bg=BG, width=LEFT_W)
        left.pack(side=tk.LEFT, fill=tk.Y)
        left.pack_propagate(False)                 # keep a stable column width
        col = tk.Frame(left, bg=BG)
        col.pack(fill=tk.BOTH, expand=True, padx=16)

        self._timer_var = tk.StringVar(value="00:00")
        tk.Label(col, textvariable=self._timer_var, font=FONT_TIMER,
                 bg=BG, fg=TEXT_MAIN).pack(pady=(14, 8))
        self._btn = make_button(col, t("app.btn.start"), self._toggle, BTN_START, BG,
                                (SANS, 13, "bold"), width=20, height=2)
        self._btn.pack(pady=(0, 16))

        controls = tk.Frame(col, bg=BG)
        controls.pack(fill=tk.X)
        controls.columnconfigure(1, weight=1)
        tk.Label(controls, text=t("app.label.model"), font=FONT_BODY, bg=BG,
                 fg=TEXT_SUB).grid(row=0, column=0, sticky="w", padx=(0, 10), pady=3)
        self._model_var = tk.StringVar(value=self.settings["whisper_model"])
        self._model_box = ttk.Combobox(controls, textvariable=self._model_var, state="readonly",
                                       font=FONT_BODY)
        self._set_model_choices()
        self._model_box.grid(row=0, column=1, sticky="ew", pady=3)
        self._model_box.bind("<<ComboboxSelected>>", self._on_model_changed)
        tk.Label(controls, text=t("app.label.live"), font=FONT_BODY, bg=BG,
                 fg=TEXT_SUB).grid(row=1, column=0, sticky="w", padx=(0, 10), pady=3)
        self._live_var = tk.StringVar(value=live_label(self.settings["live_mode"]))
        self._live_box = ttk.Combobox(controls, textvariable=self._live_var, state="readonly",
                                      font=FONT_BODY, values=live_labels())
        self._live_box.grid(row=1, column=1, sticky="ew", pady=3)
        self._live_box.bind("<<ComboboxSelected>>", self._on_live_mode_changed)

        self._live_hint_var = tk.StringVar(value=live_hint(self.settings["live_mode"]))
        tk.Label(col, textvariable=self._live_hint_var, font=(SANS, 8), bg=BG, fg=TEXT_DIM,
                 wraplength=LEFT_W - 52, justify="left", anchor="w").pack(fill=tk.X, pady=(4, 10))

        # bottom of the control column (pinned): status, actions, log
        bottom = tk.Frame(col, bg=BG)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        self._queue_var = tk.StringVar(value="")
        self._queue_lbl = tk.Label(bottom, textvariable=self._queue_var, font=FONT_BODY,
                                   bg=BG, fg=TEXT_SUB)
        self._queue_lbl.pack(pady=(2, 4))
        btn_row = tk.Frame(bottom, bg=BG)
        btn_row.pack()
        self._pending_btn = self._small_button(btn_row, t("app.btn.pending"),
                                               self._process_pending)
        self._small_button(btn_row, t("app.btn.open_folder"), self._open_folder)
        self._small_button(btn_row, t("app.btn.settings"), self._open_settings)
        self._ontop_var = tk.BooleanVar(value=False)
        tk.Checkbutton(bottom, text=t("app.check.ontop"), variable=self._ontop_var,
                       command=self._toggle_ontop, font=(SANS, 9), bg=BG, fg=TEXT_DIM,
                       selectcolor=BG, activebackground=BG, activeforeground=TEXT_DIM,
                       relief=tk.FLAT, bd=0).pack(pady=(5, 8))
        # A friendly recent-activity card (the latest few messages, newest on top and
        # brightest) instead of a scrolling console. The full log still goes to the file.
        self._log_lines: list[str] = []
        panel = tk.Frame(bottom, bg=BG_PANEL)
        panel.pack(fill=tk.X)
        self._log_inner = tk.Frame(panel, bg=BG_PANEL)
        self._log_inner.pack(fill=tk.X, padx=12, pady=(9, 5))
        self._dir_var = tk.StringVar(value=self._saving_to_text())
        tk.Label(panel, textvariable=self._dir_var, font=(SANS, 8), bg=BG_PANEL, fg=TEXT_DIM,
                 anchor="w", wraplength=LEFT_W - 48, justify="left").pack(fill=tk.X, padx=12,
                                                                          pady=(0, 9))
        self._render_log()
        tk.Frame(bottom, bg=BG, height=10).pack()

        # the right-hand live panels (shown only when a live mode is on)
        self._build_live_area(main)
        self._apply_live_layout(resize=True)

    def _build_live_area(self, parent):
        self._live_frame = tk.Frame(parent, bg=BG)
        pad = tk.Frame(self._live_frame, bg=BG)
        pad.pack(fill=tk.BOTH, expand=True, padx=(6, 16), pady=14)
        tk.Label(pad, text=t("app.section.live_transcript"), font=FONT_SECTION,
                 bg=BG, fg=TEXT_MAIN).pack(anchor="w")
        self._live_text = scrolledtext.ScrolledText(
            pad, font=(SANS, 11), bg=BG_PANEL, fg=TEXT_MAIN, insertbackground=TEXT_MAIN,
            relief=tk.FLAT, wrap="word", height=8, state=tk.DISABLED,
            padx=12, pady=10, spacing1=1, spacing3=3)
        self._live_text.pack(fill=tk.BOTH, expand=True, pady=(4, 12))
        self._live_text.tag_configure("placeholder", foreground=TEXT_DIM)

        self._notes_frame = tk.Frame(pad, bg=BG)
        nhdr = tk.Frame(self._notes_frame, bg=BG)
        nhdr.pack(fill=tk.X)
        tk.Label(nhdr, text=t("app.section.notes"), font=FONT_SECTION, bg=BG,
                 fg=TEXT_MAIN).pack(side=tk.LEFT)
        self._live_notes_btn = make_button(nhdr, t("app.btn.generate_now"), self._live_notes_now,
                                           SURFACE, TEXT_MAIN, (SANS, 9),
                                           active_bg=SURFACE_HI, padx=10, pady=3)
        self._live_notes_btn.pack(side=tk.RIGHT)
        self._live_notes = scrolledtext.ScrolledText(
            self._notes_frame, font=(SANS, 11), bg=BG_PANEL, fg=TEXT_MAIN, relief=tk.FLAT,
            wrap="word", height=8, state=tk.DISABLED, padx=12, pady=10, spacing1=1, spacing3=4)
        self._live_notes.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
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
                        foreground=TEXT_MAIN, arrowcolor=TEXT_SUB, bordercolor=SURFACE,
                        lightcolor=SURFACE, darkcolor=SURFACE, relief="flat", padding=6)
        style.map("TCombobox", fieldbackground=[("readonly", SURFACE)],
                  background=[("readonly", SURFACE)], foreground=[("readonly", TEXT_MAIN)],
                  selectbackground=[("readonly", SURFACE)],
                  selectforeground=[("readonly", TEXT_MAIN)], arrowcolor=[("active", TEXT_MAIN)],
                  bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)],
                  darkcolor=[("focus", ACCENT)])
        self.root.option_add("*TCombobox*Listbox.background", BG_PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT_MAIN)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", BG)
        self.root.option_add("*TCombobox*Listbox.font", f"{{{SANS}}} 10")
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT_MAIN)
        style.configure("TCheckbutton", background=BG, foreground=TEXT_MAIN, focuscolor=BG)
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("TButton", background=SURFACE, foreground=TEXT_MAIN,
                        bordercolor=SURFACE, focuscolor=SURFACE, relief="flat", padding=(14, 7))
        style.map("TButton", background=[("active", SURFACE_HI)])
        # Primary (green) button for the main action, e.g. Save in the Settings dialog.
        style.configure("Primary.TButton", background=BTN_START, foreground=BG,
                        bordercolor=BTN_START, focuscolor=BTN_START, relief="flat",
                        padding=(18, 7), font=(SANS, 10, "bold"))
        style.map("Primary.TButton", background=[("active", "#b8f0b2")])
        style.configure("TEntry", fieldbackground=SURFACE, foreground=TEXT_MAIN,
                        bordercolor=SURFACE, lightcolor=SURFACE, darkcolor=SURFACE,
                        insertcolor=TEXT_MAIN, padding=5)
        style.map("TEntry", bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)],
                  darkcolor=[("focus", ACCENT)])
        style.configure("TSpinbox", fieldbackground=SURFACE, foreground=TEXT_MAIN,
                        bordercolor=SURFACE, lightcolor=SURFACE, darkcolor=SURFACE,
                        arrowcolor=TEXT_SUB, insertcolor=TEXT_MAIN, padding=5)
        style.map("TSpinbox", bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)],
                  darkcolor=[("focus", ACCENT)], arrowcolor=[("active", TEXT_MAIN)])
        # Flat, borderless tabs: dim when idle, an accent label when selected.
        style.configure("TNotebook", background=BG, bordercolor=BG, borderwidth=0,
                        tabmargins=(6, 6, 6, 0))
        style.configure("TNotebook.Tab", background=BG, foreground=TEXT_DIM, bordercolor=BG,
                        borderwidth=0, padding=(16, 9), font=(SANS, 10))
        style.map("TNotebook.Tab", background=[("selected", SURFACE), ("active", BG_PANEL)],
                  foreground=[("selected", ACCENT), ("active", TEXT_SUB)])
        style.configure("TSeparator", background=SURFACE)

    def _set_model_choices(self):
        choices = list(config.WHISPER_MODELS)
        if self.settings["whisper_model"] not in choices:
            choices.append(self.settings["whisper_model"])   # custom name from settings.json
        self._model_box.config(values=choices)

    def _saving_to_text(self) -> str:
        return t("app.saving_to", path=self._output_dir())

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
        self._live_var.set(live_label(self.settings["live_mode"]))
        self._live_hint_var.set(live_hint(self.settings["live_mode"]))
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

    def _show_welcome(self):
        """First-run setup wizard; writes the chosen settings, then picks them up."""
        def done(open_settings: bool):
            self._reload_settings()
            if open_settings:
                self._open_settings()
        try:
            WelcomeDialog(self.root, self.settings, done)
        except Exception as exc:
            self._log(f"Welcome setup skipped: {exc}")

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

    def _check_tray(self, attempt: int = 0):
        if self._tray is None:
            return
        if self._tray.error:
            self._log(f"Tray icon unavailable: {self._tray.error}")
            self._tray = None
            return
        if not self._tray.docked():
            if attempt < 2:      # a real tray may be slow to dock; retry before giving up
                self.root.after(2500, lambda: self._check_tray(attempt + 1))
                return
            self._log("No system tray on this desktop; tray icon and "
                      "minimize-to-tray are disabled.")
            self._tray.stop()
            self._tray = None
            self._show_window()   # never leave the window hidden with no icon to restore it

    def _apply_meeting_watch(self):
        want = self.settings["meeting_prompt"] and meetings.supported()
        if want and self._watcher is None:
            self._watcher = meetings.MeetingWatcher(
                lambda: self.settings["meeting_apps"],
                on_start=lambda app: self.root.after(0, self._call_started, app),
                on_end=lambda app: self.root.after(0, self._call_ended, app),
            )
            self._watcher.start()
            self._log("Watching for meeting apps that start using the microphone.")
            if not meetings.capture_api_ok():
                self._log("  Heads-up: the Core Audio detector isn't available, so detection "
                          "falls back to the registry, which can miss meetings on Windows 11.")
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
                self._tray.notify(t("app.tray.minimized"))

    def _update_tray(self, status: str = ""):
        if self._tray is not None:
            try:
                self._tray.update(status)
            except Exception:
                pass

    # ---- Meeting prompts ------------------------------------------------

    def _call_started(self, app: str):
        if self._recording:
            self._log(f"{app} started using the microphone (already recording).")
            return
        if app in self._declined:
            self._log(f"{app} started using the microphone (dismissed earlier; not asking).")
            return
        self._log(f"{app} started using the microphone - offering to record.")
        # The in-window prompt below is the only cue, on purpose: a tray toast here would
        # just duplicate it. (The minimize-to-tray hint toast is a separate thing.)
        self._prompt(t("app.prompt.started.title", app=app),
                     t("app.prompt.started.body"),
                     t("app.prompt.record"), lambda: self._start(trigger=app),
                     on_decline=lambda: self._declined.add(app), app=app)

    def _call_ended(self, app: str):
        self._log(f"{app} stopped using the microphone.")
        self._declined.discard(app)
        if self._prompt_app == app and not self._recording:
            self._close_prompt()       # the call ended before anyone answered
        if self._recording and self._trigger_app == app:
            self._prompt(t("app.prompt.ended.title", app=app),
                         t("app.prompt.ended.body"),
                         t("app.tray.stop"), self._stop, app=app)

    def _prompt(self, title: str, message: str, action_text: str, action,
                on_decline=None, app: str | None = None, timeout_ms: int = 60_000):
        """A small borderless always-on-top question in the corner of the screen."""
        self._close_prompt()
        win = tk.Toplevel(self.root)
        win.withdraw()                 # build it hidden, reveal once placed (no corner flash)
        win.overrideredirect(True)     # no title bar -- a proper popup
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.configure(bg=SURFACE_HI)   # 1px of this shows around the body as a hairline border
        body = tk.Frame(win, bg=BG)
        body.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        tk.Label(body, text=title, font=(SANS, 11, "bold"), bg=BG, fg=TEXT_MAIN,
                 wraplength=320, justify="left").pack(anchor="w", padx=16, pady=(14, 4))
        tk.Label(body, text=message, font=FONT_BODY, bg=BG, fg=TEXT_DIM,
                 wraplength=320, justify="left").pack(anchor="w", padx=16)
        row = tk.Frame(body, bg=BG)
        row.pack(anchor="e", padx=16, pady=14)

        def decline():
            self._close_prompt()
            if on_decline:
                on_decline()

        def accept():
            self._close_prompt()
            action()

        self._small_button(row, t("app.prompt.not_now"), decline)
        make_button(row, action_text, accept, BTN_START, BG, (SANS, 10, "bold"),
                    padx=12, pady=4).pack(side=tk.LEFT, padx=3)
        win.protocol("WM_DELETE_WINDOW", decline)   # kept for programmatic/edge dismissal
        win.update_idletasks()
        x = win.winfo_screenwidth() - win.winfo_reqwidth() - 24
        y = win.winfo_screenheight() - win.winfo_reqheight() - 72
        win.geometry(f"+{x}+{y}")
        osutil.set_rounded_corners(win)   # Windows 11 rounded corners
        win.deiconify()      # reveal it, positioned (also brings it up if the app was minimized)
        win.lift()
        try:
            win.bell()       # an audible cue to go with the pop-up
        except Exception:
            pass
        osutil.flash_taskbar(self.root)   # and flash the taskbar, in case it's behind a call
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
        self._btn.config(text=t("app.btn.stop"), bg=BTN_STOP, activebackground=BTN_STOP)
        self._set_status("recording")
        mic, system = device_names(s["microphone"], s["system_audio"])
        self._log(f"Started at {started.strftime('%H:%M:%S')}  (mic: {mic}; system: {system})")
        self._update_tray(t("app.status.recording"))
        if live is not None:
            live.start()
            self._apply_live_layout()
            self._clear_live(recording=True)
            self._log(f"Live mode '{s['live_mode']}' is on "
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

        self._btn.config(text=t("app.btn.start"), bg=BTN_START, activebackground=BTN_START)
        self._set_status("ready")
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

    def _make_live_transcriber(self, s: dict, start_offset: float = 0.0):
        """A LiveTranscriber for this recording, or None if live mode is off.

        start_offset is the recording's elapsed time when Live is switched on mid-way,
        so new segments line up after the catch-up pass fills in the earlier part."""
        if s["live_mode"] == "off":
            return None
        if BACKEND is None:
            self._log("Live transcription needs a transcription backend; staying off.")
            return None
        return LiveTranscriber(
            model=s["live_model"] or "base", device=s["device"],
            language=s["language"].strip() or None,
            interval=max(2, int(s["live_interval"])),
            silence_threshold=s["silence_threshold"], start_offset=start_offset,
            on_segments=self._on_live_segments, log=self._log_from_thread)

    def _on_live_mode_changed(self, _event=None):
        mode = live_mode_by_label(self._live_var.get())
        changed = mode != self.settings["live_mode"]
        self.settings = {**self.settings, "live_mode": mode}
        try:
            config.update_setting("live_mode", mode)
            self._settings_mtime = self._mtime()
        except OSError as exc:
            self._log(f"Could not save settings: {exc}")
        self._live_hint_var.set(live_hint(mode))
        self._apply_live_layout(resize=changed)
        if changed and self._recording:
            self._switch_live_while_recording(mode)   # take effect now, not next time
        elif changed:
            self._log(f"Live mode: {mode}"
                      + ("" if mode == "off" else " - starts with the next recording."))

    def _switch_live_while_recording(self, mode: str):
        """Apply a Live-menu change to the recording in progress: start or stop the
        live transcriber and the auto-notes timer, instead of waiting for the next one."""
        if mode == "off":
            self._cancel_live_notes()
            self._teardown_live(in_background=True)
            self._log("Live transcription stopped for this recording.")
            return
        if self._live is None:                 # was off: spin up a transcriber now
            elapsed = self._recorder.duration_seconds if self._recorder is not None else 0.0
            live = self._make_live_transcriber(self.settings, start_offset=elapsed)
            if live is None:
                return                         # no backend; _make_live_transcriber logged why
            self._live = live
            self._clear_live(recording=True)
            if self._recorder is not None:
                self._recorder.on_audio = live.feed_stereo
            live.start()
            self._log(f"Live mode '{mode}' is on for this recording.")
            if (self.settings["live_catchup"] and elapsed >= _CATCHUP_MIN_SECONDS
                    and self._recording_wav is not None):
                self._start_catchup(live, self._recording_wav, elapsed)
        else:
            self._log(f"Live mode: {mode}.")
        if mode == "auto-notes":
            if self._live_notes_timer is None:
                self._schedule_live_notes()
        else:
            self._cancel_live_notes()

    def _cancel_live_notes(self):
        if self._live_notes_timer:
            self.root.after_cancel(self._live_notes_timer)
            self._live_notes_timer = None

    def _teardown_live(self, in_background: bool):
        """Detach the live transcriber from the recorder and stop it. Stopping runs
        a final transcription pass, so off the UI thread when asked (in_background)."""
        live = self._live
        self._live = None
        if self._recorder is not None:
            self._recorder.on_audio = None
        if live is None:
            return
        if in_background:
            threading.Thread(target=self._stop_live_quietly, args=(live,), daemon=True).start()
        else:
            self._stop_live_quietly(live)

    def _stop_live_quietly(self, live):
        try:
            live.stop()
        except Exception as exc:
            self._log_from_thread(f"Live transcription stop error: {exc}")

    # ---- Catch-up (transcribe the part recorded before Live was turned on) ----

    def _start_catchup(self, live, wav, elapsed: float):
        """Transcribe the already-recorded audio in the background, so the live
        transcript (and any notes) also cover what was said before Live was on."""
        self._log(f"Catching up on the earlier {elapsed / 60:.0f} min in the background "
                  "(turn this off in Settings > Transcription if it's too CPU-heavy).")
        threading.Thread(target=self._catchup_worker, args=(live, wav, dict(self.settings)),
                         daemon=True).start()

    def _catchup_worker(self, live, wav, s: dict):
        try:
            segs = transcribe_recording(
                wav, Transcriber(log=self._log_from_thread),
                model=s["live_model"] or "base", device=s["device"],
                language=s["language"].strip() or None,
                speaker_labels=False, silence_threshold=s["silence_threshold"])
        except Exception as exc:
            self._log_from_thread(f"Catch-up transcription failed: {exc}")
            return
        self.root.after(0, self._finish_catchup, live, segs)

    def _finish_catchup(self, live, segs):
        if self._live is not live:
            return      # Live was turned off or the recording stopped: discard the catch-up
        if segs:
            live.add_segments(segs)          # so notes see the whole meeting
            self._prepend_live_segments(segs)  # and show it above the live lines, in order
        self._log(f"Caught up: added {len(segs)} earlier line(s) to the live transcript.")

    def _prepend_live_segments(self, segs):
        """Insert catch-up segments at the top of the transcript (they precede the live ones)."""
        if self._live_text is None or not self._live_text.winfo_exists():
            return
        lines = "".join(f"[{format_timestamp(s.start)}]  "
                        f"{s.speaker + ': ' if s.speaker else ''}{s.text}\n" for s in segs)
        self._live_text.config(state=tk.NORMAL)
        self._live_text.insert("1.0", lines)
        self._live_text.config(state=tk.DISABLED)
        self._live_has_text = True           # keep the next live line from wiping the placeholder

    def _apply_live_layout(self, resize: bool = False):
        """Show or hide the right-hand live panels to match the mode, and size
        the window: narrow (controls only) when off, wide (with side panels) live."""
        mode = self.settings["live_mode"]
        show = mode != "off"
        notes = mode in ("transcript+notes", "auto-notes")
        if show:
            if not self._live_frame.winfo_manager():
                self._live_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            if notes and not self._notes_frame.winfo_manager():
                self._notes_frame.pack(fill=tk.BOTH, expand=True)
            elif not notes:
                self._notes_frame.pack_forget()
            if not self._recording and not self._live_has_text:
                self._clear_live(recording=False)
        else:
            self._live_frame.pack_forget()
        if resize:
            if not show:
                w, h = LEFT_W + 16, 560
            else:
                w, h = LEFT_W + 540, (660 if notes else 600)
            self.root.geometry(f"{w}x{h}")

    def _clear_live(self, recording: bool):
        """Empty the transcript (with a placeholder when idle) and the notes."""
        if self._live_text is not None and self._live_text.winfo_exists():
            self._live_text.config(state=tk.NORMAL)
            self._live_text.delete("1.0", tk.END)
            if not recording:
                self._live_text.insert("1.0", t("app.live.placeholder"), "placeholder")
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
            self._set_live_notes(t("app.notes.unavailable", reason=reason))
            return
        text = self._live.transcript_text()
        if not text.strip():
            self._set_live_notes(t("app.notes.nothing_yet"))
            return
        self._live_notes_running = True
        if self._live_notes_btn is not None and self._live_notes_btn.winfo_exists():
            self._live_notes_btn.config(text=t("app.btn.working"))
        threading.Thread(target=self._live_notes_worker,
                         args=(text, dict(self.settings)), daemon=True).start()

    def _live_notes_worker(self, text: str, settings: dict):
        try:
            notes, markdown = summarize.summarize(text, settings), True
        except Exception as exc:
            notes, markdown = t("app.notes.failed", detail=exc), False
        self.root.after(0, self._finish_live_notes, notes, markdown)

    def _finish_live_notes(self, notes: str, markdown: bool = True):
        self._live_notes_running = False
        if self._live_notes_btn is not None and self._live_notes_btn.winfo_exists():
            self._live_notes_btn.config(text=t("app.btn.generate_now"))
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
        self._update_tray(f"{t('app.status.recording')} {mm:02d}:{ss:02d}")

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

    def _set_status(self, key: str):
        self._status_var.set(t(f"app.status.{key}"))
        color = {"recording": BTN_STOP, "ready": TEXT_SUB}.get(key, ACCENT)
        self._status_lbl.config(fg=color)

    def _refresh_status(self):
        """Update the queue label and the 'pending' button count (main thread)."""
        with self._pending_lock:
            pending_set = set(self._pending)
        active = len(pending_set)
        self._queue_var.set(t("app.queue.processing", n=active) if active else "")
        waiting = sum(1 for f in self._pending_files() if f not in pending_set)
        self._pending_btn.config(
            text=t("app.btn.pending_count", n=waiting) if waiting else t("app.btn.pending"))

    def _render_log(self):
        """Redraw the activity card: newest message on top and brightest, older ones faded."""
        for widget in self._log_inner.winfo_children():
            widget.destroy()
        for i, line in enumerate(self._log_lines):
            tk.Label(self._log_inner, text=line, bg=BG_PANEL,
                     fg=TEXT_SUB if i == 0 else TEXT_DIM, font=(SANS, 8),
                     wraplength=LEFT_W - 60, justify="left", anchor="w").pack(
                         fill=tk.X, anchor="w", pady=1)

    def _log(self, msg: str, window: bool = True):
        now = datetime.now()
        if window:
            self._log_lines.insert(0, msg)
            del self._log_lines[4:]          # keep only the few most recent on screen
            self._render_log()
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
            busy.append(t("app.quit.busy.recording"))
        if active:
            busy.append(t("app.quit.busy.processing", n=active))

        if busy:
            self._show_window()
            what = f" {t('app.and')} ".join(busy)
            if not messagebox.askokcancel(t("app.quit.title"),
                                          t("app.quit.body", what=what)):
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
