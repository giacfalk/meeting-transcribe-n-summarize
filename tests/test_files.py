from datetime import datetime

from meetingrec import files


def test_new_recording_path_avoids_collisions(tmp_path):
    started = datetime(2026, 5, 14, 10, 30, 0)
    first = files.new_recording_path(tmp_path, started)
    assert first.name == "meeting_20260514_103000.wav"
    first.touch()
    second = files.new_recording_path(tmp_path, started)
    assert second.name == "meeting_20260514_103000_2.wav"
    assert files.parse_stamp(second) == started


def test_parse_stamp_rejects_other_names(tmp_path):
    assert files.parse_stamp(tmp_path / "notes.wav") is None


def test_pending_detection(tmp_path):
    (tmp_path / "meeting_20260101_090000.wav").touch()          # untranscribed
    (tmp_path / "meeting_20260101_100000.wav").touch()          # being recorded
    (tmp_path / "meeting_20260101_110000.wav").touch()          # has transcript
    (tmp_path / "meeting_20260101_110000.txt").write_text("x")  # no summary
    (tmp_path / "meeting_20260101_120000.txt").write_text("x")  # legacy summary
    (tmp_path / "meeting_20260101_130000.txt").write_text("x")  # new summary
    summary = tmp_path / files.SUMMARY_SUBDIR
    summary.mkdir()
    (summary / "meeting_20260101_120000_summary.txt").write_text("s")
    (summary / "meeting_20260101_130000_summary.md").write_text("s")

    recording = tmp_path / "meeting_20260101_100000.wav"
    assert [p.name for p in files.find_untranscribed(tmp_path, exclude={recording, None})] == [
        "meeting_20260101_090000.wav"]
    assert [p.name for p in files.find_unsummarized(tmp_path)] == [
        "meeting_20260101_110000.txt"]


def test_missing_folder_has_nothing_pending(tmp_path):
    assert files.find_untranscribed(tmp_path / "nope") == []
    assert files.find_unsummarized(tmp_path / "nope") == []
