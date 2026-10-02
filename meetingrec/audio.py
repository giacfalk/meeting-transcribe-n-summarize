"""Audio capture: microphone + system loopback, streamed to a stereo WAV on disk.

The left channel is the microphone ("me"), the right channel is what the
computer plays ("others"). Audio goes to disk every half second instead of
being held in memory until Stop, so memory use stays flat for long meetings
and a crash loses at most the last couple of seconds.
"""

import functools
import threading
import time
import warnings
import wave
from pathlib import Path

import numpy as np

from .config import SAMPLE_RATE

MIC, SYSTEM = 0, 1          # channel indices in the stereo WAV
_CHANNEL_NAMES = ("microphone", "system audio")


# -- Audio devices (soundcard / WASAPI) ------------------------------------
def _soundcard():
    import soundcard as sc
    # soundcard turns its warnings to "always" on import, so filter after importing.
    warnings.filterwarnings("ignore", message="data discontinuity")
    return sc


def soundcard_available() -> bool:
    try:
        _soundcard()
        return True
    except Exception:
        return False


def find_microphone(name: str = ""):
    """The default microphone, or the first whose name contains `name` (case-insensitive)."""
    sc = _soundcard()
    if name:
        for mic in sc.all_microphones():
            if name.lower() in mic.name.lower():
                return mic
        raise RuntimeError(f"no microphone matching '{name}'")
    return sc.default_microphone()


def device_names(microphone: str = "") -> tuple[str, str]:
    """(microphone, speaker) names for the log, '?' where unavailable."""
    try:
        mic = find_microphone(microphone).name
    except Exception:
        mic = "?"
    try:
        speaker = _soundcard().default_speaker().name
    except Exception:
        speaker = "?"
    return mic, speaker


def open_microphone(sample_rate: int, name: str = ""):
    return find_microphone(name).recorder(samplerate=sample_rate, channels=1)


def open_default_loopback(sample_rate: int):
    sc = _soundcard()
    devs = [m for m in sc.all_microphones(include_loopback=True) if m.isloopback]
    if not devs:
        raise RuntimeError("no loopback device found")
    default_name = sc.default_speaker().name
    dev = next((d for d in devs if default_name in d.name), devs[0])
    return dev.recorder(samplerate=sample_rate, channels=2)


def rms(samples: np.ndarray) -> float:
    """Root-mean-square level of a chunk (0.0 = digital silence)."""
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


def loudest_chunk_rms(samples: np.ndarray, sample_rate: int = SAMPLE_RATE,
                      chunk_seconds: float = 0.5) -> float:
    """RMS of the loudest half-second -- same yardstick as the silence auto-stop."""
    n = max(1, int(sample_rate * chunk_seconds))
    return max((rms(samples[i:i + n]) for i in range(0, len(samples), n)), default=0.0)


# -- WAV writing / reading -------------------------------------------------
class StereoWavWriter:
    """Writes 16-bit stereo PCM incrementally.

    `wave` patches the header after every write and the file is unbuffered, so
    whatever is on disk is a valid WAV even if the app dies mid-recording.
    """

    def __init__(self, path: Path, sample_rate: int = SAMPLE_RATE):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.frames = 0
        self._fh = open(path, "wb", buffering=0)
        self._wav = wave.open(self._fh, "wb")
        self._wav.setnchannels(2)
        self._wav.setsampwidth(2)
        self._wav.setframerate(sample_rate)

    def write(self, left: np.ndarray, right: np.ndarray) -> None:
        if len(left) == 0:
            return
        stereo = np.stack([left, right], axis=1)
        pcm = (np.clip(stereo, -1.0, 1.0) * 32767).astype("<i2")
        self._wav.writeframes(pcm.tobytes())
        self.frames += len(pcm)

    def close(self) -> None:
        self._wav.close()      # patches the header; leaves our file object open
        self._fh.close()


def read_channels(path: Path) -> tuple[list[np.ndarray], int]:
    """A WAV's channels as float32 arrays, plus its sample rate."""
    import soundfile as sf
    data, sr = sf.read(str(path), dtype="int16", always_2d=True)
    return [data[:, i].astype(np.float32) / 32768.0 for i in range(data.shape[1])], sr


def repair_wav(path: Path) -> bool:
    """Fix the size fields of a WAV whose writer died before patching the header.

    Only touches files whose `data` chunk is the last chunk (true for files this
    app writes). Returns True if the header was changed.
    """
    try:
        size = path.stat().st_size
        with open(path, "r+b") as f:
            head = f.read(12)
            if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
                return False
            pos = 12
            while pos + 8 <= size:
                f.seek(pos)
                chunk_id, chunk_size = f.read(4), int.from_bytes(f.read(4), "little")
                if chunk_id != b"data":
                    pos += 8 + chunk_size + (chunk_size & 1)
                    continue
                actual = size - (pos + 8)
                if chunk_size == actual:
                    return False
                if chunk_size < actual and _looks_like_chunk(f, pos + 8 + chunk_size, size):
                    return False   # a legitimate trailing chunk (e.g. LIST), not lost audio
                f.seek(pos + 4)
                f.write(actual.to_bytes(4, "little"))
                f.seek(4)
                f.write((size - 8).to_bytes(4, "little"))
                return True
    except OSError:
        pass
    return False


