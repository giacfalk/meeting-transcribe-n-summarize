"""Settings dialog: the everyday options of settings.json in a small tabbed window.

settings.json stays the source of truth: the dialog only changes the keys it
shows and keeps everything else in the file as it is.
"""

import tkinter as tk
from tkinter import filedialog, ttk

from . import config, meetings, osutil, summarize, tray
from .hotkey import parse_hotkey

AUTO = "Auto-detect"
DEFAULT_MIC = "System default"
DEFAULT_SYSTEM = "Default output (loopback)"
LANGUAGES = [AUTO, "en", "it", "de", "fr", "es", "pt", "nl", "pl", "sv", "da", "no", "fi",
             "cs", "ro", "hu", "el", "tr", "ru", "uk", "ar", "he", "hi", "zh", "ja", "ko"]
_MODEL_HINTS = {
    "claude-cli": "e.g. sonnet or opus; empty = the CLI's default",
    "anthropic-api": f"empty = {summarize.DEFAULT_API_MODEL}",
    "ollama": "required, e.g. llama3.1 (run: ollama pull llama3.1)",
    "none": "",
}


def _default_list_devices():
    from .audio import list_devices
    return list_devices()


class SettingsDialog(tk.Toplevel):
    def __init__(self, master, settings: dict, on_saved, list_devices=_default_list_devices):
        super().__init__(master)
        self.title("Settings - Meeting Recorder")
        self.transient(master)
        self.resizable(False, False)
        self._on_saved = on_saved
        self._s = settings
        self._error = tk.StringVar()
        self._hint_font = (osutil.FONTS[0], 8)
        try:
            self._mics, self._system_sources = list_devices()
        except Exception:
            self._mics, self._system_sources = [], []

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=10, pady=(10, 0))
        tabs = {}
        for name in ("Transcription", "Audio", "Summaries", "Behavior"):
            frame = ttk.Frame(notebook, padding=12)
            frame.columnconfigure(1, weight=1)
            notebook.add(frame, text=name)
            tabs[name] = frame
        self._build_transcription(tabs["Transcription"])
        self._build_audio(tabs["Audio"])
        self._build_summaries(tabs["Summaries"])
        self._build_behavior(tabs["Behavior"])

        bottom = ttk.Frame(self, padding=(10, 8, 10, 10))
        bottom.pack(fill="x")
        ttk.Button(bottom, text="Edit settings.json...", command=self._edit_json).pack(side="left")
        ttk.Button(bottom, text="Save", command=self.save).pack(side="right")
        ttk.Button(bottom, text="Cancel", command=self.destroy).pack(side="right", padx=6)
        ttk.Label(self, textvariable=self._error, foreground="#c62828",
                  wraplength=420).pack(fill="x", padx=12, pady=(0, 8))
        self.bind("<Escape>", lambda _e: self.destroy())

        self.update_idletasks()
        x = master.winfo_rootx() + 30
        y = master.winfo_rooty() + 30
        self.geometry(f"+{x}+{y}")
        self.grab_set()

    # ---- layout helpers -------------------------------------------------

    def _row(self, frame, label: str, widget, hint: str = "") -> None:
        row = frame.grid_size()[1]
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="nw", pady=(4, 0), padx=(0, 12))
        widget.grid(row=row, column=1, sticky="ew", pady=(3, 0))
        if hint:
            ttk.Label(frame, text=hint, font=self._hint_font, foreground="#6c7086",
                      wraplength=300).grid(row=row + 1, column=1, sticky="w")

    def _check(self, frame, text: str, key: str, hint: str = "", enabled: bool = True):
        var = tk.BooleanVar(value=self._s[key])
        cb = ttk.Checkbutton(frame, text=text, variable=var)
        if not enabled:
            cb.state(["disabled"])
        row = frame.grid_size()[1]
        cb.grid(row=row, column=0, columnspan=2, sticky="w", pady=(4, 0))
        if hint:
            ttk.Label(frame, text=hint, font=self._hint_font, foreground="#6c7086",
                      wraplength=380).grid(row=row + 1, column=0, columnspan=2, sticky="w",
                                           padx=(22, 0))
        return var

    def _entry(self, frame, key: str, width: int = 32, **kw):
        var = tk.StringVar(value=str(self._s[key]))
        return var, ttk.Entry(frame, textvariable=var, width=width, **kw)

    # ---- tabs -----------------------------------------------------------

    def _build_transcription(self, f):
        self.v_model = tk.StringVar(value=self._s["whisper_model"])
        self._row(f, "Whisper model", ttk.Combobox(f, textvariable=self.v_model,
                                                   values=config.WHISPER_MODELS, width=30),
                  "large-v3-turbo: best accuracy for the speed; tiny/base: fastest")
        self.v_language = tk.StringVar(value=self._s["language"] or AUTO)
        self._row(f, "Language", ttk.Combobox(f, textvariable=self.v_language, values=LANGUAGES,
                                              width=30),
                  "Set it if auto-detection guesses wrong (e.g. mixed-language meetings)")
        self.v_device = tk.StringVar(value=self._s["device"])
        self._row(f, "Run on", ttk.Combobox(f, textvariable=self.v_device, values=config.DEVICES,
                                            state="readonly", width=30),
                  "auto / cuda use an NVIDIA GPU if available, otherwise the CPU")
        self.v_labels = self._check(f, "Label speakers (me / others)", "speaker_labels")
        self.v_mic_label, e1 = self._entry(f, "mic_label")
        self._row(f, "Your label", e1)
        self.v_others_label, e2 = self._entry(f, "others_label")
        self._row(f, "Others' label", e2)
        self.v_srt = self._check(f, "Also write .srt subtitles", "write_srt")

    def _build_audio(self, f):
        self.v_mic = tk.StringVar(value=self._s["microphone"] or DEFAULT_MIC)
        self._row(f, "Microphone", ttk.Combobox(f, textvariable=self.v_mic, width=30,
                                                values=[DEFAULT_MIC] + self._mics),
                  "Or type part of a device name")
        self.v_system = tk.StringVar(value=self._s["system_audio"] or DEFAULT_SYSTEM)
        hint = ("macOS: pick a virtual device such as BlackHole (see the README)"
                if osutil.MACOS else "What the other participants say: your speakers/headset")
        self._row(f, "System audio", ttk.Combobox(f, textvariable=self.v_system, width=30,
                                                  values=[DEFAULT_SYSTEM] + self._system_sources),
                  hint)
        self.v_timeout = tk.StringVar(value=str(self._s["silence_timeout"]))
        self._row(f, "Auto-stop after", ttk.Spinbox(f, from_=0, to=3600, increment=30, width=8,
                                                    textvariable=self.v_timeout),
                  "seconds of silence (0 = never)")
        self.v_threshold = tk.StringVar(value=str(self._s["silence_threshold"]))
        self._row(f, "Silence level", ttk.Entry(f, textvariable=self.v_threshold, width=8),
                  "0-1; quieter audio counts as silence and isn't transcribed")
        self.v_keep = self._check(f, "Keep the audio (.wav) after transcribing", "keep_audio")

        self.v_output = tk.StringVar(value=self._s["output_dir"])
        box = ttk.Frame(f)
        box.columnconfigure(0, weight=1)
        ttk.Entry(box, textvariable=self.v_output, width=24).grid(row=0, column=0, sticky="ew")
        ttk.Button(box, text="Browse...", command=self._browse).grid(row=0, column=1, padx=(6, 0))
        self._row(f, "Save to", box, f"Empty = {config.default_output_dir()}")

    def _build_summaries(self, f):
        self.v_backend = tk.StringVar(value=self._s["summary_backend"])
        combo = ttk.Combobox(f, textvariable=self.v_backend, values=config.SUMMARY_BACKENDS,
                             state="readonly", width=30)
        self._row(f, "Summarize with", combo)
        self.v_status = tk.StringVar()
        ttk.Label(f, textvariable=self.v_status, font=self._hint_font).grid(
            row=f.grid_size()[1], column=1, sticky="w")
        self.v_summary_model, e = self._entry(f, "summary_model")
        self.v_model_hint = tk.StringVar()
        self._row(f, "Model", e)
        ttk.Label(f, textvariable=self.v_model_hint, font=self._hint_font,
                  foreground="#6c7086").grid(row=f.grid_size()[1], column=1, sticky="w")
        self.v_api_key, e = self._entry(f, "anthropic_api_key", show="•")
        self._row(f, "Anthropic API key", e, "Only for anthropic-api. The ANTHROPIC_API_KEY "
                  "environment variable is better: settings.json is plain text.")
        self.v_ollama, e = self._entry(f, "ollama_url")
        self._row(f, "Ollama URL", e)
        self.t_prompt = tk.Text(f, width=40, height=4, wrap="word", font=(osutil.FONTS[0], 9))
        self.t_prompt.insert("1.0", self._s["summary_prompt"])
        self._row(f, "Custom prompt", self.t_prompt, "Empty = the built-in prompt")
        combo.bind("<<ComboboxSelected>>", lambda _e: self._update_backend_hints())
        self._update_backend_hints()

    def _build_behavior(self, f):
        self.v_hotkey, e = self._entry(f, "hotkey")
        self._row(f, "Global hotkey", e, "Starts/stops recording from anywhere, e.g. "
                  "ctrl+alt+r. Empty = off.")
        can_watch = meetings.supported()
        self.v_prompt = self._check(
            f, "Offer to record when a call starts", "meeting_prompt",
            "Asks when one of these apps starts using the microphone - never records by itself"
            if can_watch else "Not available on macOS yet", enabled=can_watch)
        self.v_apps = tk.StringVar(value=", ".join(self._s["meeting_apps"]))
        self._row(f, "Meeting apps", ttk.Entry(f, textvariable=self.v_apps, width=32),
                  "Comma-separated parts of app names; * = any app")
        can_tray = tray.supported()
        note = "" if can_tray else "Not available on this system"
        self.v_tray = self._check(f, "Show an icon in the notification area", "tray_icon",
                                  note, enabled=can_tray)
        self.v_min_tray = self._check(f, "Minimizing hides the window to the tray",
                                      "minimize_to_tray", enabled=can_tray)

    # ---- actions --------------------------------------------------------

    def _update_backend_hints(self):
        backend = self.v_backend.get()
        self.v_model_hint.set(_MODEL_HINTS.get(backend, ""))
        trial = {**self._s, "summary_backend": backend,
                 "summary_model": self.v_summary_model.get().strip()}
        reason = summarize.unavailable_reason(trial) if backend != "none" else ""
        self.v_status.set(f"⚠ {reason}" if reason else
                          ("" if backend == "none" else "✓ ready"))

    def _browse(self):
        path = filedialog.askdirectory(parent=self, initialdir=self.v_output.get() or
                                       str(config.default_output_dir()))
        if path:
            self.v_output.set(path)

    def _edit_json(self):
        path = config.settings_path()
        if not path.exists():
            config.save_settings(self._s, path)
        osutil.open_in_text_editor(path)
        self.destroy()

    def collect(self) -> dict:
        """The dialog's values as settings, or ValueError with a message for the user."""
        hotkey = self.v_hotkey.get().strip()
        if hotkey:
            try:
                parse_hotkey(hotkey)
            except ValueError as exc:
                raise ValueError(f"Hotkey: {exc}") from None
        try:
            timeout = int(float(self.v_timeout.get()))
            if timeout < 0:
                raise ValueError
        except ValueError:
            raise ValueError("Auto-stop must be a number of seconds (0 = never).") from None
        try:
            threshold = float(self.v_threshold.get())
            if not 0 <= threshold <= 1:
                raise ValueError
        except ValueError:
            raise ValueError("Silence level must be a number between 0 and 1.") from None
        mic_label, others_label = self.v_mic_label.get().strip(), self.v_others_label.get().strip()
        if self.v_labels.get() and not (mic_label and others_label):
            raise ValueError("Speaker labels can't be empty.")
        ollama = self.v_ollama.get().strip()
        if not ollama.startswith(("http://", "https://")):
            raise ValueError("Ollama URL must start with http:// or https://")
        language = self.v_language.get().strip()
        mic, system = self.v_mic.get().strip(), self.v_system.get().strip()
        return {
            "whisper_model": self.v_model.get().strip() or "base",
            "language": "" if language == AUTO else language,
            "device": self.v_device.get(),
            "speaker_labels": self.v_labels.get(),
            "mic_label": mic_label or "Me",
            "others_label": others_label or "Others",
            "write_srt": self.v_srt.get(),
            "microphone": "" if mic == DEFAULT_MIC else mic,
            "system_audio": "" if system == DEFAULT_SYSTEM else system,
            "silence_timeout": timeout,
            "silence_threshold": threshold,
            "keep_audio": self.v_keep.get(),
            "output_dir": self.v_output.get().strip(),
            "summary_backend": self.v_backend.get(),
            "summary_model": self.v_summary_model.get().strip(),
            "anthropic_api_key": self.v_api_key.get().strip(),
            "ollama_url": ollama,
            "summary_prompt": self.t_prompt.get("1.0", "end").strip(),
            "hotkey": hotkey,
            "meeting_prompt": self.v_prompt.get(),
            "meeting_apps": [a.strip() for a in self.v_apps.get().split(",") if a.strip()],
            "tray_icon": self.v_tray.get(),
            "minimize_to_tray": self.v_min_tray.get(),
        }

    def save(self):
        try:
            changes = self.collect()
        except ValueError as exc:
            self._error.set(str(exc))
            return
        try:
            config.update_settings(changes)
        except OSError as exc:
            self._error.set(f"Could not save settings: {exc}")
            return
        self.destroy()
        self._on_saved()
