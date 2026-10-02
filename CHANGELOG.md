# Changelog

## 1.1.0

### New
- **Speaker labels.** Recordings are now stereo (left = your microphone, right = system
  audio). Each side is transcribed separately, so transcript lines read `Me:` / `Others:`.
  When you use speakers, mic echoes of the remote audio are detected and dropped.
- **Crash-safe recording.** Audio is streamed to disk while you record, so memory use stays
  flat in long meetings, and a crash loses at most a couple of seconds.
  Interrupted recordings are repaired automatically.
- **Settings file** (`%APPDATA%\MeetingRecorder\settings.json`, *Settings* button). Every
  option can now be changed without rebuilding, and changes apply when you switch back to
  the window. The selected model is remembered.
- **More summary backends:** the Anthropic API (`anthropic-api`, default model
  `claude-opus-5-5`) and fully local models through Ollama (`ollama`), alongside the Claude
  CLI. You can also set `none`.
- **Markdown summaries** with a title (`summary/<name>_summary.md`). Existing `.txt`
  summaries are still recognized, so nothing gets summarized twice.
- **Models:** `large-v3-turbo` and `large-v3`. A fixed `language` option. Optional GPU
  transcription (`device`: `auto` / `cuda`) that falls back to the CPU.
- **Microphone checks.** Each recording logs which mic and speaker it uses, and the app
  warns you if the mic is sending pure digital silence (muted or disconnected input). A new
  `microphone` setting picks a specific input by name.
- A **global hotkey** to start and stop recording (`hotkey`, off by default).
- Optional **`.srt` subtitles** (`write_srt`).
- The window title shows `● REC mm:ss` while recording.
- Transcripts of meetings longer than an hour use `[HH:MM:SS]` timestamps.

### Security and privacy
- The Claude CLI summarizer now runs with **no tools and no MCP servers**, because a
  transcript is untrusted input. It no longer saves each summary as a Claude Code session,
  which used to leave copies of your transcripts under `~/.claude/projects/`.

### Fixes
- **No more invented transcripts from silence.** Audio that never rises above
  `silence_threshold` is no longer sent to Whisper, which made up sentences from room noise
  and could take minutes doing it. Recordings with no speech get a short
  "No speech detected" note instead of being summarized.
- The window icon now loads in the packaged `.exe`.
- Messages referred to a "Transcribe pending recordings" button that is now called
  "Process pending".

### Project
- The code is split into the `meetingrec/` package, with a test suite and ruff linting.
- GitHub Actions tests on Python 3.10 and 3.14, builds the exe from pinned dependencies,
  and attaches it to tagged releases.
- New microphone icon and README header.

## 1.0.0

First public release.
