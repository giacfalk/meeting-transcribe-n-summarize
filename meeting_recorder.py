#!/usr/bin/env python3
"""
Meeting Recorder
GUI tool: click the button to start/stop recording.
Audio (mic + system loopback) is transcribed automatically when you stop,
then summarized. The code lives in the meetingrec/ package.
"""

from meetingrec.app import main

if __name__ == "__main__":
    main()
