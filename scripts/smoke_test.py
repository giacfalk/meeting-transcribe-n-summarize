#!/usr/bin/env python3
"""Start a built app with a throwaway profile and check that it starts properly.

    python scripts/smoke_test.py dist/MeetingRecorder/MeetingRecorder.exe   # Windows
    python scripts/smoke_test.py dist/MeetingRecorder/MeetingRecorder       # Linux (xvfb-run)
    python scripts/smoke_test.py dist/MeetingRecorder.app                   # macOS

"Properly" means the app wrote its start-up line to the log, transcription is
available, nothing was logged as an ERROR, and it is still running. A crash or
an error dialog would leave one of those missing.
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def log_path(home: Path) -> Path:
    if sys.platform == "win32":
        return home / "AppData" / "Roaming" / "MeetingRecorder" / "meetingrec.log"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "MeetingRecorder" / "meetingrec.log"
    return home / ".config" / "MeetingRecorder" / "meetingrec.log"


def main() -> int:
    target = Path(sys.argv[1]).resolve()
    exe = target / "Contents" / "MacOS" / target.stem if target.suffix == ".app" else target
    # PulseAudio clients find their auth cookie via HOME/XDG_CONFIG_HOME; keep the real one.
    pulse_cookie = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") \
        / "pulse" / "cookie"
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
                   APPDATA=str(home / "AppData" / "Roaming"), OneDrive=str(home / "OneDrive"))
        if pulse_cookie.exists():
            env.setdefault("PULSE_COOKIE", str(pulse_cookie))
        log = log_path(home)
        proc = subprocess.Popen([str(exe)], env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        text, deadline = "", time.time() + 120
        while time.time() < deadline and proc.poll() is None:
            text = log.read_text(encoding="utf-8") if log.exists() else ""
            if "Started on" in text:
                break
            time.sleep(1)
        time.sleep(5)   # give it time to fail after start-up, too
        text = log.read_text(encoding="utf-8") if log.exists() else ""
        alive = proc.poll() is None
        if alive:
            proc.kill()
        output = proc.communicate(timeout=30)[0].decode("utf-8", errors="replace")

    problems = []
    if "Started on" not in text:
        problems.append("no start-up line in the log")
    if "transcription: none" in text or "transcription:" not in text:
        problems.append("no transcription backend")
    if "ERROR" in text:
        problems.append("errors in the log")
    if not alive:
        problems.append(f"the app exited (code {proc.returncode})")
    print("--- app log ---\n" + (text or "(empty)"))
    if output.strip():
        print("--- app output ---\n" + output)
    if problems:
        print("SMOKE TEST FAILED: " + "; ".join(problems))
        return 1
    print("Smoke test passed: the app started properly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
