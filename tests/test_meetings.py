import subprocess

from meetingrec import meetings


def test_matching():
    patterns = ["teams", "zoom", "chrome"]
    assert meetings.matches("MSTeams", patterns)
    assert meetings.matches("Zoom", patterns)
    assert not meetings.matches("python", patterns)
    assert meetings.matches("anything", ["*"])
    assert not meetings.matches("Zoom", [""])


def test_windows_registry_entries():
    entries = [
        ("MSTeams_8wekyb3d8bbwe", True, 134353314878557272, 0),               # in a call
        ("C:#Users#me#AppData#Roaming#Zoom#bin#Zoom.exe", False, 1343533148, 0),  # in a call
        ("C:#Program Files#Mozilla Firefox#firefox.exe", False, 1343471396, 1343471423),  # done
        ("DRAWBOARD.DRAWBOARDPDF_az88965nfbvjc", True, 0, 0),                 # never used
        ("C:#Apps#MeetingRecorder#MeetingRecorder.exe", False, 1343, 0),      # ourselves
    ]
    assert meetings._windows_in_use(entries, [r"C:\Apps\MeetingRecorder\MeetingRecorder.exe"]) \
        == {"MSTeams", "Zoom"}


def test_linux_streams():
    streams = [
        {"application.name": "Firefox", "application.process.id": "1200"},
        {"application.process.binary": "zoom", "application.process.id": "1300"},
        {"application.name": "Meeting Recorder", "application.process.id": "42"},   # us
        {},
    ]
    assert meetings._linux_in_use(streams, own_pid=42) == {"Firefox", "zoom"}


def test_old_pactl_text_output_is_parsed(monkeypatch):
    text = (
        "Source Output #12\n"
        "\tDriver: protocol-native.c\n"
        "\tProperties:\n"
        '\t\tapplication.name = "Zoom Meetings"\n'
        '\t\tapplication.process.id = "991"\n'
        "Source Output #13\n"
        "\tProperties:\n"
        '\t\tapplication.name = "parecord"\n'
    )

    def fake_run(cmd, **kw):
        if "-f" in cmd:   # pactl < 16 has no JSON output
            return subprocess.CompletedProcess(cmd, 1, "", "Invalid option")
        return subprocess.CompletedProcess(cmd, 0, text, "")

    monkeypatch.setattr(meetings.subprocess, "run", fake_run)
    streams = meetings._pactl_source_outputs()
    assert [s.get("application.name") for s in streams] == ["Zoom Meetings", "parecord"]


def test_watcher_reports_start_and_end_once():
    using = [set()]
    events = []
    w = meetings.MeetingWatcher(lambda: ["zoom", "teams"],
                                on_start=lambda a: events.append(("start", a)),
                                on_end=lambda a: events.append(("end", a)),
                                probe=lambda: using[0])
    w.poll()
    using[0] = {"Zoom", "python"}          # python isn't a meeting app
    w.poll()
    w.poll()                                # still in the call: no new event
    using[0] = {"MSTeams"}
    w.poll()
    using[0] = set()
    w.poll()
    assert events == [("start", "Zoom"), ("start", "MSTeams"), ("end", "Zoom"),
                      ("end", "MSTeams")]


def test_watcher_survives_probe_errors():
    def broken():
        raise OSError("registry unavailable")

    w = meetings.MeetingWatcher(lambda: ["*"], on_start=lambda a: None, on_end=lambda a: None,
                                probe=broken)
    w.poll()   # must not raise
