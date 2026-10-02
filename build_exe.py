#!/usr/bin/env python3
"""
Build script -- produces dist/MeetingRecorder/MeetingRecorder.exe (pin this to taskbar)

    python build_exe.py            draw the icons, then package with PyInstaller
    python build_exe.py --zip      ...and zip the bundle into release/ for publishing
    python build_exe.py --icons    only redraw icon.ico and assets/logo.png
"""

import hashlib
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).parent


def version() -> str:
    text = (HERE / "meetingrec" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)


# -- Icon creation -----------------------------------------------------------
def draw_logo(size: int):
    """Red rounded square with a white studio microphone.

    Drawn once at 1024 px and downscaled, so every size is smooth.
    """
    from PIL import Image, ImageDraw

    S = 1024
    grad = Image.new("RGBA", (S, S))
    top, bottom = (229, 57, 53), (183, 28, 28)
    for y in range(S):
        t = y / (S - 1)
        color = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom, strict=True)) + (255,)
        grad.paste(color, (0, y, S, y + 1))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([40, 40, S - 41, S - 41], radius=220, fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)

    d, cx, white = ImageDraw.Draw(img), S // 2, (255, 255, 255, 255)
    d.rounded_rectangle([cx - 150, 160, cx + 150, 620], radius=150, fill=white)    # capsule
    for y in (300, 370, 440):                                                      # grille
        d.rounded_rectangle([cx - 88, y - 11, cx + 88, y + 11], radius=11, fill=(198, 40, 40, 255))
    d.arc([cx - 230, 240, cx + 230, 700], start=0, end=180, fill=white, width=56)  # holder
    d.rectangle([cx - 28, 690, cx + 28, 820], fill=white)                          # stem
    d.rounded_rectangle([cx - 170, 812, cx + 170, 868], radius=28, fill=white)     # base
    return img.resize((size, size), Image.LANCZOS)


def make_icon() -> Path:
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("Installing Pillow...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow", "-q"])

    sizes = [256, 128, 64, 48, 32, 24, 16]
    frames = [draw_logo(s) for s in sizes]
    out = HERE / "icon.ico"
    frames[0].save(str(out), format="ICO", sizes=[(s, s) for s in sizes],
                   append_images=frames[1:])
    print(f"  Created: {out.name}")

    logo = HERE / "assets" / "logo.png"
    logo.parent.mkdir(exist_ok=True)
    draw_logo(256).save(str(logo), optimize=True)
    print(f"  Created: assets/{logo.name}")
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
        # The Claude CLI and Ollama backends are external programs; the optional
        # Anthropic API backend is bundled when `anthropic` is installed.
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
        print("    2. Right-click MeetingRecorder.exe -> 'Pin to taskbar'")
    else:
        print("\n  Build finished -- check dist/MeetingRecorder/")


def make_zip() -> Path:
    """Zip dist/MeetingRecorder into release/ and print its SHA-256."""
    src = HERE / "dist" / "MeetingRecorder"
    out = HERE / "release" / f"MeetingRecorder-v{version()}-windows-x64.zip"
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(src.rglob("*")):
            if f.is_file():
                z.write(f, Path("MeetingRecorder") / f.relative_to(src))
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    (out.parent / f"{out.name}.sha256").write_text(f"{digest}  {out.name}\n")
    print(f"\n  Zipped: {out}  ({out.stat().st_size / 1e6:.1f} MB)\n  SHA-256: {digest}")
    return out


# -- Main --------------------------------------------------------------------
if __name__ == "__main__":
    print(f"=== Meeting Recorder {version()} -- Build ===\n")
    print("Step 1: creating icons...")
    icon = make_icon()
    if "--icons" in sys.argv:
        sys.exit(0)
    print("Step 2: packaging with PyInstaller...")
    build(icon)
    if "--zip" in sys.argv:
        print("Step 3: zipping the release...")
        make_zip()
    print("\nAll done.")
