"""The real tray icon (Windows/Linux). Skips where the desktop has no tray."""

import threading
import time
from pathlib import Path

import pytest

from meetingrec import tray

LOGO = Path(__file__).parent.parent / "assets" / "logo.png"


@pytest.mark.skipif(not tray.supported(), reason="no tray support on this system")
def test_tray_icon_starts_switches_and_stops():
    recording = [False]
    icon = tray.TrayIcon(LOGO, on_show=lambda: None, on_toggle=lambda: None,
                         on_process_pending=lambda: None, on_open_folder=lambda: None,
                         on_quit=lambda: None, is_recording=lambda: recording[0])
    icon.start()
    deadline = time.time() + 5
    while time.time() < deadline and not (icon.running or icon.error):
        time.sleep(0.2)
    if not icon.running:
        stopper = threading.Thread(target=icon.stop, daemon=True)
        stopper.start()
        stopper.join(5)
        pytest.skip(f"no system tray available: {icon.error or 'icon never appeared'}")
    try:
        recording[0] = True
        icon.update("recording 00:05")
        assert icon._icon.icon is icon._recording
        assert icon._icon.title == "Meeting Recorder - recording 00:05"
        recording[0] = False
        icon.update()
        assert icon._icon.icon is icon._idle
    finally:
        icon.stop()
        icon._thread.join(5)
    assert not icon._thread.is_alive()
