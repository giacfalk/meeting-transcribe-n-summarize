"""Tkinter GUI: one button to start/stop; transcription and summaries run in the background."""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

from . import __version__, config, files, summarize
from .audio import DualChannelRecorder, device_names, repair_wav, soundcard_available
from .hotkey import GlobalHotkey
from .transcribe import BACKEND, Transcriber, format_transcript, to_srt, transcribe_recording

# -- Path resolution (works both in dev and when frozen by PyInstaller) --
# PyInstaller 6 puts bundled data files in _internal/ (sys._MEIPASS), not next to the .exe.
if getattr(sys, "frozen", False):
    _HERE = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    _HERE = Path(__file__).resolve().parent.parent

# -- Look ------------------------------------------------------------------
BG          = "#1e1e2e"
BTN_START   = "#4ade80"   # green
BTN_STOP    = "#f87171"   # red
TEXT_MAIN   = "#cdd6f4"
TEXT_DIM    = "#6c7086"
FONT_BODY   = ("Segoe UI", 10)
FONT_TIMER  = ("Segoe UI", 36, "bold")
FONT_STATUS = ("Segoe UI", 11)
FONT_LOG    = ("Consolas",  9)
TITLE       = "Meeting Recorder"


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(TITLE)
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.settings, setting_warnings = config.load_settings()
        self._settings_mtime = self._mtime()

        self._recording   = False
        self._start_time: datetime | None = None
        self._timer_id    = None
        self._recorder: DualChannelRecorder | None = None
        self._recording_wav: Path | None = None   # file being recorded right now
        self._errors_seen = 0
        self._warned_silent_mic = False
        self._hotkey: GlobalHotkey | None = None
        self._transcriber = Transcriber(log=self._log_from_thread)

        # Background work. Stopping a recording drops its .wav on this queue; one
        # worker thread transcribes (.wav) or summarizes (.txt) jobs one at a time,
        # so a new recording can begin while earlier ones are still processing.
        self._job_queue: queue.Queue = queue.Queue()
        self._pending: set = set()           # paths queued or in progress
        self._pending_lock = threading.Lock()

        self._build()
        self._log(f"Meeting Recorder v{__version__}")
        for w in setting_warnings:
            self._log(w)
        if BACKEND is None:
            messagebox.showerror(TITLE, "No transcription backend found.\n\n"
                                 "Install one with:  pip install faster-whisper")
        if not soundcard_available():
            messagebox.showerror(TITLE, "Audio capture is unavailable: the 'soundcard' "
                                 "package could not be loaded.")
        self._report_summary_backend()
        self._apply_hotkey()

        self._worker = threading.Thread(target=self._process_worker, daemon=True)
        self._worker.start()
        self._refresh_status()               # reflect any leftover files

        # Pick up edits to settings.json when the user comes back to the window.
        self.root.bind("<FocusIn>", lambda _e: self._reload_settings())

    # ---- UI construction ------------------------------------------------

    def _build(self):
        tk.Frame(self.root, bg=BG, height=20).pack()

        self._status_var = tk.StringVar(value="Ready")
        tk.Label(self.root, textvariable=self._status_var,
                 font=FONT_STATUS, bg=BG, fg=TEXT_DIM).pack(padx=24)

        self._timer_var = tk.StringVar(value="00:00")
        tk.Label(self.root, textvariable=self._timer_var,
                 font=FONT_TIMER, bg=BG, fg=TEXT_MAIN).pack(pady=(6, 14))

        self._btn = tk.Button(
            self.root, text="Start Recording",
            font=("Segoe UI", 13, "bold"),
            bg=BTN_START, fg=BG,
            activebackground=BTN_START, activeforeground=BG,
            relief=tk.FLAT, cursor="hand2",
            width=22, height=2,
            command=self._toggle,
        )
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

        tk.Frame(self.root, bg="#313244", height=1).pack(fill=tk.X, pady=(12, 0))

        self._log_box = scrolledtext.ScrolledText(
            self.root, font=FONT_LOG, bg="#181825", fg=TEXT_DIM,
            insertbackground=TEXT_MAIN, relief=tk.FLAT,
            width=50, height=7, state=tk.DISABLED,
            padx=8, pady=6,
        )
        self._log_box.pack(fill=tk.X)

        self._dir_var = tk.StringVar(value=self._saving_to_text())
        tk.Label(self.root, textvariable=self._dir_var, font=("Segoe UI", 8),
                 bg="#181825", fg=TEXT_DIM, anchor="w").pack(fill=tk.X, padx=8)

        tk.Frame(self.root, bg=BG, height=10).pack()

    def _small_button(self, parent, text, command) -> tk.Button:
        btn = tk.Button(
            parent, text=text, font=("Segoe UI", 10),
            bg="#313244", fg=TEXT_MAIN,
            activebackground="#45475a", activeforeground=TEXT_MAIN,
            relief=tk.FLAT, cursor="hand2", bd=0, padx=10, pady=4,
            command=command,
        )
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
        self._log("Settings reloaded.")
        self._model_var.set(self.settings["whisper_model"])
        self._set_model_choices()
        self._dir_var.set(self._saving_to_text())
        if self.settings["hotkey"] != old["hotkey"]:
            self._apply_hotkey()
        if (self.settings["summary_backend"], self.settings["summary_model"]) != \
                (old["summary_backend"], old["summary_model"]):
            self._report_summary_backend()
        self._refresh_status()

    def _open_settings(self):
        path = config.settings_path()
        if not path.exists():
            config.save_settings(self.settings, path)
        try:
            subprocess.Popen(["notepad.exe", str(path)])
            self._log("Edit and save settings.json; changes apply when you "
                      "return to this window.")
        except OSError as exc:
            self._log(f"Could not open {path}: {exc}")

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

    # ---- Recording toggle -----------------------------------------------

    def _toggle(self):
        if not self._recording:
            self._start()
        else:
            self._stop()

    def _start(self):
        self._reload_settings()
        started = datetime.now()
        wav = files.new_recording_path(self._output_dir(), started)
        recorder = DualChannelRecorder(wav, silence_threshold=self.settings["silence_threshold"],
                                       microphone=self.settings["microphone"])
        try:
            recorder.start()
        except OSError as exc:
            self._log(f"Could not start recording: {exc}")
            return

        self._recording, self._start_time = True, started
        self._recorder, self._recording_wav = recorder, wav
        self._errors_seen = 0
        self._warned_silent_mic = False
        self._btn.config(text="Stop Recording", bg=BTN_STOP, activebackground=BTN_STOP)
        self._set_status("Recording")
        mic, speaker = device_names(self.settings["microphone"])
        self._log(f"Started at {started.strftime('%H:%M:%S')}  (mic: {mic}; system: {speaker})")
        self._tick()

    def _stop(self):
        """Stop recording and hand the file off for background transcription.

        Returns to the idle state immediately so a new recording can begin
        right away, even while previous recordings are still transcribing.
        """
        self._recording = False
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

        for err in self._recorder.errors[self._errors_seen:]:
            self._log(f"Not recording {err}")
        self._errors_seen = len(self._recorder.errors)
        if not self._warned_silent_mic and self._recorder.mic_is_digital_silence():
            self._warned_silent_mic = True
            self._log("WARNING: the microphone is sending pure silence (muted or not "
                      "connected?). Check Windows' default input, or set 'microphone' "
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

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_box.config(state=tk.NORMAL)
        self._log_box.insert(tk.END, f"{ts}  {msg}\n")
        self._log_box.see(tk.END)
        self._log_box.config(state=tk.DISABLED)

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
            os.startfile(str(out))   # Windows: open in Explorer
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

        if busy and not messagebox.askokcancel(
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
        if self._hotkey:
            self._hotkey.stop()
        self.root.destroy()


# -- Entry point -----------------------------------------------------------
def main():
    root = tk.Tk()
    root.geometry("420x470")
    icon = _HERE / "icon.ico"
    if icon.exists():
        try:
            root.iconbitmap(str(icon))
        except Exception:
            pass
    App(root)
    root.mainloop()
