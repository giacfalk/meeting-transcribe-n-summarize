"""Real audio devices -- opt-in with MR_DEVICE_TESTS=1.

CI runs these on Linux against a PulseAudio null sink ("virtual speaker"):
a tone played into it must show up on the system-audio channel, and a
recording app must be detected as using the microphone.
"""

import os
import shutil
import subprocess
import sys
import time

import numpy as np
import pytest
import soundfile as sf

pytestmark = pytest.mark.skipif(os.environ.get("MR_DEVICE_TESTS") != "1",
                                reason="set MR_DEVICE_TESTS=1 to use real audio devices")


def test_system_audio_is_recorded(tmp_path):
    from meetingrec.audio import DualChannelRecorder, list_devices, read_channels

    mics, system = list_devices()
    assert system, "no loopback/monitor device found"
    tone = tmp_path / "tone.wav"
    t = np.arange(int(48_000 * 3)) / 48_000
    sf.write(str(tone), (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), 48_000)

    rec = DualChannelRecorder(tmp_path / "meeting.wav")
    rec.start()
    time.sleep(0.5)
    player = subprocess.Popen(["paplay", str(tone)])
    player.wait(timeout=15)
    time.sleep(0.5)
    frames = rec.stop()
    assert frames > 0 and not [e for e in rec.errors if "system audio" in e], rec.errors
    (_mic, others), _ = read_channels(tmp_path / "meeting.wav")
    assert float(np.sqrt(np.mean(others ** 2))) > 0.02   # the tone, not silence


@pytest.mark.skipif(not sys.platform.startswith("linux") or not shutil.which("parecord"),
                    reason="needs PulseAudio's parecord")
def test_recording_app_is_detected():
    from meetingrec.meetings import apps_using_microphone

    rec = subprocess.Popen(["parecord", "--raw", os.devnull])
    try:
        deadline = time.time() + 10
        while time.time() < deadline and "parecord" not in apps_using_microphone():
            time.sleep(0.5)
        assert "parecord" in apps_using_microphone()
    finally:
        rec.terminate()
        rec.wait(timeout=10)
    time.sleep(1)
    assert "parecord" not in apps_using_microphone()
