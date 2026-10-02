"""Tkinter GUI: one button to start/stop; transcription and summaries run in the background."""

import platform
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

from . import __version__, config, files, meetings, osutil, summarize, tray
from .audio import DualChannelRecorder, device_names, repair_wav, soundcard_available
from .hotkey import GlobalHotkey
from .settings_dialog import SettingsDialog
from .transcribe import BACKEND, Transcriber, format_transcript, to_srt, transcribe_recording

# -- Path resolution (works both in dev and when frozen by PyInstaller) --
# PyInstaller 6 puts bundled data files in _internal/ (sys._MEIPASS), not next to the .exe.
if getattr(sys, "frozen", False):
    _HERE = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    _HERE = Path(__file__).resolve().parent.parent
LOGO = _HERE / "assets" / "logo.png"

# -- Look ------------------------------------------------------------------
SANS, MONO  = osutil.FONTS
BG          = "#1e1e2e"
BTN_START   = "#4ade80"   # green
BTN_STOP    = "#f87171"   # red
BTN_SMALL   = "#313244"
TEXT_MAIN   = "#cdd6f4"
TEXT_DIM    = "#6c7086"
FONT_BODY   = (SANS, 10)
FONT_TIMER  = (SANS, 36, "bold")
FONT_STATUS = (SANS, 11)
FONT_LOG    = (MONO, 9)
TITLE       = "Meeting Recorder"


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
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        if osutil.MACOS:
            self.root.createcommand("tk::mac::Quit", self._on_close)

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
        tk.Frame(self.root, bg=BG, height=20).pack()

        self._status_var = tk.StringVar(value="Ready")
        tk.Label(self.root, textvariable=self._status_var,
                 font=FONT_STATUS, bg=BG, fg=TEXT_DIM).pack(padx=24)

        self._timer_var = tk.StringVar(value="00:00")
        tk.Label(self.root, textvariable=self._timer_var,
                 font=FONT_TIMER, bg=BG, fg=TEXT_MAIN).pack(pady=(6, 14))

        self._btn = make_button(self.root, "Start Recording", self._toggle, BTN_START, BG,
                                (SANS, 13, "bold"), width=22, height=2)
        self._btn.pack(pady=(0, 18))

        # model selector + always-on-top row
        row = tk.Frame(self.root, bg=BG)
        row.pack(pady=(0, 4))
        tk.Label(row, text="Model:", font=FONT_BODY, bg=BG,
                 fg=TEXT_DIM).pack(side=tk.LEFT, padx=(0, 6))
        self._model_var = tk.StringVar(value=self.settings["whisper_model"])
        self._model_box = ttk.Combobox(row, textvariable=self._model_var, state="readonly",
                                       width=14, font=FONT_BODY)
        self._set_model_choices()
        self._model_box.pack(side=tk.LEFT, padx=(0, 16))
        self._model_box.bind("<<ComboboxSelected>>", self._on_model_changed)

        self._ontop_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            row, text="Always on top",
            variable=self._ontop_var, command=self._toggle_ontop,
            font=FONT_BODY, bg=BG, fg=TEXT_DIM,
            selectcolor=BG, activebackground=BG, activeforeground=TEXT_DIM,
            relief=tk.FLAT, bd=0,
        ).pack(side=tk.LEFT)

        # transcription/summary queue status
        self._queue_var = tk.StringVar(value="")
        tk.Label(self.root, textvariable=self._queue_var,
                 font=FONT_BODY, bg=BG, fg=TEXT_DIM).pack(pady=(10, 2))

        # manual triggers
        btn_row = tk.Frame(self.root, bg=BG)
        btn_row.pack(pady=(0, 6))
        self._pending_btn = self._small_button(btn_row, "Process pending", self._process_pending)
        self._small_button(btn_row, "Open folder", self._open_folder)
        self._small_button(btn_row, "Settings", self._open_settings)

        tk.Frame(self.root, bg=BTN_SMALL, height=1).pack(fill=tk.X, pady=(12, 0))

        self._log_box = scrolledtext.ScrolledText(
            self.root, font=FONT_LOG, bg="#181825", fg=TEXT_DIM,
            insertbackground=TEXT_MAIN, relief=tk.FLAT,
            width=50, height=7, state=tk.DISABLED,
            padx=8, pady=6,
        )
        self._log_box.pack(fill=tk.X)

        self._dir_var = tk.StringVar(value=self._saving_to_text())
        tk.Label(self.root, textvariable=self._dir_var, font=(SANS, 8),
                 bg="#181825", fg=TEXT_DIM, anchor="w").pack(fill=tk.X, padx=8)

        tk.Frame(self.root, bg=BG, height=10).pack()

    def _small_button(self, parent, text, command):
        btn = make_button(parent, text, command, BTN_SMALL, TEXT_MAIN, (SANS, 10),
                          active_bg="#45475a", padx=10, pady=4)
        btn.pack(side=tk.LEFT, padx=3)
        return btn

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
        self._set_model_choices()
        self._dir_var.set(self._saving_to_text())
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
        recorder = DualChannelRecorder(wav, silence_threshold=s["silence_threshold"],
                                       microphone=s["microphone"],
                                       system_audio=s["system_audio"])
        try:
            recorder.start()
        except OSError as exc:
            self._log(f"Could not start recording: {exc}")
            return

        self._recording, self._start_time = True, started
        self._recorder, self._recording_wav = recorder, wav
        self._trigger_app = trigger
        self._errors_seen = 0
        self._warned_silent_mic = False
        self._btn.config(text="Stop Recording", bg=BTN_STOP, activebackground=BTN_STOP)
        self._set_status("Recording")
        mic, system = device_names(s["microphone"], s["system_audio"])
        self._log(f"Started at {started.strftime('%H:%M:%S')}  (mic: {mic}; system: {system})")
        self._update_tray("recording")
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
        self._recorder = self._recording_wav = None
        # Reserve the path so a "Process pending" scan can't grab the file
        # before _finalize has closed it and queued it.
        with self._pending_lock:
            self._pending.add(wav)

        self._btn.config(text="Start Recording", bg=BTN_START, activebackground=BTN_START)
        self._set_status("Ready")
        self._update_tray()
        self._refresh_status()
        threading.Thread(target=self._finalize, args=(recorder, wav), daemon=True).start()

    def _finalize(self, recorder: DualChannelRecorder, wav: Path):
        """Stop the audio streams, close the .wav and queue it (worker thread)."""
        frames = recorder.stop()
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
    root.geometry("420x470")
    set_window_icon(root)
    App(root)
    root.mainloop()
