"""Settings dialog: the everyday options of settings.json in a small tabbed window.

Built from custom Tk widgets in the app's dark palette (no ttk), so it matches the
main window and the first-run wizard. settings.json stays the source of truth: the
dialog only changes the keys it shows and keeps everything else in the file as it is.
"""

import tkinter as tk
from tkinter import filedialog

from . import config, i18n, meetings, osutil, summarize, tray
from .hotkey import parse_hotkey
from .i18n import t

SANS = osutil.FONTS[0]
BG = "#1e1e2e"
SURFACE, SURFACE_HI = "#313244", "#45475a"
ACCENT, GREEN = "#89b4fa", "#a6e3a1"
TEXT_MAIN, TEXT_SUB, TEXT_DIM = "#cdd6f4", "#a6adc8", "#7f849c"
RED = "#f38ba8"

# Transcription (Whisper) language codes offered besides "auto"; not the app's own UI language.
_WHISPER_LANGS = ["en", "it", "de", "fr", "es", "pt", "nl", "pl", "sv", "da", "no", "fi",
                  "cs", "ro", "hu", "el", "tr", "ru", "uk", "ar", "he", "hi", "zh", "ja", "ko"]
_TABS = ("transcription", "audio", "summaries", "behavior")


def _default_list_devices():
    from .audio import list_devices
    return list_devices()


