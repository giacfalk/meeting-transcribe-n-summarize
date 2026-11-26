"""First-run setup: a small, skippable welcome dialog shown the first time the app
starts (when there is no settings.json yet).

It covers only the two choices that trip new users up (the transcription model and
the summary backend) and writes them to settings.json. Everything else keeps its
sensible default and can be changed later in Settings. Skipping leaves the defaults
in place, so the "just press Start" experience is preserved.

A language picker in the corner re-renders the wizard live and stores the UI
language; the rest of the app switches to it on the next launch.
"""

import threading
import tkinter as tk
import urllib.request

from . import config, i18n, osutil, summarize
from .i18n import t

SANS = osutil.FONTS[0]
BG, SURFACE, SURFACE_HI = "#1e1e2e", "#313244", "#45475a"
ACCENT, GREEN = "#89b4fa", "#a6e3a1"
TEXT_MAIN, TEXT_SUB, TEXT_DIM = "#cdd6f4", "#a6adc8", "#7f849c"

# (settings value, name key, description key)
MODELS = [
    ("base", "welcome.model.base.name", "welcome.model.base.desc"),
    ("small", "welcome.model.small.name", "welcome.model.small.desc"),
    ("large-v3-turbo", "welcome.model.turbo.name", "welcome.model.turbo.desc"),
]
BACKENDS = [
    ("claude-cli", "welcome.backend.claude.name", "welcome.backend.claude.desc"),
    ("anthropic-api", "welcome.backend.anthropic.name", "welcome.backend.anthropic.desc"),
    ("openai-api", "welcome.backend.openai.name", "welcome.backend.openai.desc"),
    ("ollama", "welcome.backend.ollama.name", "welcome.backend.ollama.desc"),
    ("none", "welcome.backend.none.name", "welcome.backend.none.desc"),
]


def settings_changes(model: str, backend: str, api_key: str = "", ollama_model: str = "",
                     openai_key: str = "", ui_language: str = "") -> dict:
    """The settings.json keys the wizard writes for the chosen options."""
    changes = {"whisper_model": model, "summary_backend": backend}
    if ui_language:
        changes["ui_language"] = ui_language
    if backend == "anthropic-api" and api_key.strip():
        changes["anthropic_api_key"] = api_key.strip()
    elif backend == "openai-api" and openai_key.strip():
        changes["openai_api_key"] = openai_key.strip()
    elif backend == "ollama" and ollama_model.strip():
        changes["summary_model"] = ollama_model.strip()
    return changes


def _ollama_running(url: str, timeout: float = 0.8) -> bool:
    try:
        urllib.request.urlopen(f"{url.rstrip('/')}/api/tags", timeout=timeout)
        return True
    except Exception:
        return False


