"""First-run setup: a small, skippable welcome dialog shown the first time the app
starts (when there is no settings.json yet).

It covers only the two choices that trip new users up (the transcription model and
the summary backend) and writes them to settings.json. Everything else keeps its
sensible default and can be changed later in Settings. Skipping leaves the defaults
in place, so the "just press Start" experience is preserved.
"""

import threading
import tkinter as tk
import urllib.request

from . import config, osutil, summarize

SANS = osutil.FONTS[0]
BG, SURFACE, SURFACE_HI = "#1e1e2e", "#313244", "#45475a"
ACCENT, GREEN = "#89b4fa", "#a6e3a1"
TEXT_MAIN, TEXT_SUB, TEXT_DIM = "#cdd6f4", "#a6adc8", "#7f849c"

# (value, short name, one-line description)
MODELS = [
    ("base", "Fast", "Quick; fine for most meetings (recommended)"),
    ("small", "Balanced", "A little slower, a bit more accurate"),
    ("large-v3-turbo", "Most accurate", "Best quality, slower on a CPU"),
]
BACKENDS = [
    ("claude-cli", "Claude Code", "Uses your existing Claude login; no API key needed."),
    ("anthropic-api", "Anthropic API", "Uses an Anthropic API key."),
    ("openai-api", "ChatGPT (OpenAI)", "Uses an OpenAI API key."),
    ("ollama", "Local model (Ollama)", "Runs fully offline on your machine."),
    ("none", "None", "Just transcripts, no summaries."),
]


def settings_changes(model: str, backend: str, api_key: str = "", ollama_model: str = "",
                     openai_key: str = "") -> dict:
    """The settings.json keys the wizard writes for the chosen options."""
    changes = {"whisper_model": model, "summary_backend": backend}
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
        self.title("Welcome to Meeting Recorder")
        self.configure(bg=BG)
        self.transient(master)
        self.resizable(False, False)
        self._on_done = on_done
        self._s = settings
        self._claude_ok = bool(summarize.find_claude_cli())
        self._ollama_note = "Checking for Ollama..."
        self._dots: list = []

        valid = {m[0] for m in MODELS}
        start_model = settings.get("whisper_model", "base")
        self._model = tk.StringVar(value=start_model if start_model in valid else "base")
        self._backend = tk.StringVar(value=settings.get("summary_backend", "claude-cli"))
        self._api_key = tk.StringVar(value=settings.get("anthropic_api_key", ""))
        self._openai_key = tk.StringVar(value=settings.get("openai_api_key", ""))
        self._ollama_model = tk.StringVar(value=settings.get("summary_model", "") or "llama3.1")

        wrap = tk.Frame(self, bg=BG, padx=26, pady=22)
        wrap.pack(fill="both", expand=True)

        tk.Label(wrap, text="Welcome to Meeting Recorder", font=(SANS, 15, "bold"),
                 bg=BG, fg=TEXT_MAIN).pack(anchor="w")
        tk.Label(wrap, text="Two quick choices to get you going. You can change everything "
                 "later in Settings.", font=(SANS, 9), bg=BG, fg=TEXT_SUB, wraplength=400,
                 justify="left").pack(anchor="w", pady=(2, 2))

        self._section(wrap, "TRANSCRIPTION")
        for value, name, desc in MODELS:
            self._option(wrap, self._model, value, name, desc)

        self._section(wrap, "SUMMARIES")
        for value, name, desc in BACKENDS:
            badge = ""
            if value == "claude-cli":
                badge = "detected" if self._claude_ok else "not found"
            self._option(wrap, self._backend, value, name, desc, badge)

        self._extra = tk.Frame(wrap, bg=BG)
        self._extra.pack(fill="x", pady=(8, 0))
        self._note = tk.Label(wrap, text="", font=(SANS, 8), bg=BG, fg=TEXT_DIM,
                              wraplength=400, justify="left")
        self._note.pack(anchor="w", pady=(8, 0))

        btns = tk.Frame(wrap, bg=BG)
        btns.pack(fill="x", pady=(18, 0))
        self._button(btns, "Get started", self._finish, GREEN, BG, bold=True).pack(side="right")
        more = self._button(btns, "More settings", self._more, SURFACE, TEXT_MAIN)
        more.pack(side="right", padx=8)
        self._button(btns, "Skip", self._skip, BG, TEXT_SUB).pack(side="left")

        self._model.trace_add("write", lambda *_: self._sync())
        self._backend.trace_add("write", lambda *_: (self._sync(), self._refresh()))
        self._sync()
        self._refresh()

        self.protocol("WM_DELETE_WINDOW", self._skip)
        self.bind("<Escape>", lambda _e: self._skip())
        self.update_idletasks()
        osutil.set_dark_titlebar(self)
        self.geometry(f"+{master.winfo_rootx() + 40}+{master.winfo_rooty() + 24}")
        self.grab_set()
        threading.Thread(target=self._check_ollama, daemon=True).start()

    # ---- styled widgets -------------------------------------------------
    def _section(self, parent, text):
        tk.Label(parent, text=text, font=(SANS, 8, "bold"), bg=BG, fg=TEXT_DIM).pack(
            anchor="w", pady=(15, 5))

    def _option(self, parent, var, value, title, desc, badge=""):
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
                     fg=GREEN if badge == "detected" else TEXT_DIM).pack(side="left")
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
    def _refresh(self):
        for child in self._extra.winfo_children():
            child.destroy()
        be = self._backend.get()
        if be == "anthropic-api":
            tk.Label(self._extra, text="Anthropic API key", font=(SANS, 8), bg=BG,
                     fg=TEXT_SUB).pack(anchor="w")
            self._entry(self._extra, self._api_key, show="•").pack(anchor="w", fill="x",
                                                                        pady=(2, 0))
            self._note.config(text="Enter a key, or leave it blank to use the "
                              "ANTHROPIC_API_KEY environment variable.")
        elif be == "openai-api":
            tk.Label(self._extra, text="OpenAI API key", font=(SANS, 8), bg=BG,
                     fg=TEXT_SUB).pack(anchor="w")
            self._entry(self._extra, self._openai_key, show="•").pack(anchor="w", fill="x",
                                                                      pady=(2, 0))
            self._note.config(text="Enter a key, or leave it blank to use the "
                              "OPENAI_API_KEY environment variable.")
        elif be == "ollama":
            tk.Label(self._extra, text="Ollama model", font=(SANS, 8), bg=BG,
                     fg=TEXT_SUB).pack(anchor="w")
            self._entry(self._extra, self._ollama_model).pack(anchor="w", fill="x", pady=(2, 0))
            self._note.config(text=self._ollama_note)
        elif be == "claude-cli":
            self._note.config(text="Ready. Summaries use your Claude login." if self._claude_ok
                              else "The Claude CLI ('claude') isn't installed. Install it or "
                              "pick another option; you'll still get transcripts either way.")
        else:
            self._note.config(text="No summaries will be written. You still get full transcripts.")

    def _check_ollama(self):
        up = _ollama_running(self._s.get("ollama_url", "http://localhost:11434"))
        self._ollama_note = ("Ollama is running. Summaries stay fully offline." if up else
                             "Ollama not detected. Install it from ollama.com, start it, then "
                             "pull a model (e.g. ollama pull llama3.1).")
        try:
            self.after(0, lambda: self._backend.get() == "ollama"
                       and self._note.config(text=self._ollama_note))
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
            self._ollama_model.get(), self._openai_key.get()))

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
