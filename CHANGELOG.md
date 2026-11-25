# Changelog

## 1.4.2

### New
- **Catch up on the earlier part of a meeting when you turn Live on partway through.**
  Switching the Live menu on mid-recording now also transcribes what was already recorded,
  in the background, so the live transcript — and any notes — cover the whole meeting instead
  of only what is said after you switch it on. New speech keeps transcribing live at the same
  time, and the earlier lines fill in above it once the background pass finishes. A new
  *Catch up on earlier audio…* option (*Settings → Transcription*, on by default) turns it off
  if it is too CPU-heavy on long meetings.

### Changed
- **One meeting prompt, not two.** With the tray icon on, a detected meeting used to show both
  a Windows toast and the in-window pop-up; it now shows only the pop-up. The pop-up is also
  borderless with rounded corners on Windows 11.

## 1.4.1

### Fixes
- **"Offer to record when a call starts" now actually works in the installed Windows app.**
  The v1.4.0 Windows build was packaged without the microphone-detection libraries
  (`pycaw`, `comtypes`, `psutil`): they were missing from the pinned build dependencies, so
  the frozen app silently fell back to the (now-stale) registry method and never showed the
  prompt — even though it worked when run from source. They are now pinned and bundled, and
  the build fails loudly if they are ever left out again.
- **The meeting prompt is now a proper borderless pop-up** (no title bar), raises itself,
  beeps and briefly flashes the taskbar, so it is hard to miss over a full-screen call.
  Every microphone detection is also written to the log now, to make this easy to diagnose.

### Changed
- **The Live menu takes effect immediately while recording.** Switching it mid-recording now
  starts or stops the live transcript and notes straight away, instead of only applying to
  the next recording.

### Linux
- **The tray icon no longer risks hiding the window for good.** On desktops without a real
  system tray (e.g. COSMIC/Wayland), the icon silently failed to dock but still reported
  itself as running, so *minimize to tray* could withdraw the window with no way to bring it
  back. The app now detects that the icon isn't really there, disables the tray and
  minimize-to-tray, and keeps the window visible. (Thanks to the detailed report in #9.)

## 1.4.0

### Changed
- **Live transcript and notes now sit in side panels.** When a Live mode is on, the window
  widens and shows the live transcript and the running notes to the *right* of the controls,
  instead of stacked underneath them. The notes use a larger font and keep their formatted,
  Markdown rendering, so they are easier to read during a meeting. With Live off, the window
  stays a compact single column, and switching Live mode resizes the window to match.

### Fixes
- **"Offer to record when a call starts" works again on recent Windows 11 builds.** Newer
  builds stopped updating the microphone-usage timestamps in the registry
  (`CapabilityAccessManager`) in real time, so the app could no longer tell when a meeting
  app began using the mic, and the prompt stopped appearing. Detection now uses the Windows
  audio session API — the same signal behind the taskbar mic indicator — which reports active
  microphone use reliably; the registry method stays as a fallback for older Windows. (Adds
  the `pycaw` dependency on Windows.)

## 1.3.0

### New
- **Live ("near real time") transcription.** A new **Live** menu on the main window (and in
  *Settings → Transcription*) chooses what happens while you record: *Off* (unchanged —
  transcribe and summarize when you stop), *Live transcript* (a rolling draft transcript
  appears right in the window as the meeting goes), *Live transcript + notes (on demand)*
  (adds a *Generate now* button for running notes), or *Live transcript + auto notes* (also
  refreshes the notes every few minutes). Each option explains itself with a one-line hint
  under the menu. The live draft uses a fast model (`live_model`, default `base`) on the
  mixed audio and lags a little behind on CPU; the clean, speaker-labelled transcript and
  the summary are still produced from the recording when you stop. New settings: `live_mode`,
  `live_model`, `live_interval`, `live_notes_interval`.
- **Refreshed interface.** The live transcript and notes are now embedded in the main
  window (no separate window), the notes are rendered as formatted, easy-to-read text, the
  window is resizable, and the app uses a dark title bar on Windows 11 with a tidied,
  consistent dark theme throughout (including the Settings dialog).

## 1.2.0

### New
- **Windows installer.** `…-windows-x64-setup.exe` installs per user (no admin rights), adds
  a Start-menu entry and an uninstaller, and upgrades earlier versions in place. The portable
  zip is still available.
- **Settings window.** *Settings* now opens a tabbed window with the everyday options
  (model, language, devices, summaries, hotkey, call prompts, tray). It checks your input
  and shows whether the chosen summary backend is ready. *Edit settings.json…* still opens
  the file itself.
- **Offer to record when a call starts.** When Teams, Zoom, Meet in a browser and so on
  start using the microphone, a prompt asks whether to record, and asks again whether to
  stop when the call ends. It never records by itself. This works on Windows and Linux; the
  list of apps is configurable.
- **Tray icon** (Windows, Linux) with *Start/Stop recording*, *Process pending*, *Open
  folder* and *Quit*. A red dot shows while recording. Optionally, minimizing hides the
  window to the tray.
- **Linux support** (PulseAudio / PipeWire), with an *experimental* `linux-x64.tar.gz`
  build and an `install.sh` that adds it to the application menu.
- **macOS support** (*experimental* Apple Silicon build). It records the microphone; with
  a virtual device such as BlackHole it also records the other participants.
- A **`system_audio`** setting picks which device is recorded as "others".
- The global **hotkey** now also works on Linux (X11).
- A **log file** (`meetingrec.log`, next to `settings.json`) for troubleshooting.

### Fixes
- Dialogs now show the app icon instead of Tk's default one.

### Project
- CI tests on Windows, Linux and macOS. The Linux tests record real audio through a
  virtual PulseAudio device and check call detection. Each build is started as a smoke
  test that reads the app's log, so a start-up error now fails the build. The Windows
  installer is installed, started and uninstalled in CI.
- PyInstaller 6.22, which handles the Tcl/Tk 9 used by newer Python builds.
- [docs/code-signing.md](docs/code-signing.md) explains the options for signing the builds.

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
