#!/usr/bin/env python3
"""
Meeting Recorder
GUI tool: click the button to start/stop recording.
Audio (mic + system loopback) is transcribed automatically when you stop.
"""

import os
import sys
import shutil
import warnings
import threading
import time
import queue
import subprocess
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
from datetime import datetime
from pathlib import Path

import numpy as np
import soundfile as sf

warnings.filterwarnings("ignore", message="data discontinuity", category=RuntimeWarning)

try:
    import soundcard as sc
except ImportError:
    sys.exit("ERROR: soundcard not installed.  Run: pip install soundcard")

try:
    from faster_whisper import WhisperModel
    _BACKEND = "faster_whisper"
except ImportError:
    try:
        import whisper as _openai_whisper
        _BACKEND = "whisper"
    except ImportError:
        sys.exit("ERROR: no transcription backend.  Run: pip install faster-whisper")


# -- Path resolution (works both in dev and when frozen by PyInstaller) --
_HERE = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent

# Persistent output location (NOT next to the .exe, so rebuilds never wipe it).
# Prefer the OneDrive-synced Documents folder; fall back to local Documents.
_ONEDRIVE = os.environ.get("OneDrive") or os.environ.get("OneDriveCommercial")
_DOCS = (Path(_ONEDRIVE) / "Documents") if _ONEDRIVE else (Path.home() / "Documents")

# -- Configuration -------------------------------------------------------
OUTPUT_DIR      = _DOCS / "Meeting Recorder" / "recordings"
WHISPER_MODEL   = "base"   # tiny | base | small | medium | large-v2
KEEP_AUDIO_FILE = False    # set True to keep the .wav alongside the .txt
SAMPLE_RATE     = 16_000

# Auto-stop a recording after this many seconds with no incoming audio.
SILENCE_TIMEOUT   = 120     # seconds of silence before auto-stop (2 minutes)
SILENCE_THRESHOLD = 0.01    # chunk RMS at/below this counts as silence (0.0-1.0)

# -- Summarization (via the Claude CLI) ---------------------------------
# Uses the locally-installed `claude` command, which authenticates with your
# existing Claude login -- no API key needed. Summaries are written into a
# "summary" subfolder of OUTPUT_DIR; transcripts are never modified or deleted.
ENABLE_SUMMARY = True       # auto-summarize each transcript with the Claude CLI
SUMMARY_MODEL  = ""         # "" = the CLI's default model; or "opus"/"sonnet"/"fable"
SUMMARY_SUBDIR = "summary"  # summaries go in OUTPUT_DIR/<this>/

SUMMARY_SYSTEM_PROMPT = (
    "You are a meeting-summarization assistant. The user's message is a raw "
    "meeting transcript; it may contain [MM:SS] timestamps and may be in any "
    "language. Write a concise summary IN THE SAME LANGUAGE as the transcript, "
    "structured with these headings:\n"
    "- Overview: 2-3 sentences.\n"
    "- Key points: bullet list.\n"
    "- Decisions: bullet list (omit this section if there are none).\n"
    "- Action items: bullets with owners/deadlines when mentioned (omit if none).\n"
    "Base everything only on the transcript; do not invent information. "
    "Output only the summary itself, with no preamble or sign-off."
)

# Hide the console window when launching the CLI from the windowed .exe.
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _find_claude_cli() -> str:
    """Absolute path to the Claude CLI, or '' if it can't be found."""
    exe = shutil.which("claude")
    if exe:
        return exe
    for cand in (Path.home() / ".local" / "bin" / "claude.exe",
                 Path.home() / ".local" / "bin" / "claude"):
        if cand.exists():
            return str(cand)
    return ""


