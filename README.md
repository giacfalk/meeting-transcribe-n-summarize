<p align="center">
  <img src="assets/logo.png" width="128" alt="Meeting Recorder logo">
</p>

<h1 align="center">Meeting Recorder</h1>

<p align="center">
  One-button Windows app that records your meetings, transcribes them locally,<br>
  tells you who said what, and writes the summary for you.
</p>

<p align="center">
  <a href="https://github.com/giacfalk/meeting-transcribe-n-summarize/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/giacfalk/meeting-transcribe-n-summarize/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/giacfalk/meeting-transcribe-n-summarize/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/giacfalk/meeting-transcribe-n-summarize"></a>
  <a href="https://github.com/giacfalk/meeting-transcribe-n-summarize/releases"><img alt="Downloads" src="https://img.shields.io/github/downloads/giacfalk/meeting-transcribe-n-summarize/total"></a>
  <a href="LICENSE"><img alt="License: GPL-3.0" src="https://img.shields.io/github/license/giacfalk/meeting-transcribe-n-summarize"></a>
  <img alt="Platform: Windows 10 | 11" src="https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078D6">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white">
</p>

---

- **One button.** Click *Start Recording*, then *Stop*. That's all you have to do.
- **Records both sides of a call.** It captures your **microphone** and the **system audio**
  (WASAPI loopback), so it works with Zoom, Teams, Meet, Webex or any other app. There are
  no plugins, and no bot joins your call.
- **Who said what.** Your voice and everyone else's are recorded on separate channels, so
  every line of the transcript is labelled **Me** or **Others**.
