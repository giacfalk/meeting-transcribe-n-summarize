#!/usr/bin/env python3
"""Re-translate the UI strings whenever the English ones change.

`meetingrec/locales/en.json` is the source of truth. This script fills in
`meetingrec/locales/<code>.json` for every other language in `i18n.LANGUAGES`,
translating with the locally installed Claude CLI (the same `claude` login the
app's default summary backend uses) -- so no API key and no per-string cost.

Only strings that are new or whose English changed since the last run are sent
to Claude; the rest are reused. It runs as a step of `build_exe.py`, and you can
run it by hand too:

    python tools/translate.py                 # bring every language up to date
    python tools/translate.py --languages fr,de
    python tools/translate.py --force         # re-translate everything
    python tools/translate.py --model claude-haiku-5-5

It is a build/maintainer tool: nothing here ships in the app, and end users
never translate anything.
"""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from meetingrec import i18n, summarize  # noqa: E402

LOCALES = ROOT / "meetingrec" / "locales"
SOURCE = LOCALES / "en.json"
STATE = LOCALES / "_source_hashes.json"   # {key: hash of the English last translated}
CHUNK = 60                                # strings per Claude call (keeps replies parseable)

_PROMPT = (
    "You are a professional software-localization translator. You are given a JSON "
    "object whose values are short user-interface strings (button labels, menu items, "
    "settings labels, status messages) for a desktop meeting-recording app, in English. "
    "Translate every VALUE into {language}. Rules:\n"
    "- Return ONLY a JSON object with exactly the same keys, values translated. No prose, "
    "no code fences.\n"
    "- Keep it concise and natural for a UI in {language}; match the tone of short app labels.\n"
    "- Preserve any {{placeholder}} tokens in curly braces exactly as they are "
    "(same spelling, do not translate or reorder their names).\n"
    "- Preserve product and technology names verbatim: Meeting Recorder, Claude, Claude Code, "
    "Anthropic, ChatGPT, OpenAI, Ollama, Whisper, API. Keep environment-variable names "
    "(e.g. OPENAI_API_KEY) and example commands (e.g. 'ollama pull llama3.1') unchanged.\n"
    "- Keep leading/trailing spaces and punctuation style of each string."
)


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _claude_translate(cli: str, strings: dict, language: str, model: str) -> dict:
    """Translate one batch; raise RuntimeError if the CLI fails or returns non-JSON."""
    prompt = _PROMPT.format(language=language)
    payload = json.dumps(strings, ensure_ascii=False)
    try:
        proc = subprocess.run(
            summarize.claude_cli_command(cli, prompt, model), input=payload,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Claude CLI failed: {exc}") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise RuntimeError(tail[-1] if tail else f"Claude CLI exited {proc.returncode}")

    out = (proc.stdout or "").strip()
    if out.startswith("```"):                         # tolerate a stray ```json ... ``` fence
        out = out.strip("`")
        if out[:4].lower() == "json":
            out = out[4:]
        out = out.strip()
    try:
        result = json.loads(out)
    except ValueError as exc:
        raise RuntimeError(f"Claude did not return JSON: {out[:200]}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("Claude returned JSON that was not an object")
    return {k: v for k, v in result.items() if k in strings and isinstance(v, str)}


def _translate_language(cli: str, code: str, english: dict, changed: set, model: str) -> bool:
    """Bring one language file up to date. Returns False if anything failed."""
    name = i18n.english_name(code)
    path = LOCALES / f"{code}.json"
    existing = _load(path)
    needed = {k: v for k, v in english.items() if k not in existing or k in changed}

    if not needed:
        pruned = {k: existing[k] for k in english if k in existing}   # drop removed keys, reorder
        if pruned != existing:
            _write(path, pruned)
        print(f"  {code} ({name}): up to date")
        return True

    print(f"  {code} ({name}): translating {len(needed)} string(s)...")
    ok = True
    items = list(needed.items())
    for i in range(0, len(items), CHUNK):
        batch = dict(items[i:i + CHUNK])
        try:
            existing.update(_claude_translate(cli, batch, name, model))
        except RuntimeError as exc:
            print(f"    ! {exc}")
            ok = False
    ordered = {k: existing[k] for k in english if k in existing}       # en.json order, pruned
    _write(path, ordered)
    missing = [k for k in needed if k not in existing]
    if missing:
        print(f"    ! {len(missing)} string(s) still untranslated (left as English fallback)")
        ok = False
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="Re-translate the UI strings from en.json.")
    ap.add_argument("--languages", help="comma-separated codes to do (default: all but en)")
    ap.add_argument("--model", default="", help="Claude model to use (default: your CLI default)")
    ap.add_argument("--force", action="store_true", help="re-translate every string")
    args = ap.parse_args()

    english = _load(SOURCE)
    if not english:
        print(f"[!] {SOURCE} is missing or empty; nothing to translate.")
        return 1

    cli = summarize.find_claude_cli()
    if not cli:
        print("[!] Claude CLI ('claude') not found on PATH; cannot translate.")
        return 1

    cur = {k: _hash(v) for k, v in english.items()}
    old = {} if args.force else _load(STATE)
    changed = {k for k in english if old.get(k) != cur[k]}

    targets = [c for c in i18n.LANGUAGES if c != "en"]
    if args.languages:
        wanted = {c.strip() for c in args.languages.split(",") if c.strip()}
        targets = [c for c in targets if c in wanted]
        unknown = wanted - set(i18n.LANGUAGES)
        if unknown:
            print(f"[!] Unknown language code(s): {', '.join(sorted(unknown))}")

    print(f"Translating from en.json ({len(english)} strings; "
          f"{len(changed)} new/changed) into {len(targets)} language(s).")
    all_ok = True
    for code in targets:
        all_ok &= _translate_language(cli, code, english, changed, args.model)

    # Only record the English we translated from when every target succeeded, so a
    # failed or offline run retries the same strings next time instead of skipping them.
    if all_ok and not args.languages:
        _write(STATE, cur)
    print("Done." if all_ok else "Done with warnings (see above).")
    return 0 if all_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