class SettingsDialog(tk.Toplevel):
    def __init__(self, master, settings: dict, on_saved, list_devices=_default_list_devices):
        super().__init__(master)
        self._on_saved = on_saved
        self._s = settings
        self._error = tk.StringVar()
        # Display sentinels for the "empty value" entries, in the active language. They double
        # as the value the entry carries, so collect() compares against the same text.
        self._auto = t("settings.transcription.auto")
        self._default_mic = t("settings.audio.default_mic")
        self._default_system = t("settings.audio.default_system")
        try:
            self._mics, self._system_sources = list_devices()
        except Exception:
            self._mics, self._system_sources = [], []

        self.title(t("settings.title"))
        self.configure(bg=BG)
        self.transient(master)
        self.resizable(False, False)

        # ---- tab bar + stacked tab pages --------------------------------
        tabbar = tk.Frame(self, bg=BG)
        tabbar.pack(fill="x", padx=16, pady=(14, 0))
        content = tk.Frame(self, bg=BG)
        content.pack(fill="both", expand=True, padx=16, pady=(12, 0))
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)
        self._tab_btns, self._pages = {}, {}
        for key in _TABS:
            b = tk.Label(tabbar, text=t(f"settings.tab.{key}"), font=(SANS, 10, "bold"),
                         bg=BG, fg=TEXT_DIM, cursor="hand2", padx=14, pady=7)
            b.pack(side="left", padx=(0, 2))
            b.bind("<Button-1>", lambda _e, k=key: self._show_tab(k))
            self._tab_btns[key] = b
            page = tk.Frame(content, bg=BG)
            page.grid(row=0, column=0, sticky="nsew")
            page.columnconfigure(0, minsize=150)
            page.columnconfigure(1, weight=1, minsize=330)
            self._pages[key] = page

        self._build_transcription(self._pages["transcription"])
        self._build_audio(self._pages["audio"])
        self._build_summaries(self._pages["summaries"])
        self._build_behavior(self._pages["behavior"])
        self._show_tab("transcription")

        # ---- bottom bar -------------------------------------------------
        tk.Label(self, textvariable=self._error, bg=BG, fg=RED, font=(SANS, 9),
                 wraplength=460, justify="left", anchor="w").pack(fill="x", padx=18, pady=(8, 0))
        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=16, pady=(6, 14))
        self._button(bar, t("settings.edit_json"), self._edit_json, "ghost").pack(side="left")
        self._button(bar, t("common.save"), self.save, "primary").pack(side="right")
        self._button(bar, t("common.cancel"), self.destroy, "surface").pack(side="right",
                                                                            padx=(0, 8))
        self.bind("<Escape>", lambda _e: self.destroy())

        self.update_idletasks()
        osutil.set_dark_titlebar(self)
        self.geometry(f"+{master.winfo_rootx() + 30}+{master.winfo_rooty() + 24}")
        self.grab_set()

    # ---- tab switching --------------------------------------------------
    def _show_tab(self, key):
        for k, b in self._tab_btns.items():
            on = k == key
            b.config(fg=ACCENT if on else TEXT_DIM, bg=SURFACE if on else BG)
        self._pages[key].tkraise()

    # ---- styled widgets (each created as a child of `parent`) -----------
    def _button(self, parent, text, command, kind="surface"):
        bg, fg, weight = {"primary": (GREEN, BG, "bold"),
                          "surface": (SURFACE, TEXT_MAIN, "normal"),
                          "ghost": (BG, TEXT_SUB, "normal")}[kind]
        active = {"primary": "#b8f0b2", "surface": SURFACE_HI, "ghost": BG}[kind]
        return tk.Button(parent, text=text, command=command, bg=bg, fg=fg, activebackground=active,
                         activeforeground=fg, relief="flat", bd=0, cursor="hand2", padx=18, pady=8,
                         font=(SANS, 10, weight))

    def _focusable(self, box, entry):
        """Give a field an accent border while its entry has keyboard focus."""
        entry.bind("<FocusIn>", lambda _e: box.config(highlightbackground=ACCENT,
                                                       highlightcolor=ACCENT))
        entry.bind("<FocusOut>", lambda _e: box.config(highlightbackground=SURFACE,
                                                       highlightcolor=SURFACE))

    def _field_box(self, parent):
        return tk.Frame(parent, bg=SURFACE, highlightthickness=1, highlightbackground=SURFACE,
                        highlightcolor=SURFACE)

    def _entry_var(self, parent, var, show="", width=28):
        box = self._field_box(parent)
        e = tk.Entry(box, textvariable=var, show=show, bg=SURFACE, fg=TEXT_MAIN,
                     insertbackground=TEXT_MAIN, relief="flat", bd=0, font=(SANS, 10), width=width)
        e.pack(fill="x", expand=True, ipady=5, padx=9)
        self._focusable(box, e)
        return box

    def _entry(self, parent, key, show="", width=28):
        var = tk.StringVar(value=str(self._s[key]))
        return var, self._entry_var(parent, var, show, width)

    def _combo(self, parent, var, values, editable=True, command=None):
        box = self._field_box(parent)
        e = tk.Entry(box, textvariable=var, bg=SURFACE, fg=TEXT_MAIN, insertbackground=TEXT_MAIN,
                     relief="flat", bd=0, font=(SANS, 10), readonlybackground=SURFACE,
                     state="normal" if editable else "readonly")
        e.pack(side="left", fill="x", expand=True, ipady=5, padx=(9, 0))
        self._focusable(box, e)
        mb = tk.Menubutton(box, text="▾", bg=SURFACE, fg=TEXT_SUB, activebackground=SURFACE,
                           activeforeground=ACCENT, relief="flat", bd=0, cursor="hand2",
                           font=(SANS, 10), padx=10)
        menu = tk.Menu(mb, tearoff=0, bg=SURFACE_HI, fg=TEXT_MAIN, activebackground=ACCENT,
                       activeforeground=BG, bd=0, font=(SANS, 10))

        def choose(value):
            var.set(value)
            if command:
                command()
        for value in values:
            menu.add_command(label=str(value), command=lambda v=value: choose(v))
        mb["menu"] = menu
        mb.pack(side="right", fill="y")
        if not editable:   # a read-only field opens the menu when clicked anywhere
            e.config(cursor="hand2")
            e.bind("<Button-1>", lambda _e: menu.tk_popup(
                box.winfo_rootx(), box.winfo_rooty() + box.winfo_height()))
        return box

    def _stepper(self, parent, var, lo, hi, step):
        box = tk.Frame(parent, bg=BG)

        def bump(delta):
            try:
                value = int(float(var.get()))
            except ValueError:
                value = lo
            var.set(str(max(lo, min(hi, value + delta))))

        def mini(text, command):
            return tk.Button(box, text=text, command=command, bg=SURFACE, fg=TEXT_MAIN,
                             activebackground=SURFACE_HI, activeforeground=TEXT_MAIN, relief="flat",
                             bd=0, cursor="hand2", font=(SANS, 12), width=2)
        mini("−", lambda: bump(-step)).pack(side="left")
        field = self._field_box(box)
        field.pack(side="left", padx=6)
        e = tk.Entry(field, textvariable=var, bg=SURFACE, fg=TEXT_MAIN, insertbackground=TEXT_MAIN,
                     relief="flat", bd=0, font=(SANS, 10), width=6, justify="center")
        e.pack(ipady=5, padx=6)
        self._focusable(field, e)
        mini("+", lambda: bump(step)).pack(side="left")
        return box

    # ---- layout ---------------------------------------------------------
    def _row(self, frame, label, widget, hint=""):
        row = frame.grid_size()[1]
        tk.Label(frame, text=label, bg=BG, fg=TEXT_SUB, font=(SANS, 10), anchor="nw").grid(
            row=row, column=0, sticky="nw", pady=(11, 0), padx=(0, 14))
        widget.grid(row=row, column=1, sticky="ew", pady=(11, 0))
        if hint:
            tk.Label(frame, text=hint, bg=BG, fg=TEXT_DIM, font=(SANS, 8), anchor="w",
                     justify="left", wraplength=330).grid(row=row + 1, column=1, sticky="w",
                                                          pady=(3, 0))

    def _sub(self, frame, var):
        """A small status line under the control in column 1 (driven by a StringVar)."""
        tk.Label(frame, textvariable=var, bg=BG, fg=TEXT_DIM, font=(SANS, 8), anchor="w",
                 justify="left", wraplength=330).grid(row=frame.grid_size()[1], column=1,
                                                      sticky="w", pady=(3, 0))

    def _check(self, frame, text, key, hint="", enabled=True):
        var = tk.BooleanVar(value=self._s[key])
        row = frame.grid_size()[1]
        holder = tk.Frame(frame, bg=BG, cursor="hand2" if enabled else "arrow")
        holder.grid(row=row, column=0, columnspan=2, sticky="w", pady=(13, 0))
        box = tk.Label(holder, width=2, bg=ACCENT if var.get() else SURFACE, fg=BG,
                       text="✓" if var.get() else "", font=(SANS, 9, "bold"))
        box.pack(side="left", ipady=1)
        tk.Label(holder, text=text, bg=BG, fg=TEXT_MAIN if enabled else TEXT_DIM,
                 font=(SANS, 10), padx=8).pack(side="left")

        def toggle(_e=None):
            var.set(not var.get())
            box.config(bg=ACCENT if var.get() else SURFACE, text="✓" if var.get() else "")
        if enabled:
            for w in holder.winfo_children() + [holder]:
                w.bind("<Button-1>", toggle)
        if hint:
            tk.Label(frame, text=hint, bg=BG, fg=TEXT_DIM, font=(SANS, 8), anchor="w",
                     justify="left", wraplength=430).grid(row=row + 1, column=0, columnspan=2,
                                                          sticky="w", padx=(28, 0), pady=(3, 0))
        return var

    def _separator(self, frame):
        tk.Frame(frame, bg=SURFACE, height=1).grid(row=frame.grid_size()[1], column=0,
                                                   columnspan=2, sticky="ew", pady=(14, 2))

    # ---- tabs -----------------------------------------------------------
    def _build_transcription(self, f):
        self.v_model = tk.StringVar(value=self._s["whisper_model"])
        self._row(f, t("settings.transcription.model"),
                  self._combo(f, self.v_model, config.WHISPER_MODELS),
                  t("settings.transcription.model.hint"))
        self.v_language = tk.StringVar(value=self._s["language"] or self._auto)
        self._row(f, t("settings.transcription.language"),
                  self._combo(f, self.v_language, [self._auto, *_WHISPER_LANGS]),
                  t("settings.transcription.language.hint"))
        self.v_device = tk.StringVar(value=self._s["device"])
        self._row(f, t("settings.transcription.device"),
                  self._combo(f, self.v_device, config.DEVICES, editable=False),
                  t("settings.transcription.device.hint"))
        self.v_labels = self._check(f, t("settings.transcription.labels"), "speaker_labels")
        self.v_mic_label, e1 = self._entry(f, "mic_label")
        self._row(f, t("settings.transcription.mic_label"), e1)
        self.v_others_label, e2 = self._entry(f, "others_label")
        self._row(f, t("settings.transcription.others_label"), e2)
        self.v_srt = self._check(f, t("settings.transcription.srt"), "write_srt")

        self._separator(f)
        self.v_live_mode = tk.StringVar(value=self._s["live_mode"])
        self._row(f, t("settings.transcription.live_mode"),
                  self._combo(f, self.v_live_mode, config.LIVE_MODES, editable=False),
                  t("settings.transcription.live_mode.hint"))
        self.v_live_model = tk.StringVar(value=self._s["live_model"])
        self._row(f, t("settings.transcription.live_model"),
                  self._combo(f, self.v_live_model, config.WHISPER_MODELS),
                  t("settings.transcription.live_model.hint"))
        self.v_live_interval = tk.StringVar(value=str(self._s["live_interval"]))
        self._row(f, t("settings.transcription.live_interval"),
                  self._stepper(f, self.v_live_interval, 2, 120, 5),
                  t("settings.transcription.live_interval.hint"))
        self.v_live_notes_interval = tk.StringVar(value=str(self._s["live_notes_interval"]))
        self._row(f, t("settings.transcription.live_notes_interval"),
                  self._stepper(f, self.v_live_notes_interval, 60, 3600, 60),
                  t("settings.transcription.live_notes_interval.hint"))
        self.v_live_catchup = self._check(
            f, t("settings.transcription.live_catchup"), "live_catchup",
            t("settings.transcription.live_catchup.hint"))

    def _build_audio(self, f):
        self.v_mic = tk.StringVar(value=self._s["microphone"] or self._default_mic)
        self._row(f, t("settings.audio.mic"),
                  self._combo(f, self.v_mic, [self._default_mic] + self._mics),
                  t("settings.audio.mic.hint"))
        self.v_system = tk.StringVar(value=self._s["system_audio"] or self._default_system)
        hint = (t("settings.audio.system.hint.macos") if osutil.MACOS
                else t("settings.audio.system.hint"))
        self._row(f, t("settings.audio.system"),
                  self._combo(f, self.v_system, [self._default_system] + self._system_sources),
                  hint)
        self.v_timeout = tk.StringVar(value=str(self._s["silence_timeout"]))
        self._row(f, t("settings.audio.timeout"), self._stepper(f, self.v_timeout, 0, 3600, 30),
                  t("settings.audio.timeout.hint"))
        self.v_threshold, e = self._entry(f, "silence_threshold", width=10)
        self._row(f, t("settings.audio.threshold"), e, t("settings.audio.threshold.hint"))
        self.v_keep = self._check(f, t("settings.audio.keep"), "keep_audio")

        self.v_output = tk.StringVar(value=self._s["output_dir"])
        box = tk.Frame(f, bg=BG)
        self._entry_var(box, self.v_output, width=20).pack(side="left", fill="x", expand=True)
        self._button(box, t("common.browse"), self._browse, "surface").pack(side="left",
                                                                            padx=(6, 0))
        self._row(f, t("settings.audio.save_to"), box,
                  t("settings.audio.save_to.hint", path=config.default_output_dir()))

    def _build_summaries(self, f):
        self.v_backend = tk.StringVar(value=self._s["summary_backend"])
        self._row(f, t("settings.summaries.backend"),
                  self._combo(f, self.v_backend, config.SUMMARY_BACKENDS, editable=False,
                              command=self._update_backend_hints))
        self.v_status = tk.StringVar()
        self._sub(f, self.v_status)
        self.v_summary_model, e = self._entry(f, "summary_model")
        self._row(f, t("settings.summaries.model"), e)
        self.v_model_hint = tk.StringVar()
        self._sub(f, self.v_model_hint)
        self.v_api_key, e = self._entry(f, "anthropic_api_key", show="•")
        self._row(f, t("settings.summaries.anthropic_key"), e,
                  t("settings.summaries.anthropic_key.hint"))
        self.v_openai_key, e = self._entry(f, "openai_api_key", show="•")
        self._row(f, t("settings.summaries.openai_key"), e, t("settings.summaries.openai_key.hint"))
        self.v_ollama, e = self._entry(f, "ollama_url")
        self._row(f, t("settings.summaries.ollama_url"), e)
        prompt_box = self._field_box(f)
        self.t_prompt = tk.Text(prompt_box, width=36, height=4, wrap="word", font=(SANS, 10),
                                bg=SURFACE, fg=TEXT_MAIN, insertbackground=TEXT_MAIN,
                                relief="flat", bd=0, padx=8, pady=6, highlightthickness=0)
        self.t_prompt.insert("1.0", self._s["summary_prompt"])
        self.t_prompt.pack(fill="both", expand=True)
        self._focusable(prompt_box, self.t_prompt)
        self._row(f, t("settings.summaries.prompt"), prompt_box,
                  t("settings.summaries.prompt.hint"))
        self._update_backend_hints()

    def _build_behavior(self, f):
        self.v_hotkey, e = self._entry(f, "hotkey")
        self._row(f, t("settings.behavior.hotkey"), e,
                  t("settings.behavior.hotkey.hint.macos") if osutil.MACOS
                  else t("settings.behavior.hotkey.hint"))
        can_watch = meetings.supported()
        self.v_prompt = self._check(
            f, t("settings.behavior.meeting_prompt"), "meeting_prompt",
            t("settings.behavior.meeting_prompt.hint") if can_watch
            else t("settings.behavior.meeting_prompt.hint.macos"), enabled=can_watch)
        self.v_apps = tk.StringVar(value=", ".join(self._s["meeting_apps"]))
        self._row(f, t("settings.behavior.meeting_apps"), self._entry_var(f, self.v_apps),
                  t("settings.behavior.meeting_apps.hint"))
        can_tray = tray.supported()
        self.v_tray = self._check(f, t("settings.behavior.tray"), "tray_icon",
                                  "" if can_tray else t("settings.behavior.tray.hint.unsupported"),
                                  enabled=can_tray)
        self.v_min_tray = self._check(f, t("settings.behavior.min_tray"), "minimize_to_tray",
                                      enabled=can_tray)

        self._separator(f)
        self._lang_by_name = {i18n.native_name(c): c for c in i18n.LANGUAGES}
        self.v_ui_language = tk.StringVar(value=i18n.native_name(self._s.get("ui_language", "en")))
        self._row(f, t("settings.behavior.ui_language"),
                  self._combo(f, self.v_ui_language, list(self._lang_by_name), editable=False),
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
