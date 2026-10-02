"""System-wide start/stop shortcut.

Windows uses RegisterHotKey (no dependencies); Linux (X11) uses the optional
`pynput` package. Not on macOS yet: pynput's keyboard listener calls input-source
APIs off the main thread there, which current macOS kills the app for.
"""

import ctypes
import re
import sys
import threading

_MODIFIERS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4,
              "win": 0x8, "cmd": 0x8, "super": 0x8}
_MOD_NOREPEAT = 0x4000
_WM_HOTKEY = 0x0312
_WM_QUIT = 0x0012
_ERROR_HOTKEY_ALREADY_REGISTERED = 1409


def _parts(spec: str) -> list[str]:
    return [p.strip().lower() for p in spec.split("+") if p.strip()]


def parse_hotkey(spec: str) -> tuple[int, int]:
    """'ctrl+alt+r' -> (modifier flags, virtual-key code). Raises ValueError."""
    parts = _parts(spec)
    if len(parts) < 2:
        raise ValueError("use at least one modifier plus a key, e.g. ctrl+alt+r")
    *mods, key = parts
    flags = 0
    for mod in mods:
        if mod not in _MODIFIERS:
            raise ValueError(f"unknown modifier '{mod}' (use ctrl, alt, shift, win/cmd)")
        flags |= _MODIFIERS[mod]
    if len(key) == 1 and key.isascii() and key.isalnum():
        vk = ord(key.upper())
    elif re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", key):
        vk = 0x70 + int(key[1:]) - 1
    elif key == "space":
        vk = 0x20
    else:
        raise ValueError(f"unknown key '{key}' (use a letter, digit, F1-F24 or space)")
    return flags, vk


def to_pynput(spec: str) -> str:
    """'ctrl+alt+r' -> '<ctrl>+<alt>+r' (pynput's GlobalHotKeys format)."""
    parse_hotkey(spec)   # validate
    *mods, key = _parts(spec)
    names = {"control": "ctrl", "win": "cmd", "super": "cmd"}
    out = [f"<{names.get(m, m)}>" for m in mods]
    out.append(key if len(key) == 1 else f"<{key}>")
    return "+".join(out)


class GlobalHotkey:
    """Calls `callback` (on a background thread) whenever the shortcut is pressed."""

    def __init__(self, spec: str, callback):
        self.spec = spec
        self._flags, self._vk = parse_hotkey(spec)
        self._callback = callback
        self._thread_id = 0
        self._ready = threading.Event()
        self._listener = None
        self.error = ""

    def start(self) -> bool:
        if sys.platform == "darwin":
            self.error = "global hotkeys aren't supported on macOS yet"
            return False
        if sys.platform != "win32":
            return self._start_pynput()
        threading.Thread(target=self._run, daemon=True).start()
        self._ready.wait(timeout=2)
        return not self.error

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:   # the listener thread may already have died
                pass
            self._listener = None
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, _WM_QUIT, 0, 0)
            self._thread_id = 0

    def _start_pynput(self) -> bool:
        try:
            from pynput import keyboard
        except Exception as exc:   # not installed, or no X display
            self.error = f"needs the 'pynput' package ({exc})"
            return False
        try:
            listener = keyboard.GlobalHotKeys({to_pynput(self.spec): self._callback})
            listener.start()
        except Exception as exc:
            self.error = str(exc)
            return False
        # pynput's wait() never returns if its thread dies first (e.g. no X11 RECORD
        # extension), so wait on a helper thread with a timeout.
        waiter = threading.Thread(target=listener.wait, daemon=True)
        waiter.start()
        waiter.join(timeout=3)
        if waiter.is_alive() or not listener.is_alive():
            self.error = "the keyboard listener could not start (Linux needs an X11 session)"
            self._listener = listener
            self.stop()
            return False
        self._listener = listener
        return True

    def _run(self) -> None:
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, 1, self._flags | _MOD_NOREPEAT, self._vk):
            code = ctypes.get_last_error()
            self.error = ("already used by another program"
                          if code == _ERROR_HOTKEY_ALREADY_REGISTERED else f"Windows error {code}")
            self._thread_id = 0
            self._ready.set()
            return
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == _WM_HOTKEY:
                    self._callback()
        finally:
            user32.UnregisterHotKey(None, 1)
