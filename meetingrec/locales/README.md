# UI languages

This folder holds the app's interface text, one JSON file per language.

- **`en.json` is the source of truth.** Every UI string lives here as `"key": "English text"`.
  To change what the app says, edit the English **value** (leave the key alone).
- **`<code>.json`** (e.g. `fr.json`, `de.json`) are the translations. They are **generated**,
  not edited by hand: `tools/translate.py` fills them in from `en.json` using the local
  Claude CLI, and `build_exe.py` runs it on every build. Only strings whose English changed
  (or new keys) are re-translated; the rest are reused.
- **`_source_hashes.json`** records the English each language was last translated from, so the
  translator knows what changed. Don't edit it.

The set of languages offered in the app is `LANGUAGES` in `../i18n.py`. Add a code there and
rebuild (or run `python tools/translate.py`) to generate its file.

At runtime the app loads the chosen language and falls back to `en.json` for any missing key,
so a not-yet-translated string simply shows in English instead of breaking the window.

### Refresh translations by hand

```
python tools/translate.py                 # bring every language up to date
python tools/translate.py --languages fr  # just one
python tools/translate.py --force         # re-translate everything
```

Needs the `claude` CLI on PATH and logged in (the same one the default summary backend uses).