- **Local transcription** with [faster-whisper](https://github.com/SYSTRAN/faster-whisper),
  with automatic language detection and timestamps. The audio never leaves your machine.
- **Automatic summaries** in Markdown: a title, an overview, key points, decisions and action
  items, written in the meeting's own language. They come from the
  [Claude Code](https://code.claude.com/docs/en/overview) CLI (your Claude login, no API
  key), the Anthropic API, or a **fully local model through [Ollama](https://ollama.com)**.
- **Crash-safe.** Audio is written to disk while you record, so a crash or power cut loses
  only the last couple of seconds. *Process pending* finishes anything left unfinished.
- **Doesn't block you.** Transcripts and summaries are made in the background, so you can
  start the next recording right away.
- **Optional extras:** a global start/stop hotkey, `.srt` subtitles, GPU transcription, and
  auto-stop after a configurable stretch of silence.

## Install

### Option A: download the Windows build (no Python needed)

1. Download `MeetingRecorder-vX.Y.Z-windows-x64.zip` from the
   [latest release](https://github.com/giacfalk/meeting-transcribe-n-summarize/releases/latest).
2. Unzip it to a short path such as `C:\Apps\`. Very deep folders can hit Windows'
   260-character path limit.
3. Run `MeetingRecorder.exe`. Optionally right-click it and choose *Pin to taskbar*.

> The executable is not code-signed, so Windows SmartScreen may show
> *"Windows protected your PC"*. Click **More info → Run anyway**. Every release is built
> from source by [GitHub Actions](https://github.com/giacfalk/meeting-transcribe-n-summarize/actions)
> and comes with a SHA-256 checksum. You can also build it yourself (see below).

### Option B: run from source

Requires Windows 10/11 and Python 3.10 or newer.

```powershell
git clone https://github.com/giacfalk/meeting-transcribe-n-summarize.git
cd meeting-transcribe-n-summarize
pip install -r requirements.txt
python meeting_recorder.py
```

You can also double-click `Start Meeting Recorder.bat`.

## Usage

| Control | What it does |
|---|---|
| **Start / Stop Recording** | Toggle recording. While recording, the window title shows `● REC mm:ss`, so you can see it on the taskbar too. |
| **Model** | Whisper model: `tiny`, `base` (default), `small`, `medium`, `large-v3-turbo`, `large-v3`, `large-v2`. Larger models are more accurate but slower. `large-v3-turbo` is the best accuracy for the speed. |
| **Always on top** | Keep the small window above other windows during a call. |
| **Process pending** | Transcribe any leftover recordings and summarize transcripts that don't have a summary yet. |
| **Open folder** | Open the output folder in Explorer. |
| **Settings** | Open `settings.json` in Notepad (see [Configuration](#configuration)). |

The first time you use a model, it is downloaded from Hugging Face (about 150 MB for
`base`, 1.6 GB for `large-v3-turbo`).

### Output

Files go to `Documents\Meeting Recorder\recordings\`. If OneDrive is set up, that's the
OneDrive-synced Documents folder.

```
Meeting Recorder/recordings/
├── meeting_20260514_103000.txt           # transcript
├── meeting_20260514_103000.srt           # subtitles (optional)
└── summary/
    └── meeting_20260514_103000_summary.md
```

A transcript looks like this:

```
Started    : 2026-05-14 10:30:00
Transcribed: 2026-05-14 11:02:13
============================================================

[00:03] Me: Good morning, shall we start with the budget?
[00:07] Others: Yes. The report is ready, I'll share my screen.
```

By default the `.wav` is deleted once its transcript is written.

### How speaker labels work

The recording is stereo: the left channel is your microphone and the right channel is
whatever your computer plays (the other participants). Each channel is transcribed on its
own and the lines are merged by time. **"Others" means everyone else on the call together**,
so it can't tell remote participants apart from each other.

When a recording starts, the log shows which microphone and speaker are in use. If the
microphone delivers pure digital silence (muted, or a headset jack with nothing plugged in),
the app warns you after a few seconds, so you don't lose your side of the meeting.

A channel that never gets louder than `silence_threshold` (for example a mic that only
hears faint room noise) is skipped. Otherwise Whisper tends to invent sentences from noise.

If you use speakers instead of headphones, your microphone also picks up the other
participants. The app detects these echoes (mic lines that repeat what the remote side said
at the same moment) and drops them. Headphones still give the cleanest result. Set
`"speaker_labels": false` to get a single unlabelled transcript instead.

## Configuration

Click **Settings**, or edit `%APPDATA%\MeetingRecorder\settings.json` directly. The file
is created with every option on first launch. Changes apply as soon as you switch back to
the app window.

| Setting | Default | Meaning |
|---|---|---|
| `output_dir` | `""` | Where files are written (`""` = `Documents/Meeting Recorder/recordings`) |
| `whisper_model` | `"base"` | Whisper model (any [faster-whisper model name](https://github.com/SYSTRAN/faster-whisper) works) |
| `device` | `"cpu"` | `"cpu"`, or `"auto"` / `"cuda"` to use an NVIDIA GPU (needs CUDA 12 + cuDNN 9; falls back to the CPU) |
| `language` | `""` | `""` = auto-detect, or a language code such as `"en"`, `"it"`, `"de"` |
| `microphone` | `""` | `""` = the Windows default input, or part of a device name, e.g. `"Headset"` or `"Microphone Array"` |
| `speaker_labels` | `true` | Label lines with `mic_label` / `others_label` |
| `mic_label` / `others_label` | `"Me"` / `"Others"` | The labels used in transcripts |
| `keep_audio` | `false` | Keep the `.wav` next to the transcript |
| `write_srt` | `false` | Also write an `.srt` subtitle file |
| `silence_timeout` | `120` | Seconds of silence before recording stops itself (`0` = never) |
| `silence_threshold` | `0.01` | Audio level (0–1) counted as silence. Used for auto-stop, and a channel that never gets louder than this isn't transcribed. |
| `hotkey` | `""` | Global start/stop shortcut, e.g. `"ctrl+alt+r"` (works even when the app is minimized) |
| `summary_backend` | `"claude-cli"` | `"claude-cli"`, `"anthropic-api"`, `"ollama"` or `"none"` |
| `summary_model` | `""` | Model for the summary backend (`""` = its default; required for Ollama) |
| `summary_prompt` | `""` | Your own summary instructions (`""` = the built-in prompt) |
| `anthropic_api_key` | `""` | API key for `anthropic-api`. The `ANTHROPIC_API_KEY` environment variable is better. |
| `ollama_url` | `"http://localhost:11434"` | Where Ollama is running |

### Summary backends

| Backend | Needs | Where your transcript goes |
|---|---|---|
| `claude-cli` (default) | [Claude Code](https://code.claude.com/docs/en/setup) installed and logged in, with `claude` on your `PATH` | Anthropic, under your Claude account |
| `anthropic-api` | An Anthropic API key (`ANTHROPIC_API_KEY`). The default model is `claude-opus-5-5`. | Anthropic, under your API account |
| `ollama` | [Ollama](https://ollama.com) running, with a model pulled (e.g. `ollama pull llama3.1`) and set as `summary_model` | **Nowhere.** It stays on your machine. |
| `none` | – | – |

A transcript is untrusted input, because anyone in a meeting can say *"ignore your
instructions and …"*. So the summarizer only ever gets text in and text out:

- The Claude CLI runs with no tools and no MCP servers.
- The summary run is not saved as a Claude Code session.
- The API and Ollama backends have no tools.

If a backend isn't available, the app still records and transcribes. It summarizes later,
when you click *Process pending*.

## Privacy

- **Audio and transcription stay local.** Whisper runs on your machine, and audio,
  transcripts and summaries are only written to your output folder.
- **Summaries depend on the backend** (see the table above). With `claude-cli` or
  `anthropic-api`, the transcript text is sent to Anthropic under your account's data-usage
  terms. Choose `ollama` or `none` if your meetings must not leave your machine.

## ⚠️ Recording consent

Recording conversations without the consent of all participants is illegal in many
jurisdictions. Some require all parties to consent, and GDPR applies in the EU. **Always
tell participants and get their consent before recording.** You are responsible for using
this tool lawfully.

## Development

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python -m pytest            # -m "not slow" skips the real-transcription test
.venv\Scripts\python -m ruff check .
```

The code lives in [`meetingrec/`](meetingrec):

| Module | Contents |
|---|---|
| `audio.py` | Capture and the crash-safe stereo WAV writer |
| `transcribe.py` | Whisper, speaker merging and echo removal |
| `summarize.py` | The summary backends |
| `config.py` | Settings |
| `app.py` | The GUI |

### Building the .exe

Build inside a clean virtual environment. Packages left over in a global Python install
(for example an old `setuptools`) can break PyInstaller. `requirements-build.txt` pins
the exact versions used for releases.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-build.txt
.venv\Scripts\python build_exe.py --zip
```

The output is `dist\MeetingRecorder\MeetingRecorder.exe`, a folder bundle for fast startup.
`--zip` also writes the release zip and its checksum to `release\`.

### Releasing

Bump `__version__` in `meetingrec/__init__.py`, update [CHANGELOG.md](CHANGELOG.md), then
push a matching tag:

```powershell
git tag v1.2.0
git push origin v1.2.0
```

CI tests and builds the exe, then attaches the zip to the GitHub release.

## Limitations and roadmap

- Windows only for now (WASAPI loopback, Explorer integration). See the
  [open issues](https://github.com/giacfalk/meeting-transcribe-n-summarize/issues) for
  Linux support, a tray icon, and other ideas. Contributions are welcome.
- "Others" covers every remote participant together. Telling individual remote voices
  apart would need a diarization model.

## License

Copyright (C) 2026 Giacomo Falchetta

This program is free software: you can redistribute it and/or modify it under the terms of
the GNU General Public License as published by the Free Software Foundation, either version 3
of the License, or (at your option) any later version. See [LICENSE](LICENSE).

The Windows build bundles third-party components (Python, faster-whisper, CTranslate2,
ONNX Runtime, soundcard, soundfile, NumPy, the Anthropic SDK, Tcl/Tk), each under its own
license.
