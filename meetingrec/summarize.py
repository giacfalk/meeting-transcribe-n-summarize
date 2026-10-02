"""Meeting summaries through one of three backends.

    claude-cli     the locally installed Claude Code CLI, using your Claude login
    anthropic-api  the Anthropic API with an API key (needs the `anthropic` package)
    ollama         a local model served by Ollama -- nothing leaves your machine

A transcript is untrusted input: anyone in a meeting can say "ignore your
instructions and ...". Every backend therefore gets text in and text out only,
with no tools.
"""

import json
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_SUMMARY_PROMPT = (
    "You are a meeting-summarization assistant. The user's message is a raw "
    "meeting transcript; lines may start with [MM:SS] or [HH:MM:SS] timestamps "
    "and the transcript may be in any language. Write a concise summary IN THE "
    "SAME LANGUAGE as the transcript, formatted as Markdown:\n"
    "- First line: '# ' followed by a short, descriptive title for the meeting.\n"
    "- ## Overview: 2-3 sentences.\n"
    "- ## Key points: bullet list.\n"
    "- ## Decisions: bullet list (omit this section if there are none).\n"
    "- ## Action items: bullets with owners/deadlines when mentioned (omit if none).\n"
    "Base everything only on the transcript; do not invent information. "
    "Output only the summary itself, with no preamble or sign-off."
)

DEFAULT_API_MODEL = "claude-opus-5-5"
# Models that accept server-side refusal fallbacks and the `effort` setting.
_FALLBACK_MODEL_PREFIXES = ("claude-opus-5", "claude-fable-5", "claude-sonnet-5-5")

# Hide the console window when launching the CLI from the windowed .exe.
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class SummaryError(Exception):
    """A summary could not be produced; the message is shown in the app log."""


def find_claude_cli() -> str:
    """Absolute path to the Claude CLI, or '' if it can't be found."""
    exe = shutil.which("claude")
    if exe:
        return exe
    for cand in (Path.home() / ".local" / "bin" / "claude.exe",
                 Path.home() / ".local" / "bin" / "claude"):
        if cand.exists():
            return str(cand)
    return ""


def unavailable_reason(settings: dict) -> str:
    """'' if the configured backend can run, else why it can't."""
    backend = settings["summary_backend"]
    if backend == "none":
        return "summaries are turned off (summary_backend = none)"
    if backend == "claude-cli" and not find_claude_cli():
        return "Claude CLI ('claude') not found on PATH"
    if backend == "anthropic-api":
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return "the 'anthropic' package is not installed"
    if backend == "ollama" and not settings["summary_model"]:
        return "set summary_model to an Ollama model name, e.g. \"llama3.1\""
    return ""


def system_prompt(settings: dict) -> str:
    prompt = settings["summary_prompt"].strip() or DEFAULT_SUMMARY_PROMPT
    if settings["speaker_labels"]:
        prompt += (f"\nSpeaker labels: '{settings['mic_label']}' is the person who recorded "
                   f"the meeting; '{settings['others_label']}' is everyone else on the call "
                   "(possibly several people).")
    return prompt


def summarize(transcript: str, settings: dict, cwd: Path | None = None) -> str:
    """Return a Markdown summary of the transcript, or raise SummaryError."""
    backend = settings["summary_backend"]
    prompt = system_prompt(settings)
    if backend == "claude-cli":
        text = _claude_cli(transcript, prompt, settings["summary_model"], cwd)
    elif backend == "anthropic-api":
        text = _anthropic_api(transcript, prompt, settings)
    elif backend == "ollama":
        text = _ollama(transcript, prompt, settings)
    else:
        raise SummaryError("summaries are turned off")
    text = text.strip()
    if not text:
        raise SummaryError("the summarizer returned an empty response")
    return text


# -- Claude Code CLI -------------------------------------------------------
def claude_cli_command(cli: str, prompt: str, model: str = "") -> list[str]:
    cmd = [cli, "-p", "--output-format", "text", "--system-prompt", prompt,
           "--strict-mcp-config",        # no MCP servers (mail, drives, ...)
           "--no-session-persistence"]   # don't keep a copy of the transcript as a session
    if model:
        cmd += ["--model", model]
    return cmd + ["--tools", ""]         # no tools at all; keep last (it takes a list)


