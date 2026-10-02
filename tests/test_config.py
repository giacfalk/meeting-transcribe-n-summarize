import json

from meetingrec import config


def test_missing_file_is_created_with_defaults(tmp_path):
    path = tmp_path / "MeetingRecorder" / "settings.json"
    settings, warnings = config.load_settings(path)
    assert settings == config.DEFAULTS
    assert warnings == []
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["_help"] == config.HELP_URL
    assert written["whisper_model"] == "base"


def test_values_are_merged_and_bad_ones_fall_back(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "whisper_model": "small",
        "keep_audio": "yes",              # wrong type
        "silence_timeout": 30.5,          # int default accepts any number
        "summary_backend": "chatgpt",     # not a valid choice
        "unknown_key": 1,                 # ignored
    }), encoding="utf-8")
    settings, warnings = config.load_settings(path)
    assert settings["whisper_model"] == "small"
    assert settings["keep_audio"] is False
    assert settings["silence_timeout"] == 30.5
    assert settings["summary_backend"] == "claude-cli"
    assert len(warnings) == 2


def test_invalid_json_uses_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ not json", encoding="utf-8")
    settings, warnings = config.load_settings(path)
    assert settings == config.DEFAULTS
    assert "ignored" in warnings[0]


def test_notepad_bom_is_tolerated(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"language": "it"}), encoding="utf-8-sig")
    settings, warnings = config.load_settings(path)
    assert settings["language"] == "it" and warnings == []


def test_update_setting_keeps_other_keys(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"language": "de", "my_note": "keep me"}), encoding="utf-8")
    config.update_setting("whisper_model", "medium", path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["whisper_model"] == "medium"
    assert data["language"] == "de" and data["my_note"] == "keep me"


def test_output_dir_default_and_override(tmp_path, monkeypatch):
    monkeypatch.setenv("OneDrive", str(tmp_path / "od"))
    assert config.output_dir({"output_dir": ""}) == (
        tmp_path / "od" / "Documents" / "Meeting Recorder" / "recordings")
    assert config.output_dir({"output_dir": str(tmp_path / "x")}) == tmp_path / "x"
