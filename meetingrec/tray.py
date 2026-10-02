"""Notification-area (tray) icon with a small menu, via pystray.

Windows and Linux only: on macOS pystray must own the main thread, which Tk
already does there (the Dock icon covers the same need).
"""

import threading
from pathlib import Path

from .osutil import MACOS


def supported() -> bool:
    if MACOS:
        return False
    try:
        import pystray  # noqa: F401
        return True
    except Exception:   # not installed, or no display/tray backend
        return False


def recording_badge(image):
    """The app icon with a red "recording" dot in a white ring, bottom-right."""
    from PIL import ImageDraw

    img = image.convert("RGBA").copy()
    s = img.size[0]
    r = s * 0.22
    cx = cy = s - r - s * 0.02
    d = ImageDraw.Draw(img)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 255))
    d.ellipse([cx - r * 0.72, cy - r * 0.72, cx + r * 0.72, cy + r * 0.72],
              fill=(255, 23, 68, 255))
    return img


class TrayIcon:
    """Menu callbacks run on the tray's own thread: hand them to Tk with `after`."""

    def __init__(self, logo: Path, *, on_show, on_toggle, on_process_pending,
                 on_open_folder, on_quit, is_recording):
        import pystray
        from PIL import Image

        self._idle = Image.open(logo).convert("RGBA")
        self._recording = recording_badge(self._idle)
        self._is_recording = is_recording
        menu = pystray.Menu(
            pystray.MenuItem("Show Meeting Recorder", lambda: on_show(), default=True),
            pystray.MenuItem(
                lambda _item: "Stop recording" if is_recording() else "Start recording",
                lambda: on_toggle()),
            pystray.MenuItem("Process pending", lambda: on_process_pending()),
            pystray.MenuItem("Open folder", lambda: on_open_folder()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda: on_quit()),
        )
        self._icon = pystray.Icon("MeetingRecorder", self._idle, "Meeting Recorder", menu)
        self._thread: threading.Thread | None = None
        self.error = ""

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self._icon.visible)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self._icon.run()
        except Exception as exc:   # e.g. no system tray on this desktop
            self.error = str(exc) or type(exc).__name__

    def update(self, status: str = "") -> None:
        """Switch icon/tooltip between idle and recording (call after a state change)."""
        recording = self._is_recording()
        self._icon.icon = self._recording if recording else self._idle
        self._icon.title = f"Meeting Recorder - {status}" if status else "Meeting Recorder"
        self._icon.update_menu()

    def notify(self, message: str, title: str = "Meeting Recorder") -> None:
        if self._icon.HAS_NOTIFICATION and self.running:
            try:
                self._icon.notify(message, title)
            except Exception:
                pass

    def stop(self) -> None:
        try:
            self._icon.stop()
        except Exception:
            pass
