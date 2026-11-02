"""Near-real-time "draft" transcription while a meeting is still being recorded.

The recorder taps its audio into a :class:`LiveTranscriber`; a worker thread
periodically runs Whisper on the newest few seconds and appends segments, so a
rough transcript appears during the meeting. This is a fast *preview* only --
the clean transcript (per-speaker, echo-removed) is still produced from the WAV
when recording stops, so chunk-boundary glitches here don't matter.

It deliberately uses its own small model (``base`` by default) and the mixed
mono signal (no speaker labels), so it keeps up on the CPU without competing
with the stop-time transcription, which uses its own model.
"""

import threading
import time

import numpy as np

from .audio import loudest_chunk_rms, rms
from .config import SAMPLE_RATE
from .transcribe import Segment, Transcriber, format_transcript


class LiveTranscriber:
    """Turn a stream of audio chunks into live transcript segments."""

    _STEP_SECONDS = 0.5     # how often the worker wakes to check the clock / stop flag
    _CUT_SCAN_SECONDS = 4.0  # how far back to look for a pause to cut a window at

    def __init__(self, *, model: str = "base", device: str = "cpu",
                 language: str | None = None, interval: float = 15.0,
                 min_window: float = 6.0, silence_threshold: float = 0.0,
                 sample_rate: int = SAMPLE_RATE, on_segments=None, log=print,
                 transcriber: Transcriber | None = None):
        self.model = model or "base"
        self.device = device
        self.language = language or None
        self.interval = max(self._STEP_SECONDS, float(interval))
        self.min_window = float(min_window)
        self.silence_threshold = silence_threshold
        self.sample_rate = sample_rate
        self._on_segments = on_segments or (lambda segs: None)
        self._log = log
        self._transcriber = transcriber or Transcriber(log=log)

        self._pending: list[np.ndarray] = []
        self._pending_frames = 0
        self._consumed = 0.0            # seconds of audio already transcribed (time offset)
        self._lock = threading.Lock()         # guards the pending buffer
        self._segments: list[Segment] = []
        self._seg_lock = threading.Lock()     # guards the segment list
        self._running = False
        self._thread: threading.Thread | None = None

    # -- audio in (called from the recorder's writer thread) --------------

    def feed(self, mono: np.ndarray) -> None:
        """Add mono 16 kHz float32 samples to the buffer waiting to be transcribed."""
        if mono is None or len(mono) == 0:
            return
        with self._lock:
            self._pending.append(np.asarray(mono, dtype=np.float32))
            self._pending_frames += len(mono)

    def feed_stereo(self, left: np.ndarray, right: np.ndarray) -> None:
        """Mix microphone + system audio the way the batch mono path does, then feed."""
        n = min(len(left), len(right))
        if n == 0:
            return
        mixed = np.clip(left[:n] * 0.6 + right[:n] * 0.85, -1.0, 1.0)
        self.feed(mixed.astype(np.float32))

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the worker and transcribe whatever audio is left."""
        self._running = False
        thread = self._thread
        self._thread = None
        if thread:
            thread.join(timeout=30)
        try:
            self._process(final=True)
        except Exception as exc:          # a final pass must never raise into teardown
            self._log(f"Live transcription error: {exc}")

    @property
    def segments(self) -> list[Segment]:
        with self._seg_lock:
            return list(self._segments)

    def transcript_text(self) -> str:
        return format_transcript(self.segments)

    # -- worker -----------------------------------------------------------

    def _loop(self) -> None:
        waited = 0.0
        while self._running:
            time.sleep(self._STEP_SECONDS)
            waited += self._STEP_SECONDS
            if waited + 1e-6 >= self.interval:
                waited = 0.0
                try:
                    self._process()
                except Exception as exc:
                    self._log(f"Live transcription error: {exc}")

    def _take_window(self, final: bool) -> np.ndarray | None:
        """Pop the buffered audio to transcribe now.

        Unless this is the final pass, trim the window back to the last pause so
        a word isn't split across windows, and keep the trimmed tail for next time.
        """
        with self._lock:
            if self._pending_frames == 0:
                return None
            if not final and self._pending_frames < self.min_window * self.sample_rate:
                return None
            window = np.concatenate(self._pending)
            self._pending = []
            self._pending_frames = 0
        if not final:
            cut = self._cut_point(window)
            if cut < len(window):
                remainder = window[cut:]
                window = window[:cut]
                with self._lock:
                    self._pending.insert(0, remainder)
                    self._pending_frames += len(remainder)
        return window

    def _cut_point(self, window: np.ndarray) -> int:
        """Index of the last quiet half-second near the end, or len(window) if none."""
        step = int(self.sample_rate * 0.5)
        if len(window) <= 2 * step:
            return len(window)
        floor = max(self.silence_threshold, 1e-4)
        earliest = max(step, len(window) - int(self._CUT_SCAN_SECONDS * self.sample_rate))
        for start in range(len(window) - step, earliest - 1, -step):
            if rms(window[start:start + step]) <= floor:
                return start + step
        return len(window)

    def _process(self, final: bool = False) -> None:
        window = self._take_window(final)
        if window is None or len(window) == 0:
            return
        offset = self._consumed
        self._consumed += len(window) / self.sample_rate
        if loudest_chunk_rms(window, self.sample_rate) <= self.silence_threshold:
            return      # silence: the clock has advanced, but there's nothing to transcribe
        segs = self._transcriber.transcribe(window, self.model, self.device, self.language)
        new = [Segment(s.start + offset, s.end + offset, s.text, s.speaker)
               for s in segs if s.text]
        if not new:
            return
        with self._seg_lock:
            self._segments.extend(new)
        self._on_segments(new)
