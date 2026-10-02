import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from meetingrec import config, summarize


def settings(**overrides):
    return {**config.DEFAULTS, **overrides}


def test_claude_cli_runs_without_tools_mcp_or_saved_sessions():
    cmd = summarize.claude_cli_command("claude", "PROMPT", "sonnet")
    assert cmd[:2] == ["claude", "-p"]
    assert "--strict-mcp-config" in cmd and "--no-session-persistence" in cmd
    assert cmd[-2:] == ["--tools", ""]                      # last: it takes a list
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert "--model" not in summarize.claude_cli_command("claude", "PROMPT")


def test_prompt_explains_speaker_labels():
    prompt = summarize.system_prompt(settings(mic_label="Ana"))
    assert prompt.startswith(summarize.DEFAULT_SUMMARY_PROMPT)
    assert "'Ana' is the person who recorded" in prompt
    assert summarize.system_prompt(settings(speaker_labels=False)) == \
        summarize.DEFAULT_SUMMARY_PROMPT
    assert summarize.system_prompt(settings(summary_prompt="Custom.",
                                            speaker_labels=False)) == "Custom."


def test_unavailable_reasons(monkeypatch):
    assert "turned off" in summarize.unavailable_reason(settings(summary_backend="none"))
    assert "summary_model" in summarize.unavailable_reason(settings(summary_backend="ollama"))
    assert summarize.unavailable_reason(settings(summary_backend="ollama",
                                                 summary_model="llama3.1")) == ""
    monkeypatch.setattr(summarize, "find_claude_cli", lambda: "")
    assert "not found" in summarize.unavailable_reason(settings())


def test_ollama_context_grows_with_transcript():
    assert summarize.ollama_num_ctx("x" * 100, "p") == 8192
    assert summarize.ollama_num_ctx("x" * 90_000, "p") == 32768
    assert summarize.ollama_num_ctx("x" * 10_000_000, "p") == 131072


@pytest.fixture
def fake_ollama():
    received = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received["path"] = self.path
            received["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            reply = {"message": {"content": "<think>hmm</think># Title\n\n## Overview\nOk."}}
            data = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", received
    server.shutdown()


def test_ollama_backend(fake_ollama):
    url, received = fake_ollama
    out = summarize.summarize("[00:00] Me: hi", settings(
        summary_backend="ollama", summary_model="llama3.1", ollama_url=url + "/"))
    assert out == "# Title\n\n## Overview\nOk."
    assert received["path"] == "/api/chat"
    body = received["body"]
    assert body["model"] == "llama3.1" and body["stream"] is False
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["messages"][1]["content"] == "[00:00] Me: hi"


def test_ollama_unreachable_is_a_summary_error():
    with pytest.raises(summarize.SummaryError, match="could not reach Ollama"):
        summarize.summarize("x", settings(summary_backend="ollama", summary_model="m",
                                          ollama_url="http://127.0.0.1:9"))


def test_anthropic_without_credentials_is_a_summary_error(monkeypatch):
    pytest.importorskip("anthropic")
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")   # never reach the network
    with pytest.raises(summarize.SummaryError):
        summarize.summarize("x", settings(summary_backend="anthropic-api"))
