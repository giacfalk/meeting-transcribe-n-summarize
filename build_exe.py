#!/usr/bin/env python3
"""
Build script -- run once to produce MeetingRecorder.exe
Output: dist/MeetingRecorder/MeetingRecorder.exe  (pin this to taskbar)
"""

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent


# -- Icon creation -----------------------------------------------------------
def make_icon() -> Path:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("Installing Pillow...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow", "-q"])
        from PIL import Image, ImageDraw

    sizes = [256, 64, 48, 32, 16]
    frames = []

    for sz in sizes:
        img = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
        d   = ImageDraw.Draw(img)
        cx  = sz // 2

        # Red rounded-square background
        pad = max(1, sz // 14)
        d.rounded_rectangle(
            [pad, pad, sz - pad - 1, sz - pad - 1],
            radius=sz // 5, fill="#c62828"
        )

        # Mic capsule (white rounded rectangle)
        mw = max(4,  sz * 22 // 64)
        mh = max(6,  sz * 30 // 64)
        my = max(2,  sz * 13 // 64)
        d.rounded_rectangle(
            [cx - mw // 2, my, cx + mw // 2, my + mh],
            radius=mw // 2, fill="white"
        )

        # Stand arc
        am  = max(2, sz * 10 // 64)
        ay  = my + mh - max(1, sz // 20)
        alw = max(2, sz // 22)
        d.arc(
            [am, ay, sz - am, ay + sz * 10 // 32],
            start=180, end=0, fill="white", width=alw
        )

        # Vertical post
        lw = max(1, sz // 44)
        lt = ay + sz * 10 // 32 // 2
        lb = lt + sz * 9 // 64
        d.rectangle([cx - lw, lt, cx + lw, lb], fill="white")

        # Base bar
        bw = sz * 18 // 64
        bh = max(2, sz // 30)
        d.rectangle([cx - bw // 2, lb, cx + bw // 2, lb + bh], fill="white")

        frames.append(img)

    out = HERE / "icon.ico"
    frames[0].save(
        str(out), format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=frames[1:],
    )
    print(f"  Created: {out.name}")
    return out


# -- PyInstaller build -------------------------------------------------------
def build(icon: Path):
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("Installing PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller", "-q"])

    sep = os.pathsep  # ';' on Windows

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onedir",                          # folder bundle -- fast startup
        "--windowed",                        # no console window
        f"--icon={icon}",
        "--name=MeetingRecorder",
        "--distpath", str(HERE / "dist"),
        "--workpath", str(HERE / "build"),
        "--specpath", str(HERE),
        # embed icon.ico so root.iconbitmap() works at runtime
        "--add-data", f"{icon}{sep}.",
        # collect native libs for audio + AI backends
        "--collect-all", "soundcard",
        "--collect-all", "faster_whisper",
        "--collect-all", "ctranslate2",
        "--collect-all", "onnxruntime",
        # NB: summarization shells out to the external `claude` CLI at runtime,
        # so no Anthropic SDK needs to be bundled here.
        str(HERE / "meeting_recorder.py"),
    ]

    print("Running PyInstaller (this takes a minute)...")
    result = subprocess.run(cmd)

    if result.returncode != 0:
        print("\n[!] PyInstaller failed. See output above.")
        sys.exit(1)

    exe = HERE / "dist" / "MeetingRecorder" / "MeetingRecorder.exe"
    if exe.exists():
        print(f"\n  Done!  {exe}")
        print("\n  To pin to taskbar:")
        print(f"    1. Open:  {exe.parent}")
        print(f"    2. Right-click MeetingRecorder.exe -> 'Pin to taskbar'")
    else:
        print("\n  Build finished -- check dist/MeetingRecorder/")


# -- Main --------------------------------------------------------------------
if __name__ == "__main__":
    print("=== Meeting Recorder -- Build ===\n")
    print("Step 1/2: creating icon...")
    icon = make_icon()
    print("Step 2/2: packaging with PyInstaller...")
    build(icon)
    print("\nAll done.")
