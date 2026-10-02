"""Speech-to-text with faster-whisper (or openai-whisper), with "me / others" labels.

Recordings are stereo: left = microphone, right = system audio. With speaker
labels on, each channel is transcribed on its own and the lines are merged by
time, which tells you who said what without a diarization model.
"""

import difflib
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .audio import loudest_chunk_rms, read_channels
from .config import SAMPLE_RATE

try:
    from faster_whisper import WhisperModel
    BACKEND = "faster_whisper"
except ImportError:
    try:
        import whisper as _openai_whisper
        BACKEND = "whisper"
    except ImportError:
        BACKEND = None


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: str = ""


# -- Model -----------------------------------------------------------------
class Transcriber:
    """Loads one Whisper model lazily and reuses it until size or device change."""

    def __init__(self, log=print):
        self._log = log
        self._model = None
        self._key: tuple | None = None
        self.device_used = "cpu"

    def _resolve_device(self, device: str) -> str:
        if device == "cpu" or BACKEND != "faster_whisper":
            return "cpu"
        try:
            import ctranslate2
            has_cuda = ctranslate2.get_cuda_device_count() > 0
        except Exception:
            has_cuda = False
        if not has_cuda and device == "cuda":
            self._log("No CUDA GPU found; transcribing on the CPU.")
        return "cuda" if has_cuda else "cpu"

    def load(self, size: str, device: str = "cpu"):
        resolved = self._resolve_device(device)
        if self._model is not None and self._key == (size, resolved):
            return self._model
        self._model = None
        self._log(f"Loading Whisper '{size}' model ({resolved.upper()})...")
        if BACKEND == "faster_whisper":
            compute_type = "float16" if resolved == "cuda" else "int8"
            self._model = WhisperModel(size, device=resolved, compute_type=compute_type)
        elif BACKEND == "whisper":
            self._model = _openai_whisper.load_model(size)
        else:
            raise RuntimeError("no transcription backend installed (pip install faster-whisper)")
        self._key, self.device_used = (size, resolved), resolved
        self._log("Model ready.")
        return self._model

    def transcribe(self, audio, size: str, device: str = "cpu",
                   language: str | None = None) -> list[Segment]:
        """Transcribe a 16 kHz float32 array (or a file path) into segments."""
        model = self.load(size, device)
        try:
            return self._run(model, audio, language)
        except Exception as exc:
            # A missing cuBLAS/cuDNN DLL only shows up once inference starts.
            if self.device_used != "cuda":
                raise
            self._log(f"GPU transcription failed ({exc}); retrying on the CPU.")
            return self._run(self.load(size, "cpu"), audio, language)

    @staticmethod
    def _run(model, audio, language) -> list[Segment]:
        if BACKEND == "faster_whisper":
            segs, _info = model.transcribe(audio, language=language or None, vad_filter=True)
            out = [Segment(s.start, s.end, s.text.strip()) for s in segs]
        else:
            result = model.transcribe(audio, language=language or None, fp16=False)
            out = [Segment(s["start"], s["end"], s["text"].strip()) for s in result["segments"]]
        return [s for s in out if s.text]


# -- Recording -> segments -------------------------------------------------
def transcribe_recording(path: Path, transcriber: Transcriber, *, model: str,
                         device: str = "cpu", language: str | None = None,
                         speaker_labels: bool = True, mic_label: str = "Me",
                         others_label: str = "Others",
                         silence_threshold: float = 0.0) -> list[Segment]:
    """Transcribe a recording.

    Audio that never rises above `silence_threshold` is not transcribed at all:
    on room noise, Whisper invents sentences (and is slow doing it).
    """
    channels, sr = read_channels(path)
    if sr != SAMPLE_RATE:
        # Not one of ours: let the decoder downmix and resample.
        return transcriber.transcribe(str(path), model, device, language)

    def audible(audio) -> bool:
        return loudest_chunk_rms(audio, sr) > silence_threshold

    if len(channels) == 2 and speaker_labels:
        segments: list[Segment] = []
        for audio, label in ((channels[0], mic_label), (channels[1], others_label)):
            if not audible(audio):
                continue   # silent channel, e.g. a muted mic or no loopback device
            for s in transcriber.transcribe(audio, model, device, language):
                s.speaker = label
                segments.append(s)
        return merge_speakers(segments, mic_label, others_label)

    if len(channels) == 2:
        mixed = np.clip(channels[0] * 0.6 + channels[1] * 0.85, -1.0, 1.0)
    else:
        mixed = channels[0]   # mono recording from v1.0
    if not audible(mixed):
        return []
    return transcriber.transcribe(mixed.astype(np.float32), model, device, language)


def _norm(text: str) -> str:
    return re.sub(r"[^\w ]+", "", text.lower()).strip()


def is_echo(mic: Segment, others: list[Segment], min_match: float = 0.6) -> bool:
    """True if a mic segment is just remote audio leaking from speakers into the mic.

    It must overlap in time with remote speech, and most of its words must
    appear in what the remote side said during that time.
    """
    overlapping = [o for o in others
                   if min(mic.end, o.end) - max(mic.start, o.start) > 0]
    if not overlapping:
        return False
    span = sum(min(mic.end, o.end) - max(mic.start, o.start) for o in overlapping)
    if span < 0.5 * max(mic.end - mic.start, 1e-6):
        return False
    # Compare words, not characters: an echo repeats the remote side's words,
    # while two people talking at once merely share letters.
    a = _norm(mic.text).split()
    b = _norm(" ".join(o.text for o in overlapping)).split()
    if not a:
        return True
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return matched / len(a) >= min_match


def merge_speakers(segments: list[Segment], mic_label: str, others_label: str) -> list[Segment]:
    """Interleave both speakers by start time, dropping mic echoes of remote audio."""
    others = [s for s in segments if s.speaker == others_label]
    kept = [s for s in segments if not (s.speaker == mic_label and is_echo(s, others))]
    return sorted(kept, key=lambda s: s.start)


# -- Formatting ------------------------------------------------------------
def format_timestamp(seconds: float, with_hours: bool = False) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if with_hours else f"{m + 60 * h:02d}:{s:02d}"


def format_transcript(segments: list[Segment]) -> str:
    """One line per segment: [MM:SS] Speaker: text  (HH:MM:SS for meetings over an hour)."""
    with_hours = any(s.start >= 3600 for s in segments)
    lines = []
    for s in segments:
        who = f"{s.speaker}: " if s.speaker else ""
        lines.append(f"[{format_timestamp(s.start, with_hours)}] {who}{s.text}")
    return "\n".join(lines)


def _srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(segments: list[Segment]) -> str:
    blocks = []
    for i, s in enumerate(segments, 1):
        who = f"{s.speaker}: " if s.speaker else ""
        blocks.append(f"{i}\n{_srt_time(s.start)} --> {_srt_time(s.end)}\n{who}{s.text}\n")
    return "\n".join(blocks)