def _looks_like_chunk(f, pos: int, size: int) -> bool:
    for start in (pos, pos + 1):            # chunks are word-aligned
        if start + 8 > size:
            continue
        f.seek(start)
        chunk_id, chunk_size = f.read(4), int.from_bytes(f.read(4), "little")
        if (all(32 <= b < 127 for b in chunk_id) and chunk_id.strip()
                and start + 8 + chunk_size <= size):
            return True
    return False


# -- Recorder --------------------------------------------------------------
class DualChannelRecorder:
    """Records microphone + system-audio loopback into one stereo WAV.

    Each source runs on its own thread and buffers chunks; a writer thread
    moves them to disk side by side. A source that is missing or fails
    contributes silence, and if one stream falls more than a couple of
    seconds behind the other it is padded with silence so they stay aligned.
    """

    _CHUNK_SECONDS = 0.5
    _MAX_SKEW_SECONDS = 2.0

    def __init__(self, path: Path, sample_rate: int = SAMPLE_RATE,
                 silence_threshold: float = 0.01, microphone: str = "", sources=None):
        self.path = path
        self.sample_rate = sample_rate
        self.silence_threshold = silence_threshold
        self._sources = sources or (functools.partial(open_microphone, name=microphone),
                                    open_default_loopback)
        self._bufs: tuple[list, list] = ([], [])
        self._buffered = [0, 0]
        self._alive = [False, False]
        self._received = [0, 0]          # frames captured per channel
        self._nonzero = [False, False]   # has the channel ever been above digital zero?
        self._lock = threading.Lock()         # guards the buffers
        self._write_lock = threading.Lock()   # serializes writes to the file
        self._threads: list = []
        self._writer: StereoWavWriter | None = None
        self._writer_thread: threading.Thread | None = None
        self._last_sound_time: float | None = None
        self.is_recording = False
        self.errors: list[str] = []           # appended to by capture threads

    def start(self) -> None:
        self._writer = StereoWavWriter(self.path, self.sample_rate)
        self.is_recording = True
        self._last_sound_time = time.monotonic()
        self._alive = [True, True]
        self._threads = [threading.Thread(target=self._capture, args=(ch,), daemon=True)
                         for ch in (MIC, SYSTEM)]
        for t in self._threads:
            t.start()
        self._writer_thread = threading.Thread(target=self._write_loop, daemon=True)
        self._writer_thread.start()

    def stop(self) -> int:
        """Stop capturing, write what is left and close the file. Returns frames written."""
        self.is_recording = False
        for t in self._threads:
            t.join(timeout=6)
        if self._writer_thread:
            self._writer_thread.join(timeout=6)
        if self._writer is None:
            return 0
        self._flush(final=True)
        self._writer.close()
        return self._writer.frames

    @property
    def duration_seconds(self) -> float:
        return self._writer.frames / self.sample_rate if self._writer else 0.0

    def mic_is_digital_silence(self, after_seconds: float = 5.0) -> bool:
        """True once the mic has delivered `after_seconds` of exact zeros.

        That means a muted or disconnected input (e.g. a headset jack with no
        headset), not a quiet room: real microphones always pick up some noise.
        """
        return (self._received[MIC] >= after_seconds * self.sample_rate
                and not self._nonzero[MIC])

    @property
    def seconds_since_sound(self) -> float:
        """Seconds since the last chunk whose level was above the threshold."""
        if self._last_sound_time is None:
            return 0.0
        return time.monotonic() - self._last_sound_time

    def _capture(self, ch: int) -> None:
        numframes = int(self.sample_rate * self._CHUNK_SECONDS)
        try:
            with self._sources[ch](self.sample_rate) as rec:
                while self.is_recording:
                    data = np.asarray(rec.record(numframes=numframes), dtype=np.float32)
                    samples = data.mean(axis=1) if data.ndim > 1 else data
                    if rms(samples) > self.silence_threshold:
                        self._last_sound_time = time.monotonic()
                    self._received[ch] += len(samples)
                    if not self._nonzero[ch] and np.any(samples):
                        self._nonzero[ch] = True
                    with self._lock:
                        self._bufs[ch].append(samples)
                        self._buffered[ch] += len(samples)
        except Exception as exc:
            self.errors.append(f"{_CHANNEL_NAMES[ch]}: {exc}")
        finally:
            with self._lock:
                self._alive[ch] = False

    def _write_loop(self) -> None:
        while self.is_recording:
            time.sleep(self._CHUNK_SECONDS)
            self._flush()

    def _flush(self, final: bool = False) -> None:
        with self._write_lock:
            with self._lock:
                a, b = self._buffered
                if final or not all(self._alive):
                    n = max(a, b)    # a finished/missing source contributes silence
                else:
                    skew = int(self._MAX_SKEW_SECONDS * self.sample_rate)
                    n = max(min(a, b), max(a, b) - skew)
                if n <= 0:
                    return
                left, right = self._take(MIC, n), self._take(SYSTEM, n)
            self._writer.write(left, right)

    def _take(self, ch: int, n: int) -> np.ndarray:
        """Remove exactly n frames from a channel's buffer, zero-padded if short."""
        out = np.zeros(n, dtype=np.float32)
        buf, filled = self._bufs[ch], 0
        while filled < n and buf:
            chunk = buf[0]
            k = min(n - filled, len(chunk))
            out[filled:filled + k] = chunk[:k]
            filled += k
            if k == len(chunk):
                buf.pop(0)
            else:
                buf[0] = chunk[k:]
        self._buffered[ch] -= filled
        return out
