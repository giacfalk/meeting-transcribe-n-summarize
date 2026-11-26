from meetingrec import config
from meetingrec.welcome import WelcomeDialog, settings_changes


def test_settings_changes_model_and_backend():
    assert settings_changes("small", "claude-cli") == {
        "whisper_model": "small", "summary_backend": "claude-cli"}


def test_settings_changes_api_key_only_for_the_api_backend():
    assert settings_changes("base", "anthropic-api", api_key="  sk-x  ") == {
        "whisper_model": "base", "summary_backend": "anthropic-api", "anthropic_api_key": "sk-x"}
    assert "anthropic_api_key" not in settings_changes("base", "claude-cli", api_key="sk-x")


def test_settings_changes_ollama_model():
    assert settings_changes("base", "ollama", ollama_model="llama3.1") == {
        "whisper_model": "base", "summary_backend": "ollama", "summary_model": "llama3.1"}
    assert "summary_model" not in settings_changes("base", "ollama", ollama_model="   ")


def test_wizard_builds_and_writes_chosen_settings(tk_root, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "app_dir", lambda: tmp_path)
    config.save_settings(config.defaults(), tmp_path / "settings.json")

    done = []
    dlg = WelcomeDialog(tk_root, config.defaults(), done.append)
    dlg._model.set("small")
    dlg._backend.set("ollama")              # fires the trace -> rebuilds the extra field
    dlg._ollama_model.set("llama3.1")
    dlg._finish()
    tk_root.update()

    saved, _ = config.load_settings(tmp_path / "settings.json")
    assert saved["whisper_model"] == "small"
    assert saved["summary_backend"] == "ollama"
    assert saved["summary_model"] == "llama3.1"
    assert done == [False]                  # callback fired; not asked to open full settings
