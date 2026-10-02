import subprocess
import sys

import numpy as np
import pytest
import soundfile as sf

from meetingrec.audio import StereoWavWriter
from meetingrec.transcribe import (
    Segment,
    Transcriber,
    format_timestamp,
    format_transcript,
    is_echo,
    merge_speakers,
    to_srt,
    transcribe_recording,
)


def test_echo_of_remote_speech_is_dropped():
    others = [Segment(10.0, 14.0, "Let's ship the release on Friday.", "Others")]
    echo = Segment(10.3, 14.2, "let's ship the release on friday", "Me")
    reply = Segment(15.0, 17.0, "Sounds good to me.", "Me")
    overlap_but_different = Segment(11.0, 13.0, "Can you hear me okay?", "Me")
    assert is_echo(echo, others)
    assert not is_echo(reply, others)
    assert not is_echo(overlap_but_different, others)

    # Talking at the same time with similar-looking but different words is not an echo.
    lookalike = [Segment(20.0, 21.0, "level 0.2", "Others")]
    assert not is_echo(Segment(20.0, 21.0, "level 0.5", "Me"), lookalike)

    merged = merge_speakers([echo, reply, overlap_but_different] + others, "Me", "Others")
    assert [s.text for s in merged] == [
        "Let's ship the release on Friday.", "Can you hear me okay?", "Sounds good to me."]


def test_timestamps_switch_to_hours_for_long_meetings():
    assert format_timestamp(75) == "01:15"
    assert format_timestamp(3725) == "62:05"
    assert format_timestamp(3725, with_hours=True) == "01:02:05"
    short = format_transcript([Segment(5, 6, "Hi", "Me")])
    assert short == "[00:05] Me: Hi"
    long = format_transcript([Segment(5, 6, "Hi", "Me"), Segment(3700, 3701, "Bye", "")])
    assert long.splitlines() == ["[00:00:05] Me: Hi", "[01:01:40] Bye"]


def test_srt():
    srt = to_srt([Segment(0.0, 1.5, "Hello", "Me"), Segment(61.25, 62.0, "Hi", "Others")])
    assert srt == ("1\n00:00:00,000 --> 00:00:01,500\nMe: Hello\n\n"
                   "2\n00:01:01,250 --> 00:01:02,000\nOthers: Hi\n")


class FakeTranscriber:
    """Returns one segment per non-silent input, recording what it was given."""

    def __init__(self):
        self.calls = []

    def transcribe(self, audio, size, device="cpu", language=None):
        self.calls.append(audio)
        return [Segment(0.0, 1.0, f"level {float(np.max(audio)):.1f}")]


def _stereo(path, left, right):
    w = StereoWavWriter(path, 16_000)
    w.write(left, right)
    w.close()


def test_channels_are_transcribed_separately_and_labelled(tmp_path):
    path = tmp_path / "m.wav"
    _stereo(path, np.full(16_000, 0.5, np.float32), np.full(16_000, 0.25, np.float32))
    t = FakeTranscriber()
    segs = transcribe_recording(path, t, model="tiny")
    assert len(t.calls) == 2
    assert sorted((s.speaker, s.text) for s in segs) == [("Me", "level 0.5"),
                                                         ("Others", "level 0.2")]


def test_silent_system_channel_is_skipped(tmp_path):
    path = tmp_path / "m.wav"
    _stereo(path, np.full(16_000, 0.5, np.float32), np.zeros(16_000, np.float32))
    t = FakeTranscriber()
    segs = transcribe_recording(path, t, model="tiny")
    assert len(t.calls) == 1 and segs[0].speaker == "Me"


def test_room_noise_below_silence_threshold_is_not_transcribed(tmp_path):
    # A real failure: an unused mic picking up faint room noise (RMS ~0.002) made
    # Whisper invent sentences. Below the silence threshold, skip it entirely.
    rng = np.random.default_rng(0)
    noise = (rng.standard_normal(16_000 * 3) * 0.002).astype(np.float32)
    speech_level = np.full(16_000 * 3, 0.2, np.float32)
    path = tmp_path / "m.wav"
    _stereo(path, noise, speech_level)
    t = FakeTranscriber()
    segs = transcribe_recording(path, t, model="tiny", silence_threshold=0.01)
    assert len(t.calls) == 1 and [s.speaker for s in segs] == ["Others"]

    _stereo(path, noise, noise)
    for labels in (True, False):
        t = FakeTranscriber()
        assert transcribe_recording(path, t, model="tiny", speaker_labels=labels,
                                    silence_threshold=0.01) == []
        assert t.calls == []


def test_labels_off_mixes_and_v1_mono_files_still_work(tmp_path):
    path = tmp_path / "m.wav"
    _stereo(path, np.full(16_000, 0.5, np.float32), np.full(16_000, 0.5, np.float32))
    t = FakeTranscriber()
    segs = transcribe_recording(path, t, model="tiny", speaker_labels=False)
    assert len(t.calls) == 1 and segs[0].speaker == ""

    mono = tmp_path / "old.wav"
    sf.write(str(mono), np.full(16_000, 0.3, np.float32), 16_000)
    t = FakeTranscriber()
    segs = transcribe_recording(mono, t, model="tiny")
    assert len(t.calls) == 1 and segs[0].speaker == ""


# -- Real transcription (downloads the ~75 MB "tiny" model) ----------------
def _speak(text: str, path) -> bool:
    """Synthesize speech with Windows' built-in voices; False if unavailable."""
    if sys.platform != "win32":
        return False
    script = (
        "Add-Type -AssemblyName System.Speech;"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "$v = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'en-*' }"
        " | Select-Object -First 1; if (-not $v) { exit 3 }; $s.SelectVoice($v.VoiceInfo.Name);"
        "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,"
        " [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,"
        " [System.Speech.AudioFormat.AudioChannel]::Mono);"
        f"$s.SetOutputToWaveFile('{path}', $f); $s.Speak('{text}'); $s.Dispose()"
    )
    result = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                            capture_output=True, timeout=60)
    return result.returncode == 0 and path.exists()


@pytest.mark.slow
def test_real_transcription_with_speaker_labels(tmp_path):
    me, them = tmp_path / "me.wav", tmp_path / "them.wav"
    if not (_speak("Good morning everyone. Shall we start the meeting?", me)
            and _speak("Yes. The budget report is ready for review.", them)):
        pytest.skip("no Windows text-to-speech voice available")
    a, _ = sf.read(str(me), dtype="float32")
    b, _ = sf.read(str(them), dtype="float32")
    gap = np.zeros(8_000, np.float32)
    n = len(a) + len(gap) + len(b)
    left = np.concatenate([a, np.zeros(n - len(a), np.float32)])
    right = np.concatenate([np.zeros(len(a) + len(gap), np.float32), b])
    path = tmp_path / "meeting.wav"
    _stereo(path, left, right)

    segs = transcribe_recording(path, Transcriber(log=lambda m: None), model="tiny",
                                language="en", silence_threshold=0.01)   # app default
    text = format_transcript(segs).lower()
    assert segs[0].speaker == "Me" and "morning" in text
    assert any(s.speaker == "Others" and "budget" in s.text.lower() for s in segs)