class WelcomeDialog(tk.Toplevel):
    def __init__(self, master, settings: dict, on_done):
        super().__init__(master)
        self._on_done = on_done
        self._s = settings
        self._claude_ok = bool(summarize.find_claude_cli())
        self._ollama_state = "checking"       # checking | up | down
        self._dots: list = []

        valid = {m[0] for m in MODELS}
        start_model = settings.get("whisper_model", "base")
        self._model = tk.StringVar(value=start_model if start_model in valid else "base")
        self._backend = tk.StringVar(value=settings.get("summary_backend", "claude-cli"))
        self._api_key = tk.StringVar(value=settings.get("anthropic_api_key", ""))
        self._openai_key = tk.StringVar(value=settings.get("openai_api_key", ""))
        self._ollama_model = tk.StringVar(value=settings.get("summary_model", "") or "llama3.1")
        lang = settings.get("ui_language", "en")
        self._lang = lang if lang in i18n.LANGUAGES else "en"
        i18n.set_language(self._lang)

        self.title(t("welcome.title"))
        self.configure(bg=BG)
        self.transient(master)
        self.resizable(False, False)

        self._wrap = tk.Frame(self, bg=BG, padx=26, pady=22)
        self._wrap.pack(fill="both", expand=True)
        self._build_body()

        # Add the traces once, after the first build, so they don't stack on rebuilds.
        self._model.trace_add("write", lambda *_: self._sync())
        self._backend.trace_add("write", lambda *_: (self._sync(), self._refresh()))

        self.protocol("WM_DELETE_WINDOW", self._skip)
        self.bind("<Escape>", lambda _e: self._skip())
        self.update_idletasks()
        osutil.set_dark_titlebar(self)
        self.geometry(f"+{master.winfo_rootx() + 40}+{master.winfo_rooty() + 24}")
        self.grab_set()
        threading.Thread(target=self._check_ollama, daemon=True).start()

    # ---- layout ---------------------------------------------------------
    def _build_body(self):
        """Build (or rebuild, after a language change) everything inside the wrap."""
        for child in self._wrap.winfo_children():
            child.destroy()
        self._dots = []
        wrap = self._wrap

        header = tk.Frame(wrap, bg=BG)
        header.pack(fill="x")
        tk.Label(header, text=t("welcome.title"), font=(SANS, 15, "bold"),
                 bg=BG, fg=TEXT_MAIN).pack(side="left", anchor="w")
        self._language_control(header).pack(side="right", anchor="ne")

        tk.Label(wrap, text=t("welcome.subtitle"), font=(SANS, 9), bg=BG, fg=TEXT_SUB,
                 wraplength=400, justify="left").pack(anchor="w", pady=(2, 2))

        self._section(wrap, t("welcome.section.transcription"))
        for value, name_key, desc_key in MODELS:
            self._option(wrap, self._model, value, t(name_key), t(desc_key))

        self._section(wrap, t("welcome.section.summaries"))
        for value, name_key, desc_key in BACKENDS:
            good = value == "claude-cli" and self._claude_ok
            badge = ""
            if value == "claude-cli":
                badge = t("welcome.badge.detected") if good else t("welcome.badge.notfound")
            self._option(wrap, self._backend, value, t(name_key), t(desc_key), badge, good)

        self._extra = tk.Frame(wrap, bg=BG)
        self._extra.pack(fill="x", pady=(8, 0))
        self._note = tk.Label(wrap, text="", font=(SANS, 8), bg=BG, fg=TEXT_DIM,
                              wraplength=400, justify="left")
        self._note.pack(anchor="w", pady=(8, 0))

        btns = tk.Frame(wrap, bg=BG)
        btns.pack(fill="x", pady=(18, 0))
        self._button(btns, t("welcome.btn.start"), self._finish, GREEN, BG,
                     bold=True).pack(side="right")
        self._button(btns, t("welcome.btn.more"), self._more, SURFACE,
                     TEXT_MAIN).pack(side="right", padx=8)
        self._button(btns, t("welcome.btn.skip"), self._skip, BG, TEXT_SUB).pack(side="left")

        self._sync()
        self._refresh()

    # ---- styled widgets -------------------------------------------------
    def _language_control(self, parent):
        mb = tk.Menubutton(parent, text=f"{i18n.native_name(self._lang)}  ▾",
                           font=(SANS, 9), bg=SURFACE, fg=TEXT_SUB,
                           activebackground=SURFACE_HI, activeforeground=TEXT_MAIN,
                           relief="flat", bd=0, cursor="hand2", padx=10, pady=4)
        menu = tk.Menu(mb, tearoff=0, bg=SURFACE, fg=TEXT_MAIN,
                       activebackground=ACCENT, activeforeground=BG, bd=0)
        for code, (native, _english) in i18n.LANGUAGES.items():
            menu.add_command(label=native, command=lambda c=code: self._set_language(c))
        mb["menu"] = menu
        return mb

    def _section(self, parent, text):
        tk.Label(parent, text=text, font=(SANS, 8, "bold"), bg=BG, fg=TEXT_DIM).pack(
            anchor="w", pady=(15, 5))

    def _option(self, parent, var, value, title, desc, badge="", badge_good=False):
        row = tk.Frame(parent, bg=BG, cursor="hand2")
        row.pack(fill="x", pady=1)
        dot = tk.Label(row, text="○", font=(SANS, 12), bg=BG, fg=TEXT_DIM, width=2)
        dot.pack(side="left", anchor="n")
        body = tk.Frame(row, bg=BG)
        body.pack(side="left", fill="x", expand=True)
        head = tk.Frame(body, bg=BG)
        head.pack(anchor="w", fill="x")
        tk.Label(head, text=title, font=(SANS, 10, "bold"), bg=BG, fg=TEXT_MAIN).pack(side="left")
        if badge:
            tk.Label(head, text="  " + badge, font=(SANS, 8), bg=BG,
                     fg=GREEN if badge_good else TEXT_DIM).pack(side="left")
        tk.Label(body, text=desc, font=(SANS, 8), bg=BG, fg=TEXT_DIM,
                 wraplength=340, justify="left").pack(anchor="w")

        def choose(_e=None):
            var.set(value)
        for widget in (row, dot, body, head):
            widget.bind("<Button-1>", choose)
        for child in head.winfo_children() + body.winfo_children():
            child.bind("<Button-1>", choose)
        self._dots.append((var, value, dot))

    def _sync(self):
        for var, value, dot in self._dots:
            on = var.get() == value
            dot.config(text="●" if on else "○", fg=ACCENT if on else TEXT_DIM)

    def _button(self, parent, text, command, bg, fg, bold=False):
        return tk.Button(parent, text=text, command=command,
                         font=(SANS, 10, "bold" if bold else "normal"), bg=bg, fg=fg,
                         activebackground=bg, activeforeground=fg, relief="flat", bd=0,
                         cursor="hand2", padx=14, pady=7)

    def _entry(self, parent, var, show=""):
        return tk.Entry(parent, textvariable=var, show=show, bg=SURFACE, fg=TEXT_MAIN,
                        insertbackground=TEXT_MAIN, relief="flat", font=(SANS, 10), width=42)

    # ---- behaviour ------------------------------------------------------
    def _set_language(self, code):
        if code == self._lang:
            return
        self._lang = code
        i18n.set_language(code)
        self.title(t("welcome.title"))
        self._build_body()

    def _ollama_note_text(self):
        return t({"up": "welcome.note.ollama.up",
                  "down": "welcome.note.ollama.down"}.get(self._ollama_state,
                                                          "welcome.note.ollama.checking"))

    def _refresh(self):
        for child in self._extra.winfo_children():
            child.destroy()
        be = self._backend.get()
        if be == "anthropic-api":
            tk.Label(self._extra, text=t("welcome.field.anthropic_key"), font=(SANS, 8),
                     bg=BG, fg=TEXT_SUB).pack(anchor="w")
            self._entry(self._extra, self._api_key, show="•").pack(anchor="w", fill="x",
                                                                   pady=(2, 0))
            self._note.config(text=t("welcome.note.anthropic"))
        elif be == "openai-api":
            tk.Label(self._extra, text=t("welcome.field.openai_key"), font=(SANS, 8),
                     bg=BG, fg=TEXT_SUB).pack(anchor="w")
            self._entry(self._extra, self._openai_key, show="•").pack(anchor="w", fill="x",
                                                                      pady=(2, 0))
            self._note.config(text=t("welcome.note.openai"))
        elif be == "ollama":
            tk.Label(self._extra, text=t("welcome.field.ollama_model"), font=(SANS, 8),
                     bg=BG, fg=TEXT_SUB).pack(anchor="w")
            self._entry(self._extra, self._ollama_model).pack(anchor="w", fill="x", pady=(2, 0))
            self._note.config(text=self._ollama_note_text())
        elif be == "claude-cli":
            self._note.config(text=t("welcome.note.claude.ready") if self._claude_ok
                              else t("welcome.note.claude.missing"))
        else:
            self._note.config(text=t("welcome.note.none"))

    def _check_ollama(self):
        up = _ollama_running(self._s.get("ollama_url", "http://localhost:11434"))
        self._ollama_state = "up" if up else "down"
        try:
            self.after(0, lambda: self._backend.get() == "ollama"
                       and self._note.config(text=self._ollama_note_text()))
        except Exception:
            pass

    def _close(self, open_settings: bool):
        cb = self._on_done
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
        if cb:
            cb(open_settings)

    def _save(self):
        config.update_settings(settings_changes(
            self._model.get(), self._backend.get(), self._api_key.get(),
            self._ollama_model.get(), self._openai_key.get(), self._lang))

    def _finish(self):
        try:
            self._save()
        except OSError:
            pass
        self._close(False)

    def _more(self):
        try:
            self._save()
        except OSError:
            pass
        self._close(True)

    def _skip(self):
        self._close(False)
