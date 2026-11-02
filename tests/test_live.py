import time

import numpy as np

from meetingrec.live import LiveTranscriber
from meetingrec.transcribe import Segment

SR = 16_000


class FakeTranscriber:
    """Returns one segment per call, spanning the whole window; no real model."""

    def __init__(self):
        self.calls = 0
        self.windows: list[int] = []

    def transcribe(self, audio, model, device="cpu", language=None):
        self.calls += 1
        self.windows.append(len(audio))
        return [Segment(0.0, len(audio) / SR, f"chunk{self.calls}")]


def tone(seconds: float, level: float = 0.3) -> np.ndarray:
    return np.full(int(SR * seconds), level, dtype=np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(SR * seconds), dtype=np.float32)


def test_segments_accumulate_with_a_running_offset():
    ft = FakeTranscriber()
    live = LiveTranscriber(transcriber=ft, silence_threshold=0.0, min_window=0.0)
    live.feed(tone(5))
    live._process(final=True)
    live.feed(tone(5))
    live._process(final=True)
    segs = live.segments
    assert [round(s.start) for s in segs] == [0, 5]
    assert [round(s.end) for s in segs] == [5, 10]
    assert [s.text for s in segs] == ["chunk1", "chunk2"]
    assert "chunk1" in live.transcript_text()


def test_silence_advances_time_without_transcribing():
    ft = FakeTranscriber()
    live = LiveTranscriber(transcriber=ft, silence_threshold=0.01, min_window=0.0)
    live.feed(silence(4))
    live._process(final=True)
    assert ft.calls == 0 and live.segments == []
    live.feed(tone(3))
    live._process(final=True)
    assert ft.calls == 1
    assert round(live.segments[0].start) == 4   # the skipped silence still counts on the clock


def test_non_final_pass_cuts_at_a_pause_and_keeps_the_tail():
    ft = FakeTranscriber()
    live = LiveTranscriber(transcriber=ft, silence_threshold=0.01, min_window=0.0)
    live.feed(np.concatenate([tone(3), silence(0.5), tone(2)]))
    live._process(final=False)              # should cut in the silent gap near 3.5 s
    assert ft.calls == 1
    assert 3.0 <= live._consumed <= 4.0     # consumed the first part, kept ~2 s tail
    live._process(final=True)               # the kept tail is transcribed next
    assert ft.calls == 2


def test_min_window_defers_tiny_buffers_until_final():
    ft = FakeTranscriber()
    live = LiveTranscriber(transcriber=ft, silence_threshold=0.0, min_window=6.0)
    live.feed(tone(2))
    live._process(final=False)              # below min_window: nothing yet
    assert ft.calls == 0
    live._process(final=True)               # final flushes whatever is buffered
    assert ft.calls == 1


def test_feed_stereo_mixes_to_one_channel():
    ft = FakeTranscriber()
    live = LiveTranscriber(transcriber=ft, silence_threshold=0.0, min_window=0.0)
    left, right = tone(2, 0.4), tone(2, 0.4)
    live.feed_stereo(left, right)
    live._process(final=True)
    assert ft.calls == 1 and ft.windows[0] == len(left)


def test_worker_thread_processes_then_stop_flushes():
    ft = FakeTranscriber()
    live = LiveTranscriber(transcriber=ft, interval=0.5, min_window=0.0, silence_threshold=0.0)
    live.start()
    live.feed(tone(1))
    time.sleep(1.2)            # at least one 0.5 s interval tick
    live.feed(tone(1))
    live.stop()               # joins the worker and runs a final pass
    assert ft.calls >= 1 and live.segments
    assert live._pending_frames == 0
