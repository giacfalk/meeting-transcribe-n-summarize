"""User-interface translation.

The English strings live in `locales/en.json` -- that is the one file to edit.
The other languages in `locales/<code>.json` are generated from it at build time
by `tools/translate.py`, so changing an English string and rebuilding re-translates
only what changed. At runtime this module just loads the chosen language and looks
strings up, falling back to English for anything missing (so a forgotten string is
visible but never crashes the window).
"""

import json
import sys
from pathlib import Path

# UI languages offered in Settings: code -> (native name, English name).
# English is first and is the source every other language is translated from.
# The English name is what the build-time translator is told to translate into.
LANGUAGES: dict[str, tuple[str, str]] = {
    "en": ("English", "English"),
    "es": ("Español", "Spanish"),
    "fr": ("Français", "French"),
    "de": ("Deutsch", "German"),
    "it": ("Italiano", "Italian"),
    "pt": ("Português", "Portuguese"),
    "nl": ("Nederlands", "Dutch"),
    "pl": ("Polski", "Polish"),
    "tr": ("Türkçe", "Turkish"),
    "ru": ("Русский", "Russian"),
    "uk": ("Українська", "Ukrainian"),
    "zh": ("中文", "Simplified Chinese"),
    "ja": ("日本語", "Japanese"),
    "ko": ("한국어", "Korean"),
    "hi": ("हिन्दी", "Hindi"),
}

DEFAULT_LANGUAGE = "en"


def locales_dir() -> Path:
    """The folder holding en.json and the generated <code>.json files.

    Works both in development and in the PyInstaller bundle (where data files
    live under sys._MEIPASS), mirroring app.py's path resolution.
    """
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return base / "meetingrec" / "locales"
    return Path(__file__).resolve().parent / "locales"


def native_name(code: str) -> str:
    return LANGUAGES.get(code, LANGUAGES[DEFAULT_LANGUAGE])[0]


def english_name(code: str) -> str:
    return LANGUAGES.get(code, LANGUAGES[DEFAULT_LANGUAGE])[1]


def _load(code: str) -> dict[str, str]:
    try:
        data = json.loads((locales_dir() / f"{code}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}


_fallback: dict[str, str] = {}
_strings: dict[str, str] = {}
_language = DEFAULT_LANGUAGE


def set_language(code: str) -> None:
    """Load `code` as the active UI language (English stays the fallback)."""
    global _fallback, _strings, _language
    _fallback = _load(DEFAULT_LANGUAGE)
    _language = code if code in LANGUAGES else DEFAULT_LANGUAGE
    _strings = dict(_fallback) if _language == DEFAULT_LANGUAGE else _load(_language)


def language() -> str:
    """The active UI language code."""
    return _language


def t(key: str, /, **kwargs) -> str:
    """The active-language string for `key`, English if missing, the key if neither.

    `{placeholders}` in the string are filled from kwargs; a formatting slip returns
    the raw string rather than raising, so the UI never breaks on a bad translation.
    """
    if not _fallback and not _strings:
        set_language(_language)
    text = _strings.get(key) or _fallback.get(key) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text
