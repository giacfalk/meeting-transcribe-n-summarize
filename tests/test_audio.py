import time

import numpy as np
import soundfile as sf

from meetingrec.audio import DualChannelRecorder, StereoWavWriter, read_channels, repair_wav

SR = 16_000


class FakeSource:
    """Stands in for a soundcard recorder: returns a constant level in real time."""

    def __init__(self, level: float, channels: int = 1, fail: bool = False):
        self.level, self.channels, self.fail = level, channels, fail

    def __call__(self, sample_rate):
        if self.fail:
            raise RuntimeError("no loopback device found")
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def record(self, numframes):
        time.sleep(numframes / SR / 4)   # 4x faster than real time keeps tests quick
        return np.full((numframes, self.channels), self.level, dtype=np.float32)


def test_writer_header_is_valid_before_close(tmp_path):
    path = tmp_path / "a.wav"
    w = StereoWavWriter(path, SR)
    w.write(np.full(SR, 0.5, np.float32), np.zeros(SR, np.float32))
    # Simulates a crash: the file is still open, but must already be readable.
    assert sf.info(str(path)).frames == SR
    w.close()
    (left, right), sr = read_channels(path)
    assert sr == SR and abs(left.mean() - 0.5) < 1e-3 and right.max() == 0


def test_recorder_writes_aligned_stereo(tmp_path):
    path = tmp_path / "meeting.wav"
    rec = DualChannelRecorder(path, SR, sources=(FakeSource(0.25), FakeSource(-0.5, channels=2)))
    rec.start()
    time.sleep(1.5)
    frames = rec.stop()
    assert frames > SR   # at least one second of audio
    assert rec.errors == []
    (mic, system), _ = read_channels(path)
    assert len(mic) == frames
    # Away from the zero-padded tail, each channel carries its own source.
    assert abs(np.median(mic) - 0.25) < 1e-3
    assert abs(np.median(system) + 0.5) < 1e-3


def test_missing_loopback_records_mic_with_silent_right_channel(tmp_path):
    path = tmp_path / "meeting.wav"
    rec = DualChannelRecorder(path, SR, sources=(FakeSource(0.25), FakeSource(0, fail=True)))
    rec.start()
    time.sleep(1.0)
    frames = rec.stop()
    assert frames > 0
    assert rec.errors == ["system audio: no loopback device found"]
    (mic, system), _ = read_channels(path)
    assert abs(np.median(mic) - 0.25) < 1e-3 and not system.any()


def test_silence_is_tracked(tmp_path):
    rec = DualChannelRecorder(tmp_path / "s.wav", SR, silence_threshold=0.01,
                              sources=(FakeSource(0.0), FakeSource(0.0)))
    rec.start()
    time.sleep(1.0)
    assert rec.seconds_since_sound >= 0.9
    rec.stop()


def test_digitally_silent_mic_is_detected(tmp_path):
    dead = DualChannelRecorder(tmp_path / "d.wav", SR, sources=(FakeSource(0.0), FakeSource(0.1)))
    live = DualChannelRecorder(tmp_path / "l.wav", SR, sources=(FakeSource(0.001), FakeSource(0.0)))
    dead.start()
    live.start()
    time.sleep(1.0)            # ~4 s of audio at the fake sources' speed
    assert dead.mic_is_digital_silence(after_seconds=2)
    assert not live.mic_is_digital_silence(after_seconds=2)
    assert not dead.mic_is_digital_silence(after_seconds=60)   # not enough audio yet
    dead.stop()
    live.stop()


def test_repair_wav_fixes_truncated_header(tmp_path):
    path = tmp_path / "crash.wav"
    w = StereoWavWriter(path, SR)
    w.write(np.zeros(SR, np.float32), np.zeros(SR, np.float32))
    w.close()
    with open(path, "ab") as f:          # audio written after the last header patch
        f.write(b"\x01\x00" * 2 * SR)
    assert sf.info(str(path)).frames == SR
    assert repair_wav(path) is True
    assert sf.info(str(path)).frames == 2 * SR
    assert repair_wav(path) is False     # already consistent


def test_repair_wav_leaves_trailing_chunks_alone(tmp_path):
    path = tmp_path / "list.wav"
    sf.write(str(path), np.zeros(SR, np.float32), SR)
    with open(path, "ab") as f:
        f.write(b"LIST" + (4).to_bytes(4, "little") + b"INFO")
    assert repair_wav(path) is False
    assert sf.info(str(path)).frames == SR