# -- Audio recording -----------------------------------------------------
class DualChannelRecorder:
    """Records microphone + system-audio loopback simultaneously."""

    _CHUNK = SAMPLE_RATE // 2  # 0.5 s per read

    def __init__(self):
        self.is_recording = False
        self._mic_chunks:  list = []
        self._loop_chunks: list = []
        self._lock = threading.Lock()
        self._threads: list = []
        self._last_sound_time: float | None = None

    def start(self):
        self.is_recording = True
        self._mic_chunks  = []
        self._loop_chunks = []
        self._last_sound_time = time.monotonic()
        t1 = threading.Thread(target=self._record_mic,      daemon=True)
        t2 = threading.Thread(target=self._record_loopback, daemon=True)
        t1.start(); t2.start()
        self._threads = [t1, t2]

    def stop(self):
        self.is_recording = False
        for t in self._threads:
            t.join(timeout=6)

    def _record_mic(self):
        try:
            with sc.default_microphone().recorder(
                samplerate=SAMPLE_RATE, channels=1
            ) as rec:
                while self.is_recording:
                    data = rec.record(numframes=self._CHUNK)
                    samples = data.flatten()
                    if self._rms(samples) > SILENCE_THRESHOLD:
                        self._last_sound_time = time.monotonic()
                    with self._lock:
                        self._mic_chunks.append(samples)
        except Exception as exc:
            print(f"[mic] {exc}")

    def _record_loopback(self):
        try:
            devs = [m for m in sc.all_microphones(include_loopback=True)
                    if m.isloopback]
            if not devs:
                return
            default_name = sc.default_speaker().name
            dev = next((d for d in devs if default_name in d.name), devs[0])
            with dev.recorder(samplerate=SAMPLE_RATE, channels=2) as rec:
                while self.is_recording:
                    data = rec.record(numframes=self._CHUNK)
                    samples = data.mean(axis=1)
                    if self._rms(samples) > SILENCE_THRESHOLD:
                        self._last_sound_time = time.monotonic()
                    with self._lock:
                        self._loop_chunks.append(samples)
        except Exception as exc:
            print(f"[loopback] {exc}")

    def save_wav(self, path: Path) -> bool:
        with self._lock:
            mic  = np.concatenate(self._mic_chunks)  if self._mic_chunks  else None
            loop = np.concatenate(self._loop_chunks) if self._loop_chunks else None

        if mic is None and loop is None:
            return False

        if   mic  is None: mixed = loop
        elif loop is None: mixed = mic
        else:
            n = min(len(mic), len(loop))
            mixed = np.clip(mic[:n] * 0.6 + loop[:n] * 0.85, -1.0, 1.0)

        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(path), mixed, SAMPLE_RATE)
        return True

    @property
    def duration_seconds(self) -> float:
        with self._lock:
            chunks = self._mic_chunks or self._loop_chunks
            if not chunks:
                return 0.0
            return len(np.concatenate(chunks)) / SAMPLE_RATE

    @property
    def seconds_since_sound(self) -> float:
        """Seconds since the last chunk whose level was above the threshold."""
        if self._last_sound_time is None:
            return 0.0
        return time.monotonic() - self._last_sound_time

    @staticmethod
    def _rms(samples) -> float:
        """Root-mean-square level of a chunk (0.0 = digital silence)."""
        if samples.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(samples ** 2)))


