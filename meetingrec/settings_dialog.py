"""Settings dialog: the everyday options of settings.json in a small tabbed window.

settings.json stays the source of truth: the dialog only changes the keys it
shows and keeps everything else in the file as it is.
"""

import tkinter as tk
from tkinter import filedialog, ttk

from . import config, i18n, meetings, osutil, summarize, tray
from .hotkey import parse_hotkey
from .i18n import t

# Transcription (Whisper) language codes offered besides "auto"; not the app's own UI language.
_WHISPER_LANGS = ["en", "it", "de", "fr", "es", "pt", "nl", "pl", "sv", "da", "no", "fi",
                  "cs", "ro", "hu", "el", "tr", "ru", "uk", "ar", "he", "hi", "zh", "ja", "ko"]


def _default_list_devices():
    from .audio import list_devices
    return list_devices()


class SettingsDialog(tk.Toplevel):
    def __init__(self, master, settings: dict, on_saved, list_devices=_default_list_devices):
        super().__init__(master)
        self._on_saved = on_saved
        self._s = settings
        self._error = tk.StringVar()
        self._hint_font = (osutil.FONTS[0], 8)
        # Display sentinels for the "empty value" combobox entries, in the active language.
        # They double as the value the entry carries, so collect() compares against the same text.
        self._auto = t("settings.transcription.auto")
        self._default_mic = t("settings.audio.default_mic")
        self._default_system = t("settings.audio.default_system")
        try:
            self._mics, self._system_sources = list_devices()
        except Exception:
            self._mics, self._system_sources = [], []

        self.title(t("settings.title"))
        self.transient(master)
        self.resizable(False, False)

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=10, pady=(10, 0))
        tabs = {}
        for name in ("Transcription", "Audio", "Summaries", "Behavior"):
            frame = ttk.Frame(notebook, padding=(16, 14))
            frame.columnconfigure(1, weight=1)
            notebook.add(frame, text=t(f"settings.tab.{name.lower()}"))
            tabs[name] = frame
        self._build_transcription(tabs["Transcription"])
        self._build_audio(tabs["Audio"])
        self._build_summaries(tabs["Summaries"])
        self._build_behavior(tabs["Behavior"])

        bottom = ttk.Frame(self, padding=(10, 8, 10, 10))
        bottom.pack(fill="x")
        ttk.Button(bottom, text=t("settings.edit_json"), command=self._edit_json).pack(side="left")
        ttk.Button(bottom, text=t("common.save"), command=self.save,
                   style="Primary.TButton").pack(side="right")
        ttk.Button(bottom, text=t("common.cancel"), command=self.destroy).pack(side="right", padx=6)
        ttk.Label(self, textvariable=self._error, foreground="#c62828",
                  wraplength=420).pack(fill="x", padx=12, pady=(0, 8))
        self.bind("<Escape>", lambda _e: self.destroy())

        self.update_idletasks()
        osutil.set_dark_titlebar(self)
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
        self._row(f, t("settings.transcription.model"),
                  ttk.Combobox(f, textvariable=self.v_model, values=config.WHISPER_MODELS,
                               width=30),
                  t("settings.transcription.model.hint"))
        self.v_language = tk.StringVar(value=self._s["language"] or self._auto)
        self._row(f, t("settings.transcription.language"),
                  ttk.Combobox(f, textvariable=self.v_language,
                               values=[self._auto, *_WHISPER_LANGS], width=30),
                  t("settings.transcription.language.hint"))
        self.v_device = tk.StringVar(value=self._s["device"])
        self._row(f, t("settings.transcription.device"),
                  ttk.Combobox(f, textvariable=self.v_device, values=config.DEVICES,
                               state="readonly", width=30),
                  t("settings.transcription.device.hint"))
        self.v_labels = self._check(f, t("settings.transcription.labels"), "speaker_labels")
        self.v_mic_label, e1 = self._entry(f, "mic_label")
        self._row(f, t("settings.transcription.mic_label"), e1)
        self.v_others_label, e2 = self._entry(f, "others_label")
        self._row(f, t("settings.transcription.others_label"), e2)
        self.v_srt = self._check(f, t("settings.transcription.srt"), "write_srt")

        ttk.Separator(f, orient="horizontal").grid(
            row=f.grid_size()[1], column=0, columnspan=2, sticky="ew", pady=(10, 2))
        self.v_live_mode = tk.StringVar(value=self._s["live_mode"])
        self._row(f, t("settings.transcription.live_mode"),
                  ttk.Combobox(f, textvariable=self.v_live_mode, values=config.LIVE_MODES,
                               state="readonly", width=30),
                  t("settings.transcription.live_mode.hint"))
        self.v_live_model = tk.StringVar(value=self._s["live_model"])
        self._row(f, t("settings.transcription.live_model"),
                  ttk.Combobox(f, textvariable=self.v_live_model, values=config.WHISPER_MODELS,
                               width=30),
                  t("settings.transcription.live_model.hint"))
        self.v_live_interval = tk.StringVar(value=str(self._s["live_interval"]))
        self._row(f, t("settings.transcription.live_interval"),
                  ttk.Spinbox(f, from_=2, to=120, increment=5, width=8,
                              textvariable=self.v_live_interval),
                  t("settings.transcription.live_interval.hint"))
        self.v_live_notes_interval = tk.StringVar(value=str(self._s["live_notes_interval"]))
        self._row(f, t("settings.transcription.live_notes_interval"),
                  ttk.Spinbox(f, from_=60, to=3600, increment=60, width=8,
                              textvariable=self.v_live_notes_interval),
                  t("settings.transcription.live_notes_interval.hint"))
        self.v_live_catchup = self._check(
            f, t("settings.transcription.live_catchup"), "live_catchup",
            t("settings.transcription.live_catchup.hint"))

    def _build_audio(self, f):
        self.v_mic = tk.StringVar(value=self._s["microphone"] or self._default_mic)
        self._row(f, t("settings.audio.mic"),
                  ttk.Combobox(f, textvariable=self.v_mic, width=30,
                               values=[self._default_mic] + self._mics),
                  t("settings.audio.mic.hint"))
        self.v_system = tk.StringVar(value=self._s["system_audio"] or self._default_system)
        hint = (t("settings.audio.system.hint.macos") if osutil.MACOS
                else t("settings.audio.system.hint"))
        self._row(f, t("settings.audio.system"),
                  ttk.Combobox(f, textvariable=self.v_system, width=30,
                               values=[self._default_system] + self._system_sources),
                  hint)
        self.v_timeout = tk.StringVar(value=str(self._s["silence_timeout"]))
        self._row(f, t("settings.audio.timeout"),
                  ttk.Spinbox(f, from_=0, to=3600, increment=30, width=8,
                              textvariable=self.v_timeout),
                  t("settings.audio.timeout.hint"))
        self.v_threshold = tk.StringVar(value=str(self._s["silence_threshold"]))
        self._row(f, t("settings.audio.threshold"),
                  ttk.Entry(f, textvariable=self.v_threshold, width=8),
                  t("settings.audio.threshold.hint"))
        self.v_keep = self._check(f, t("settings.audio.keep"), "keep_audio")

        self.v_output = tk.StringVar(value=self._s["output_dir"])
        box = ttk.Frame(f)
        box.columnconfigure(0, weight=1)
        ttk.Entry(box, textvariable=self.v_output, width=24).grid(row=0, column=0, sticky="ew")
        ttk.Button(box, text=t("common.browse"), command=self._browse).grid(row=0, column=1,
                                                                            padx=(6, 0))
        self._row(f, t("settings.audio.save_to"), box,
                  t("settings.audio.save_to.hint", path=config.default_output_dir()))

    def _build_summaries(self, f):
        self.v_backend = tk.StringVar(value=self._s["summary_backend"])
        combo = ttk.Combobox(f, textvariable=self.v_backend, values=config.SUMMARY_BACKENDS,
                             state="readonly", width=30)
        self._row(f, t("settings.summaries.backend"), combo)
        self.v_status = tk.StringVar()
        ttk.Label(f, textvariable=self.v_status, font=self._hint_font).grid(
            row=f.grid_size()[1], column=1, sticky="w")
        self.v_summary_model, e = self._entry(f, "summary_model")
        self.v_model_hint = tk.StringVar()
        self._row(f, t("settings.summaries.model"), e)
        ttk.Label(f, textvariable=self.v_model_hint, font=self._hint_font,
                  foreground="#6c7086").grid(row=f.grid_size()[1], column=1, sticky="w")
        self.v_api_key, e = self._entry(f, "anthropic_api_key", show="•")
        self._row(f, t("settings.summaries.anthropic_key"), e,
                  t("settings.summaries.anthropic_key.hint"))
        self.v_openai_key, e = self._entry(f, "openai_api_key", show="•")
        self._row(f, t("settings.summaries.openai_key"), e, t("settings.summaries.openai_key.hint"))
        self.v_ollama, e = self._entry(f, "ollama_url")
        self._row(f, t("settings.summaries.ollama_url"), e)
        self.t_prompt = tk.Text(f, width=40, height=4, wrap="word", font=(osutil.FONTS[0], 9))
        self.t_prompt.insert("1.0", self._s["summary_prompt"])
        self._row(f, t("settings.summaries.prompt"), self.t_prompt,
                  t("settings.summaries.prompt.hint"))
        combo.bind("<<ComboboxSelected>>", lambda _e: self._update_backend_hints())
        self._update_backend_hints()

    def _build_behavior(self, f):
        self.v_hotkey, e = self._entry(f, "hotkey")
        if osutil.MACOS:
            e.state(["disabled"])
        self._row(f, t("settings.behavior.hotkey"), e,
                  t("settings.behavior.hotkey.hint.macos") if osutil.MACOS
                  else t("settings.behavior.hotkey.hint"))
        can_watch = meetings.supported()
        self.v_prompt = self._check(
            f, t("settings.behavior.meeting_prompt"), "meeting_prompt",
            t("settings.behavior.meeting_prompt.hint") if can_watch
            else t("settings.behavior.meeting_prompt.hint.macos"), enabled=can_watch)
        self.v_apps = tk.StringVar(value=", ".join(self._s["meeting_apps"]))
        self._row(f, t("settings.behavior.meeting_apps"),
                  ttk.Entry(f, textvariable=self.v_apps, width=32),
                  t("settings.behavior.meeting_apps.hint"))
        can_tray = tray.supported()
        note = "" if can_tray else t("settings.behavior.tray.hint.unsupported")
        self.v_tray = self._check(f, t("settings.behavior.tray"), "tray_icon", note,
                                  enabled=can_tray)
        self.v_min_tray = self._check(f, t("settings.behavior.min_tray"), "minimize_to_tray",
                                      enabled=can_tray)

        self._lang_by_name = {i18n.native_name(c): c for c in i18n.LANGUAGES}
        self.v_ui_language = tk.StringVar(value=i18n.native_name(self._s.get("ui_language", "en")))
        self._row(f, t("settings.behavior.ui_language"),
                  ttk.Combobox(f, textvariable=self.v_ui_language,
                               values=list(self._lang_by_name), state="readonly", width=30),
                  t("common.restart_note"))

    # ---- actions --------------------------------------------------------

    def _model_hint(self, backend: str) -> str:
        return {
            "claude-cli": t("settings.summaries.hint.claude"),
            "anthropic-api": t("settings.summaries.hint.anthropic",
                               model=summarize.DEFAULT_API_MODEL),
            "openai-api": t("settings.summaries.hint.openai",
                            model=summarize.DEFAULT_OPENAI_MODEL),
            "ollama": t("settings.summaries.hint.ollama"),
            "none": "",
        }.get(backend, "")

    def _update_backend_hints(self):
        backend = self.v_backend.get()
        self.v_model_hint.set(self._model_hint(backend))
        trial = {**self._s, "summary_backend": backend,
                 "summary_model": self.v_summary_model.get().strip()}
        reason = summarize.unavailable_reason(trial) if backend != "none" else ""
        self.v_status.set(f"⚠ {reason}" if reason else
                          ("" if backend == "none" else f"✓ {t('settings.summaries.ready')}"))

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
                raise ValueError(t("settings.error.hotkey", detail=exc)) from None
        try:
            timeout = int(float(self.v_timeout.get()))
            if timeout < 0:
                raise ValueError
        except ValueError:
            raise ValueError(t("settings.error.timeout")) from None
        try:
            threshold = float(self.v_threshold.get())
            if not 0 <= threshold <= 1:
                raise ValueError
        except ValueError:
            raise ValueError(t("settings.error.threshold")) from None
        mic_label, others_label = self.v_mic_label.get().strip(), self.v_others_label.get().strip()
        if self.v_labels.get() and not (mic_label and others_label):
            raise ValueError(t("settings.error.labels"))
        ollama = self.v_ollama.get().strip()
        if not ollama.startswith(("http://", "https://")):
            raise ValueError(t("settings.error.ollama_url"))
        try:
            live_interval = int(float(self.v_live_interval.get()))
            if live_interval < 2:
                raise ValueError
        except ValueError:
            raise ValueError(t("settings.error.live_interval")) from None
        try:
            live_notes_interval = int(float(self.v_live_notes_interval.get()))
            if live_notes_interval < 60:
                raise ValueError
        except ValueError:
            raise ValueError(t("settings.error.live_notes_interval")) from None
        language = self.v_language.get().strip()
        mic, system = self.v_mic.get().strip(), self.v_system.get().strip()
        return {
            "whisper_model": self.v_model.get().strip() or "base",
            "language": "" if language == self._auto else language,
            "device": self.v_device.get(),
            "live_mode": self.v_live_mode.get(),
            "live_model": self.v_live_model.get().strip() or "base",
            "live_interval": live_interval,
            "live_notes_interval": live_notes_interval,
            "live_catchup": self.v_live_catchup.get(),
            "speaker_labels": self.v_labels.get(),
            "mic_label": mic_label or "Me",
            "others_label": others_label or "Others",
            "write_srt": self.v_srt.get(),
            "microphone": "" if mic == self._default_mic else mic,
            "system_audio": "" if system == self._default_system else system,
            "silence_timeout": timeout,
            "silence_threshold": threshold,
            "keep_audio": self.v_keep.get(),
            "output_dir": self.v_output.get().strip(),
            "summary_backend": self.v_backend.get(),
            "summary_model": self.v_summary_model.get().strip(),
            "anthropic_api_key": self.v_api_key.get().strip(),
            "openai_api_key": self.v_openai_key.get().strip(),
            "ollama_url": ollama,
            "summary_prompt": self.t_prompt.get("1.0", "end").strip(),
            "hotkey": hotkey,
            "meeting_prompt": self.v_prompt.get(),
            "meeting_apps": [a.strip() for a in self.v_apps.get().split(",") if a.strip()],
            "tray_icon": self.v_tray.get(),
            "minimize_to_tray": self.v_min_tray.get(),
            "ui_language": self._lang_by_name.get(self.v_ui_language.get(), "en"),
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
            self._error.set(t("settings.error.save", detail=exc))
            return
        self.destroy()
        self._on_saved()
