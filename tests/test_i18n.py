import json

from meetingrec import config, i18n


def _use_locales(tmp_path, monkeypatch, files: dict):
    for code, strings in files.items():
        (tmp_path / f"{code}.json").write_text(json.dumps(strings), encoding="utf-8")
    monkeypatch.setattr(i18n, "locales_dir", lambda: tmp_path)
    i18n._fallback = {}     # force a reload from the patched folder
    i18n._strings = {}


def test_languages_english_first_and_named():
    assert next(iter(i18n.LANGUAGES)) == "en"
    assert i18n.native_name("fr") == "Français"
    assert i18n.english_name("fr") == "French"
    assert i18n.native_name("xx") == "English"      # unknown falls back to the default


def test_missing_language_falls_back_to_english(tmp_path, monkeypatch):
    _use_locales(tmp_path, monkeypatch, {"en": {"a": "Hello", "b": "World"},
                                         "fr": {"a": "Bonjour"}})
    i18n.set_language("zz")                           # not a real code
    assert i18n.language() == "en"
    assert i18n.t("a") == "Hello"


def test_missing_key_falls_back_then_returns_key(tmp_path, monkeypatch):
    _use_locales(tmp_path, monkeypatch, {"en": {"a": "Hello", "b": "World"},
                                         "fr": {"a": "Bonjour"}})
    i18n.set_language("fr")
    assert i18n.t("a") == "Bonjour"                   # translated
    assert i18n.t("b") == "World"                     # falls back to English
    assert i18n.t("nope") == "nope"                   # unknown key shows the key, never crashes


def test_placeholders_and_bad_format_are_safe(tmp_path, monkeypatch):
    _use_locales(tmp_path, monkeypatch, {"en": {"greet": "Hi {name}"}})
    i18n.set_language("en")
    assert i18n.t("greet", name="Ana") == "Hi Ana"
    assert i18n.t("greet") == "Hi {name}"             # no kwargs: left as-is
    assert i18n.t("greet", wrong="x") == "Hi {name}"  # wrong kwarg: raw string, no exception


def test_real_en_json_is_a_flat_string_map():
    data = json.loads((i18n.locales_dir() / "en.json").read_text(encoding="utf-8"))
    assert isinstance(data, dict) and data
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in data.items())


def test_config_validates_ui_language(tmp_path):
    path = tmp_path / "settings.json"
    config.save_settings({**config.defaults(), "ui_language": "zz"}, path)
    assert config.load_settings(path)[0]["ui_language"] == "en"     # invalid -> default
    config.save_settings({**config.defaults(), "ui_language": "de"}, path)
    assert config.load_settings(path)[0]["ui_language"] == "de"     # valid kept
