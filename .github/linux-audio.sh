#!/bin/sh
# CI on Linux: a virtual display (xvfb) and PulseAudio with a virtual speaker
# (null sink, whose monitor is the "system audio") and a silent virtual microphone.
set -e
sudo apt-get update -q
sudo apt-get install -y -q --no-install-recommends pulseaudio pulseaudio-utils xvfb xauth
pulseaudio --start --exit-idle-time=-1
pactl load-module module-null-sink sink_name=virtual_speaker
pactl set-default-sink virtual_speaker
pactl load-module module-null-source source_name=virtual_mic
pactl set-default-source virtual_mic
pactl info | grep -E "Server Name|Server Version|Default S"
