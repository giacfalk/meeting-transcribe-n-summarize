"""Output-folder layout: file naming, and finding work that is still unfinished.

    <output_dir>/meeting_YYYYMMDD_HHMMSS.wav      recording (deleted after transcription
                                                  unless keep_audio is set)
    <output_dir>/meeting_YYYYMMDD_HHMMSS.txt      transcript
    <output_dir>/meeting_YYYYMMDD_HHMMSS.srt      subtitles (optional)
    <output_dir>/summary/meeting_..._summary.md   summary (v1.0 wrote _summary.txt)
"""

from datetime import datetime
from pathlib import Path

SUMMARY_SUBDIR = "summary"


def new_recording_path(output_dir: Path, started: datetime) -> Path:
    """meeting_<timestamp>.wav, with a numeric suffix if that name is already taken."""
    stem = f"meeting_{started.strftime('%Y%m%d_%H%M%S')}"
    path = output_dir / f"{stem}.wav"
    n = 2
    while path.exists() or path.with_suffix(".txt").exists():
        path = output_dir / f"{stem}_{n}.wav"
        n += 1
    return path


def parse_stamp(path: Path) -> datetime | None:
    """Recover the start time from a meeting_YYYYMMDD_HHMMSS file name."""
    try:
        stamp = path.stem.split("meeting_", 1)[1][:15]
        return datetime.strptime(stamp, "%Y%m%d_%H%M%S")
    except (IndexError, ValueError):
        return None


def summary_path(txt: Path) -> Path:
    return txt.parent / SUMMARY_SUBDIR / f"{txt.stem}_summary.md"


def has_summary(txt: Path) -> bool:
    legacy = txt.parent / SUMMARY_SUBDIR / f"{txt.stem}_summary.txt"
    return summary_path(txt).exists() or legacy.exists()


def find_untranscribed(output_dir: Path, exclude=()) -> list[Path]:
    """meeting_*.wav files with no matching .txt, oldest first."""
    if not output_dir.exists():
        return []
    skip = {p for p in exclude if p}
    return [w for w in sorted(output_dir.glob("meeting_*.wav"))
            if w not in skip and not w.with_suffix(".txt").exists()]


def find_unsummarized(output_dir: Path) -> list[Path]:
    """Transcripts with no summary yet, oldest first."""
    if not output_dir.exists():
        return []
    return [t for t in sorted(output_dir.glob("meeting_*.txt"))
            if not t.name.endswith("_summary.txt")   # legacy stray summary, not a transcript
            and not has_summary(t)]