def _claude_cli(transcript: str, prompt: str, model: str, cwd: Path | None) -> str:
    cli = find_claude_cli()
    if not cli:
        raise SummaryError("Claude CLI ('claude') not found on PATH")
    try:
        proc = subprocess.run(
            claude_cli_command(cli, prompt, model), input=transcript,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(cwd) if cwd else None, creationflags=_CREATE_NO_WINDOW, timeout=600,
        )
    except subprocess.TimeoutExpired as exc:
        raise SummaryError("the Claude CLI timed out after 10 minutes") from exc
    except OSError as exc:
        raise SummaryError(f"the Claude CLI failed to launch: {exc}") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise SummaryError(tail[-1] if tail
                           else f"the Claude CLI exited with code {proc.returncode}")
    return proc.stdout or ""


# -- Anthropic API ---------------------------------------------------------
def _anthropic_api(transcript: str, prompt: str, settings: dict) -> str:
    try:
        import anthropic
    except ImportError as exc:
        raise SummaryError("install the 'anthropic' package to use summary_backend "
                           "\"anthropic-api\"") from exc

    model = settings["summary_model"] or DEFAULT_API_MODEL
    extra: dict = {}
    if model.startswith(_FALLBACK_MODEL_PREFIXES):
        # If a safety classifier declines, retry server-side on Anthropic's
        # recommended fallback model instead of returning no summary.
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default",
                 "output_config": {"effort": "medium"}}
    try:
        # api_key=None lets the SDK use ANTHROPIC_API_KEY or an `ant auth login` profile.
        client = anthropic.Anthropic(api_key=settings["anthropic_api_key"] or None,
                                     max_retries=3)
        with client.beta.messages.stream(
            model=model, max_tokens=16000, system=prompt,
            messages=[{"role": "user", "content": transcript}], **extra,
        ) as stream:
            message = stream.get_final_message()
    except anthropic.AuthenticationError as exc:
        raise SummaryError("Anthropic API key missing or invalid (set ANTHROPIC_API_KEY "
                           "or anthropic_api_key)") from exc
    except anthropic.RateLimitError as exc:
        raise SummaryError("rate-limited by the Anthropic API; use 'Process pending' "
                           "to retry later") from exc
    except anthropic.APIStatusError as exc:
        raise SummaryError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise SummaryError("could not reach the Anthropic API (network error)") from exc
    except (anthropic.AnthropicError, TypeError) as exc:   # e.g. no credentials at all
        raise SummaryError(f"Anthropic API: {exc}") from exc

    if message.stop_reason == "refusal":
        raise SummaryError("the model declined to summarize this transcript")
    text = "".join(block.text for block in message.content if block.type == "text")
    if message.stop_reason == "max_tokens":
        text += "\n\n_(summary truncated)_"
    return text


# -- Ollama ----------------------------------------------------------------
def ollama_num_ctx(transcript: str, prompt: str) -> int:
    """Context window big enough for the transcript (Ollama's default would cut it off)."""
    need = (len(transcript) + len(prompt)) // 3 + 2048   # ~3 chars/token + room for the reply
    ctx = 8192
    while ctx < need and ctx < 131072:
        ctx *= 2
    return ctx


def _ollama(transcript: str, prompt: str, settings: dict) -> str:
    model = settings["summary_model"]
    if not model:
        raise SummaryError("set summary_model to an Ollama model name, e.g. \"llama3.1\"")
    base = settings["ollama_url"].rstrip("/")
    body = json.dumps({
        "model": model, "stream": False,
        "options": {"num_ctx": ollama_num_ctx(transcript, prompt)},
        "messages": [{"role": "system", "content": prompt},
                     {"role": "user", "content": transcript}],
    }).encode("utf-8")
    req = urllib.request.Request(f"{base}/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=1800) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()[:200]
        raise SummaryError(f"Ollama error {exc.code}: {detail}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise SummaryError(f"could not reach Ollama at {base} ({exc})") from exc
    except ValueError as exc:
        raise SummaryError("Ollama returned invalid JSON") from exc
    text = (data.get("message") or {}).get("content", "")
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S)   # reasoning models