# -- GUI -----------------------------------------------------------------
BG          = "#1e1e2e"
BTN_START   = "#4ade80"   # green
BTN_STOP    = "#f87171"   # red
BTN_BUSY    = "#fbbf24"   # amber
TEXT_MAIN   = "#cdd6f4"
TEXT_DIM    = "#6c7086"
FONT_BODY   = ("Segoe UI", 10)
FONT_TIMER  = ("Segoe UI", 36, "bold")
FONT_STATUS = ("Segoe UI", 11)
FONT_LOG    = ("Consolas",  9)


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Meeting Recorder")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._recording   = False
        self._start_time: datetime | None = None
        self._timer_id    = None
        self._recorder    = DualChannelRecorder()
        self._model       = None             # loaded lazily by the worker thread
        self._model_size  = WHISPER_MODEL    # plain copy of the combobox value

        # Background transcription. Stopping a recording saves its .wav and drops
        # it on this queue; a single worker thread transcribes jobs one at a time.
        # Recording stays responsive and a new recording can begin while earlier
        # ones are still being transcribed.
        self._job_queue: queue.Queue = queue.Queue()
        self._pending: set = set()           # wav paths queued or in progress
        self._pending_lock = threading.Lock()

        self._build()

        self._worker = threading.Thread(target=self._transcribe_worker, daemon=True)
        self._worker.start()
        self._refresh_status()               # reflect any leftover files

        if ENABLE_SUMMARY and not _find_claude_cli():
            self._log("Summaries off: Claude CLI ('claude') not found on PATH.")

    # ---- UI construction ------------------------------------------------

    def _build(self):
        pad = dict(padx=24, pady=0)

        # top spacer
        tk.Frame(self.root, bg=BG, height=20).pack()

        # status line
        self._status_var = tk.StringVar(value="Ready")
        tk.Label(self.root, textvariable=self._status_var,
                 font=FONT_STATUS, bg=BG, fg=TEXT_DIM).pack(**pad)

        # timer
        self._timer_var = tk.StringVar(value="00:00")
        tk.Label(self.root, textvariable=self._timer_var,
                 font=FONT_TIMER, bg=BG, fg=TEXT_MAIN).pack(pady=(6, 14))

        # record button
        self._btn = tk.Button(
            self.root, text="Start Recording",
            font=("Segoe UI", 13, "bold"),
            bg=BTN_START, fg="#1e1e2e",
            activebackground=BTN_START, activeforeground="#1e1e2e",
            relief=tk.FLAT, cursor="hand2",
            width=22, height=2,
            state=tk.NORMAL,
            command=self._toggle,
        )
        self._btn.pack(pady=(0, 18))

        # model selector + always-on-top row
        row = tk.Frame(self.root, bg=BG)
        row.pack(pady=(0, 4))

        tk.Label(row, text="Model:", font=FONT_BODY, bg=BG, fg=TEXT_DIM).pack(side=tk.LEFT, padx=(0, 6))

        self._model_var = tk.StringVar(value=WHISPER_MODEL)
        model_box = ttk.Combobox(
            row, textvariable=self._model_var,
            values=["tiny", "base", "small", "medium", "large-v2"],
            state="readonly", width=10, font=FONT_BODY,
        )
        model_box.pack(side=tk.LEFT, padx=(0, 16))
        model_box.bind("<<ComboboxSelected>>", self._on_model_changed)

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

        # manual triggers: process pending files + set the API key
        btn_row = tk.Frame(self.root, bg=BG)
        btn_row.pack(pady=(0, 6))

        self._pending_btn = tk.Button(
            btn_row, text="Process pending",
            font=("Segoe UI", 10),
            bg="#313244", fg=TEXT_MAIN,
            activebackground="#45475a", activeforeground=TEXT_MAIN,
            relief=tk.FLAT, cursor="hand2", bd=0,
            padx=10, pady=4,
            command=self._transcribe_pending,
        )
        self._pending_btn.pack(side=tk.LEFT, padx=(0, 6))

        tk.Button(
            btn_row, text="Open folder",
            font=("Segoe UI", 10),
            bg="#313244", fg=TEXT_MAIN,
            activebackground="#45475a", activeforeground=TEXT_MAIN,
            relief=tk.FLAT, cursor="hand2", bd=0,
            padx=10, pady=4,
            command=self._open_folder,
        ).pack(side=tk.LEFT)

        # divider
        tk.Frame(self.root, bg="#313244", height=1).pack(fill=tk.X, pady=(12, 0))

        # log
        self._log_box = scrolledtext.ScrolledText(
            self.root, font=FONT_LOG, bg="#181825", fg=TEXT_DIM,
            insertbackground=TEXT_MAIN, relief=tk.FLAT,
            width=46, height=7, state=tk.DISABLED,
            padx=8, pady=6,
        )
        self._log_box.pack(fill=tk.X)

        # output dir label
        tk.Label(
            self.root,
            text=f"Saving to: {OUTPUT_DIR}",
            font=("Segoe UI", 8), bg="#181825", fg=TEXT_DIM,
            anchor="w",
        ).pack(fill=tk.X, padx=8, pady=(0, 0))

        tk.Frame(self.root, bg=BG, height=10).pack()

    # ---- Transcription model (lazy load) --------------------------------

    def _ensure_model(self):
        """Load the Whisper model once and return it (None on failure).

        Called only from the worker thread, so it never blocks the UI.
        """
        m = self._model
        if m is not None:
            return m
        size = self._model_size
        self.root.after(0, lambda: self._log(f"Loading Whisper '{size}' model..."))
        try:
            if _BACKEND == "faster_whisper":
                self._model = WhisperModel(size, device="cpu", compute_type="int8")
            else:
                self._model = _openai_whisper.load_model(size)
            self.root.after(0, lambda: self._log("Model ready."))
            return self._model
        except Exception as exc:
            self.root.after(0, lambda e=exc: self._log(f"ERROR loading model: {e}"))
            return None

    # ---- Recording toggle -----------------------------------------------

    def _toggle(self):
        if not self._recording:
            self._start()
        else:
            self._stop()

    def _start(self):
        self._recording  = True
        self._start_time = datetime.now()
        self._recorder   = DualChannelRecorder()
        self._recorder.start()

        self._btn.config(text="Stop Recording", bg=BTN_STOP,
                         activebackground=BTN_STOP)
        self._set_status("Recording")
        self._log(f"Started at {self._start_time.strftime('%H:%M:%S')}")
        self._tick()

    def _stop(self):
        """Stop recording and hand the audio off for background transcription.

        Returns to the idle state immediately so a new recording can begin
        right away, even while previous recordings are still transcribing.
        """
        self._recording = False
        if self._timer_id:
            self.root.after_cancel(self._timer_id)
            self._timer_id = None
        self._timer_var.set("00:00")

        started  = self._start_time
        recorder = self._recorder
        wav      = OUTPUT_DIR / f"meeting_{started.strftime('%Y%m%d_%H%M%S')}.wav"

        # Reserve the path now so a manual "Transcribe pending" scan can't grab
        # the same file before _finalize has finished writing and enqueuing it.
        with self._pending_lock:
            self._pending.add(wav)

        self._btn.config(state=tk.NORMAL, text="Start Recording",
                         bg=BTN_START, activebackground=BTN_START)
        self._set_status("Ready")
        self._refresh_status()

        threading.Thread(target=self._finalize,
                         args=(recorder, wav), daemon=True).start()

    def _finalize(self, recorder: DualChannelRecorder, wav: Path):
        """Stop the audio streams, save the .wav, and queue it (worker thread)."""
        recorder.stop()
        if recorder.save_wav(wav):
            self._job_queue.put(wav)
            self.root.after(0, lambda: self._log(
                f"Saved {wav.name} - queued for transcription."))
        else:
            with self._pending_lock:
                self._pending.discard(wav)
            self.root.after(0, lambda: self._log("No audio captured."))
        self.root.after(0, self._refresh_status)

    # ---- Background transcription worker --------------------------------

    def _transcribe_worker(self):
        """Process queued jobs one at a time, for the life of the app.

        A .wav job is transcribed (and then queues its transcript for a summary);
        a .txt job is summarized.
        """
        while True:
            job = self._job_queue.get()
            try:
                if job is None:
                    break
                if job.suffix == ".wav":
                    self._do_transcribe(job)
                else:
                    self._summarize(job)
            except Exception as exc:
                self.root.after(0, lambda e=exc, j=job: self._log(
                    f"ERROR processing {j.name}: {e}"))
            finally:
                if job is not None:
                    with self._pending_lock:
                        self._pending.discard(job)
                self._job_queue.task_done()
                self.root.after(0, self._refresh_status)

    def _do_transcribe(self, wav: Path):
        model = self._ensure_model()
        if model is None:
            self.root.after(0, lambda: self._log(
                f"Skipped {wav.name} - model unavailable. Use 'Transcribe "
                f"pending recordings' to retry."))
            return   # leave the .wav on disk (no .txt) so it can be retried

        try:
            duration = sf.info(str(wav)).duration
        except Exception:
            duration = 0.0
        started = self._parse_stamp(wav)
        self.root.after(0, lambda: self._log(
            f"Transcribing {wav.name} ({duration:.0f}s)..."))

        if _BACKEND == "faster_whisper":
            segs, _ = model.transcribe(str(wav), language=None, vad_filter=True)
            lines = []
            for s in segs:
                mm, ss = divmod(int(s.start), 60)
                lines.append(f"[{mm:02d}:{ss:02d}] {s.text.strip()}")
            transcript = "\n".join(lines)
        else:
            transcript = model.transcribe(str(wav))["text"]

        txt = wav.with_suffix(".txt")
        txt.parent.mkdir(parents=True, exist_ok=True)
        with open(txt, "w", encoding="utf-8") as f:
            if started:
                f.write(f"Started    : {started.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Transcribed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 60 + "\n\n")
            f.write(transcript + "\n")

        if not KEEP_AUDIO_FILE:
            wav.unlink(missing_ok=True)

        self.root.after(0, lambda: self._log(f"[OK] Transcript: {txt.name}"))

        if self._summary_available():
            self._enqueue(txt)   # queue the transcript for summarization

    # ---- Summarization (via the Claude CLI) -----------------------------

    @staticmethod
    def _summary_available() -> bool:
        return ENABLE_SUMMARY and bool(_find_claude_cli())

    @staticmethod
    def _summary_path(txt: Path) -> Path:
        return txt.parent / SUMMARY_SUBDIR / f"{txt.stem}_summary.txt"

    def _summarize(self, txt: Path):
        summary = self._summary_path(txt)
        if summary.exists():
            return                       # already summarized; never redo
        cli = _find_claude_cli()
        if not (ENABLE_SUMMARY and cli):
            return
        try:
            transcript = txt.read_text(encoding="utf-8")
        except OSError as exc:
            self.root.after(0, lambda e=exc: self._log(f"Summary skipped: {e}"))
            return
        if not transcript.strip():
            return

        self.root.after(0, lambda: self._log(f"Summarizing {txt.name}..."))
        cmd = [cli, "-p", "--output-format", "text",
               "--system-prompt", SUMMARY_SYSTEM_PROMPT]
        if SUMMARY_MODEL:
            cmd += ["--model", SUMMARY_MODEL]
        try:
            proc = subprocess.run(
                cmd, input=transcript, capture_output=True, text=True,
                encoding="utf-8", errors="replace", cwd=str(txt.parent),
                creationflags=_CREATE_NO_WINDOW, timeout=600,
            )
        except subprocess.TimeoutExpired:
            self.root.after(0, lambda: self._log(f"Summary timed out for {txt.name}."))
            return
        except OSError as exc:
            self.root.after(0, lambda e=exc: self._log(f"Summary CLI failed to launch: {e}"))
            return

        if proc.returncode != 0:
            tail = (proc.stderr or "").strip().splitlines()
            msg = tail[-1] if tail else f"exit code {proc.returncode}"
            self.root.after(0, lambda m=msg: self._log(f"Summary failed: {m}"))
            return
        text = (proc.stdout or "").strip()
        if not text:
            self.root.after(0, lambda: self._log(f"Summary empty for {txt.name}."))
            return
        try:
            summary.parent.mkdir(parents=True, exist_ok=True)
            summary.write_text(text + "\n", encoding="utf-8")
        except OSError as exc:
            self.root.after(0, lambda e=exc: self._log(f"Summary write failed: {e}"))
            return
        self.root.after(0, lambda: self._log(f"[OK] Summary: {SUMMARY_SUBDIR}/{summary.name}"))

    # ---- Pending files (manual transcription / summarization) -----------

    def _transcribe_pending(self):
        """Queue untranscribed recordings and unsummarized transcripts."""
        added = 0
        for job in self._find_untranscribed() + self._find_unsummarized():
            if self._enqueue(job):
                added += 1
        if added:
            self._log(f"Queued {added} pending file(s) for processing.")
        else:
            self._log("Nothing pending to process.")
        self._refresh_status()

    def _enqueue(self, wav: Path) -> bool:
        """Add a wav to the queue unless it is already queued / in progress."""
        with self._pending_lock:
            if wav in self._pending:
                return False
            self._pending.add(wav)
        self._job_queue.put(wav)
        return True

    @staticmethod
    def _find_untranscribed() -> list:
        """meeting_*.wav files in OUTPUT_DIR with no matching .txt, oldest first."""
        if not OUTPUT_DIR.exists():
            return []
        return [w for w in sorted(OUTPUT_DIR.glob("meeting_*.wav"))
                if not w.with_suffix(".txt").exists()]

    @staticmethod
    def _find_unsummarized() -> list:
        """Transcripts in OUTPUT_DIR with no summary/ file (only when the CLI is present)."""
        if not (ENABLE_SUMMARY and _find_claude_cli()):
            return []
        if not OUTPUT_DIR.exists():
            return []
        out = []
        for txt in sorted(OUTPUT_DIR.glob("meeting_*.txt")):
            if txt.name.endswith("_summary.txt"):
                continue   # legacy stray summary, not a transcript
            if not (txt.parent / SUMMARY_SUBDIR / f"{txt.stem}_summary.txt").exists():
                out.append(txt)
        return out

    @staticmethod
    def _parse_stamp(wav: Path) -> datetime | None:
        """Recover the start time from a meeting_YYYYMMDD_HHMMSS file name."""
        try:
            return datetime.strptime(wav.stem.split("meeting_", 1)[1],
                                     "%Y%m%d_%H%M%S")
        except (IndexError, ValueError):
            return None

    # ---- Timer ----------------------------------------------------------

    def _tick(self):
        if self._recording and self._start_time:
            elapsed = int((datetime.now() - self._start_time).total_seconds())
            mm, ss  = divmod(elapsed, 60)
            self._timer_var.set(f"{mm:02d}:{ss:02d}")

            if self._recorder.seconds_since_sound >= SILENCE_TIMEOUT:
                self._log(f"No audio for {SILENCE_TIMEOUT}s - stopping automatically.")
                self._stop()
                return

            self._timer_id = self.root.after(1000, self._tick)

    # ---- Helpers --------------------------------------------------------

    def _set_status(self, text: str):
        self._status_var.set(text)

    def _refresh_status(self):
        """Update the queue label and the 'pending' button count (main thread)."""
        with self._pending_lock:
            active = len(self._pending)
            pending_set = set(self._pending)
        self._queue_var.set(
            f"Processing... ({active} in queue)" if active else "")

        waiting = sum(1 for f in (self._find_untranscribed() + self._find_unsummarized())
                      if f not in pending_set)
        self._pending_btn.config(
            text=f"Process pending ({waiting})" if waiting else "Process pending")

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_box.config(state=tk.NORMAL)
        self._log_box.insert(tk.END, f"{ts}  {msg}\n")
        self._log_box.see(tk.END)
        self._log_box.config(state=tk.DISABLED)

    def _on_model_changed(self, _event=None):
        self._model      = None                 # force reload on next job
        self._model_size = self._model_var.get()
        self._log(f"Model changed to '{self._model_size}' (applies to new transcriptions)")

    def _open_folder(self):
        try:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(OUTPUT_DIR))   # Windows: open in Explorer
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
            busy.append(f"{active} recording(s) are being transcribed")

        if busy and not messagebox.askokcancel(
            "Quit Meeting Recorder?",
            "Currently " + " and ".join(busy) + ".\n\n"
            "Audio is saved as .wav files, so nothing is lost - you can finish "
            "any unfinished transcriptions later with 'Transcribe pending "
            "recordings'.\n\nQuit now?",
        ):
            return

        # Save an in-progress recording so it survives as a pending .wav.
        if self._recording:
            self._recording = False
            if self._timer_id:
                self.root.after_cancel(self._timer_id)
                self._timer_id = None
            started  = self._start_time
            recorder = self._recorder
            recorder.stop()
            recorder.save_wav(
                OUTPUT_DIR / f"meeting_{started.strftime('%Y%m%d_%H%M%S')}.wav")

        self.root.destroy()


# -- Entry point ---------------------------------------------------------
def main():
    root = tk.Tk()
    root.geometry("380x450")
    icon = _HERE / "icon.ico"
    if icon.exists():
        try:
            root.iconbitmap(str(icon))
        except Exception:
            pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
