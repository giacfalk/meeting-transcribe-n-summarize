"""Settings: built-in defaults, the user's settings.json, and derived paths."""

import json
import os
from pathlib import Path

SAMPLE_RATE = 16_000

WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3-turbo", "large-v3", "large-v2"]
DEVICES = ["cpu", "auto", "cuda"]
SUMMARY_BACKENDS = ["claude-cli", "anthropic-api", "ollama", "none"]

HELP_URL = "https://github.com/giacfalk/meeting-transcribe-n-summarize#configuration"

# Every key a user can set in settings.json, with its default. The type of each
# default is also the type a value in the file must have.
DEFAULTS: dict = {
    "output_dir": "",               # "" = Documents/Meeting Recorder/recordings
    "whisper_model": "base",        # see WHISPER_MODELS (any faster-whisper name works)
    "device": "cpu",                # cpu | auto (GPU if available) | cuda
    "language": "",                 # "" = auto-detect; or a code such as "en", "it", "de"
    "microphone": "",               # "" = Windows default input; or part of a device name
    "speaker_labels": True,         # label lines with mic_label / others_label
    "mic_label": "Me",
    "others_label": "Others",
    "keep_audio": False,            # keep the .wav next to the transcript
    "write_srt": False,             # also write an .srt subtitle file
    "silence_timeout": 120,         # seconds of silence before recording stops itself
    "silence_threshold": 0.01,      # chunk RMS at/below this counts as silence (0.0-1.0)
    "hotkey": "",                   # global start/stop shortcut, e.g. "ctrl+alt+r"; "" = off
    "summary_backend": "claude-cli",  # see SUMMARY_BACKENDS
    "summary_model": "",            # "" = backend default (required for ollama)
    "summary_prompt": "",           # "" = built-in prompt (summarize.DEFAULT_SUMMARY_PROMPT)
    "anthropic_api_key": "",        # prefer the ANTHROPIC_API_KEY environment variable
    "ollama_url": "http://localhost:11434",
}

_CHOICES = {"device": DEVICES, "summary_backend": SUMMARY_BACKENDS}


def app_dir() -> Path:
    """Per-user folder for settings (%APPDATA%/MeetingRecorder)."""
    base = os.environ.get("APPDATA")
    return (Path(base) if base else Path.home() / "AppData" / "Roaming") / "MeetingRecorder"


def settings_path() -> Path:
    return app_dir() / "settings.json"


def default_output_dir() -> Path:
    # Prefer the OneDrive-synced Documents folder; fall back to local Documents.
    onedrive = os.environ.get("OneDrive") or os.environ.get("OneDriveCommercial")
    docs = (Path(onedrive) / "Documents") if onedrive else (Path.home() / "Documents")
    return docs / "Meeting Recorder" / "recordings"


def output_dir(settings: dict) -> Path:
    raw = settings.get("output_dir") or ""
    return Path(os.path.expandvars(os.path.expanduser(raw))) if raw else default_output_dir()


def _valid_type(value, default) -> bool:
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, (int, float)):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, type(default))


def load_settings(path: Path | None = None) -> tuple[dict, list[str]]:
    """Defaults overlaid with settings.json. Returns (settings, warnings).

    A missing file is created with the defaults so users can see every option.
    Unknown keys are ignored and invalid values fall back to their default, so a
    typo in the file never stops the app from starting.
    """
    path = path or settings_path()
    settings = dict(DEFAULTS)
    warnings: list[str] = []

    if not path.exists():
        try:
            save_settings(settings, path)
        except OSError as exc:
            warnings.append(f"Could not create {path}: {exc}")
        return settings, warnings

    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))  # tolerate Notepad's BOM
    except (OSError, ValueError) as exc:
        return settings, [f"settings.json ignored ({exc}); using defaults."]
    if not isinstance(data, dict):
        return settings, ["settings.json ignored (not a JSON object); using defaults."]

    for key, value in data.items():
        if key not in DEFAULTS:
            continue
        default = DEFAULTS[key]
        if not _valid_type(value, default):
            warnings.append(f"settings.json: '{key}' has the wrong type; using {default!r}.")
        elif key in _CHOICES and value not in _CHOICES[key]:
            warnings.append(f"settings.json: '{key}' must be one of {_CHOICES[key]}; "
                            f"using {default!r}.")
        else:
            settings[key] = value
    return settings, warnings


def save_settings(settings: dict, path: Path | None = None) -> None:
    """Write settings atomically (temp file + rename)."""
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"_help": HELP_URL, **{k: v for k, v in settings.items() if k != "_help"}}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def update_setting(key: str, value, path: Path | None = None) -> None:
    """Change one key in settings.json, keeping everything else in the file as is."""
    path = path or settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            data = dict(DEFAULTS)
    except (OSError, ValueError):
        data = dict(DEFAULTS)
    data[key] = value
    save_settings(data, path)
