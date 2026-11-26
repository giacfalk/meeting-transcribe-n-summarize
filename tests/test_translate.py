"""Tests for the build-time translator (tools/translate.py), with the Claude CLI mocked."""

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "tools" / "translate.py"


def _load():
    spec = importlib.util.spec_from_file_location("mr_translate", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tr = _load()


class _Proc:
    def __init__(self, stdout, returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def test_parses_plain_json(monkeypatch):
    monkeypatch.setattr(tr.subprocess, "run", lambda *a, **k: _Proc('{"a": "X", "b": "Y"}'))
    out = tr._claude_translate("claude", {"a": "A", "b": "B"}, "French", "")
    assert out == {"a": "X", "b": "Y"}


def test_strips_code_fence_and_unrequested_keys(monkeypatch):
    monkeypatch.setattr(tr.subprocess, "run",
                        lambda *a, **k: _Proc('```json\n{"a": "X", "junk": "Z"}\n```'))
    assert tr._claude_translate("claude", {"a": "A"}, "German", "") == {"a": "X"}


def test_nonzero_exit_raises(monkeypatch):
    monkeypatch.setattr(tr.subprocess, "run", lambda *a, **k: _Proc("", 1, "boom"))
    with pytest.raises(RuntimeError, match="boom"):
        tr._claude_translate("claude", {"a": "A"}, "French", "")


def test_non_json_output_raises(monkeypatch):
    monkeypatch.setattr(tr.subprocess, "run", lambda *a, **k: _Proc("sorry, here you go"))
    with pytest.raises(RuntimeError, match="did not return JSON"):
        tr._claude_translate("claude", {"a": "A"}, "French", "")


def test_translate_language_writes_and_prunes(monkeypatch, tmp_path):
    monkeypatch.setattr(tr, "LOCALES", tmp_path)
    monkeypatch.setattr(tr, "_claude_translate",
                        lambda cli, batch, lang, model: {k: f"{lang}:{k}" for k in batch})
    # a stale key that is no longer in English should be pruned on write
    (tmp_path / "fr.json").write_text(json.dumps({"gone": "old"}), encoding="utf-8")

    ok = tr._translate_language("claude", "fr", {"a": "A", "b": "B"}, set(), "")

    assert ok
    assert json.loads((tmp_path / "fr.json").read_text(encoding="utf-8")) == {
        "a": "French:a", "b": "French:b"}


def test_translate_language_only_redoes_changed(monkeypatch, tmp_path):
    monkeypatch.setattr(tr, "LOCALES", tmp_path)
    calls = []

    def fake(cli, batch, lang, model):
        calls.append(set(batch))
        return {k: f"{lang}:{k}" for k in batch}

    monkeypatch.setattr(tr, "_claude_translate", fake)
    (tmp_path / "de.json").write_text(json.dumps({"a": "German:a", "b": "German:b"}),
                                      encoding="utf-8")

    # "a" is unchanged and present; only "b" changed -> only "b" is re-translated
    tr._translate_language("claude", "de", {"a": "A", "b": "B2"}, {"b"}, "")

    assert calls == [{"b"}]
