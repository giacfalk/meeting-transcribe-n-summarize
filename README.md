# Meeting Recorder — transcribe & summarize

A small Windows desktop app that records your meetings, transcribes them locally,
and writes a structured summary.

- **One button.** Click *Start Recording*, click *Stop*. That's it.
- **Captures both sides of a call.** Records your **microphone** and the **system audio**
  (WASAPI loopback) at the same time, so it works with Zoom, Teams, Meet, Webex, or any
  other app without plugins or bots joining the call.
- **Local transcription.** Uses [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
  on the CPU, with automatic language detection and `[MM:SS]` timestamps. The audio never
  leaves your machine.
- **Automatic summaries.** Each transcript is summarized by the
  [Claude Code](https://code.claude.com/docs/en/overview) CLI using your existing Claude
  login (no API key). The summary has an overview, key points, decisions and action items,
  written in the same language as the meeting.
- **Doesn't block you.** Transcription and summarization run in a background queue, so you
  can start the next recording right away.
- **Nothing gets lost.** If the app is closed or a step fails, the `.wav` or transcript stays
  on disk. *Process pending* picks up anything left unfinished.
- **Auto-stop.** Recording stops automatically after 2 minutes of silence.

## Install

### Option A — download the Windows build (no Python needed)

1. Download `MeetingRecorder-vX.Y.Z-windows-x64.zip` from the
   [latest release](../../releases/latest).
2. Unzip it to a short path such as `C:\Apps\`. Very deep folders can hit Windows'
   260-character path limit.
3. Run `MeetingRecorder.exe`. Optionally right-click it and choose *Pin to taskbar*.

> The executable is not code-signed, so Windows SmartScreen may show
> *"Windows protected your PC"*. Click **More info → Run anyway**. You can always
> build it yourself from source instead (see below).

### Option B — run from source

Requires Windows 10/11 and Python 3.10 or newer.

```powershell
git clone https://github.com/giacfalk/meeting-transcribe-n-summarize.git
cd meeting-transcribe-n-summarize
pip install -r requirements.txt
python meeting_recorder.py
```

You can also double-click `Start Meeting Recorder.bat`.

### Optional — enable summaries

Summaries need the Claude Code CLI to be installed and logged in, with `claude` on your
`PATH` (or at `~/.local/bin/claude.exe`). See the
[Claude Code setup guide](https://code.claude.com/docs/en/setup).
If the CLI isn't found, the app still records and transcribes, and simply skips the summary.

## Usage

| Control | What it does |
|---|---|
| **Start / Stop Recording** | Toggle recording. On stop, the audio is queued for transcription. |
| **Model** | Whisper model size: `tiny`, `base` (default), `small`, `medium`, `large-v2`. Larger is more accurate but slower. Applies to the next transcription. |
| **Always on top** | Keep the small window above other windows during a call. |
| **Process pending** | Transcribe any leftover `.wav` files and summarize any transcripts that don't have a summary yet. |
| **Open folder** | Open the output folder in Explorer. |

The first transcription with a given model downloads it from Hugging Face (about 150 MB for
`base`, around 3 GB for `large-v2`), so you need an internet connection the first time.

### Output

Files go to `Documents\Meeting Recorder\recordings\`. If OneDrive is set up, that's the
OneDrive-synced Documents folder.

```
Meeting Recorder/recordings/
├── meeting_20260514_103000.txt          # transcript with [MM:SS] timestamps
└── summary/
    └── meeting_20260514_103000_summary.txt
```

By default the `.wav` is deleted once its transcript is written.

## Configuration

Settings are constants at the top of [`meeting_recorder.py`](meeting_recorder.py):

| Setting | Default | Meaning |
|---|---|---|
| `OUTPUT_DIR` | `Documents/Meeting Recorder/recordings` | Where transcripts and summaries are written |
| `WHISPER_MODEL` | `"base"` | Default model size |
| `KEEP_AUDIO_FILE` | `False` | Keep the `.wav` next to the transcript |
| `SILENCE_TIMEOUT` | `120` | Seconds of silence before auto-stop |
| `SILENCE_THRESHOLD` | `0.01` | RMS level counted as silence |
| `ENABLE_SUMMARY` | `True` | Summarize transcripts with the Claude CLI |
| `SUMMARY_MODEL` | `""` | Claude model alias (`""` = CLI default) |
| `SUMMARY_SYSTEM_PROMPT` | *(see file)* | Instructions for the summary format |

If you change these, run from source or rebuild the `.exe`.

## Building the .exe

Build inside a clean virtual environment. Packages left over in a global Python install
(for example an old `setuptools`) can break PyInstaller.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt pyinstaller Pillow
.venv\Scripts\python build_exe.py
```

This draws `icon.ico` and runs PyInstaller. The output is
`dist\MeetingRecorder\MeetingRecorder.exe` (a folder bundle for fast startup).

## Privacy

- **Audio and transcription stay local.** Whisper runs on your CPU, and recordings and
  transcripts are only written to your output folder.
- **Summaries are not local.** When summaries are enabled, the transcript text is sent to
  Anthropic through the Claude CLI under your Claude account and its data-usage terms. Set
  `ENABLE_SUMMARY = False` if your meetings must not leave your machine.

## ⚠️ Recording consent

Recording conversations without the consent of all participants is illegal in many
jurisdictions. Some require all parties to consent, and GDPR applies in the EU. **Always
tell participants and get their consent before recording.** You are responsible for using
this tool lawfully.

## Limitations

- Windows only (WASAPI loopback through `soundcard`, Explorer integration).
- Mic and system audio are mixed into one mono track, so the transcript doesn't say who is
  speaking.
- Transcription runs on the CPU. Long meetings with large models take a while.

## License

Copyright (C) 2026 Giacomo Falchetta

This program is free software: you can redistribute it and/or modify it under the terms of
the GNU General Public License as published by the Free Software Foundation, either version 3
of the License, or (at your option) any later version. See [LICENSE](LICENSE).

The Windows build bundles third-party components (Python, faster-whisper, CTranslate2,
ONNX Runtime, soundcard, soundfile, NumPy, Tcl/Tk), each under its own license.
