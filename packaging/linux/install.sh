#!/bin/sh
# Adds Meeting Recorder to your application menu. Run it from the unpacked folder:
#   ./install.sh
set -e
dir="$(cd "$(dirname "$0")" && pwd)"
dest="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$dest"
sed "s|MEETING_RECORDER_DIR|$dir|g" "$dir/meeting-recorder.desktop" > "$dest/meeting-recorder.desktop"
chmod +x "$dir/MeetingRecorder"
echo "Added Meeting Recorder to your application menu: $dest/meeting-recorder.desktop"
