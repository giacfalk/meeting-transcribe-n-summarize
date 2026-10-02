#!/usr/bin/env python3
"""
Build script -- packages the app with PyInstaller for the OS it runs on.

    Windows   dist/MeetingRecorder/MeetingRecorder.exe   (pin this to the taskbar)
    macOS     dist/MeetingRecorder.app
    Linux     dist/MeetingRecorder/MeetingRecorder

    python build_exe.py              draw the icons, then build
    python build_exe.py --zip        ...and package it into release/ for publishing
    python build_exe.py --installer  ...and (Windows, needs Inno Setup 6) build the installer
    python build_exe.py --icons      only redraw icon.ico and assets/logo.png
"""

import hashlib
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

HERE = Path(__file__).parent
WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"
DIST = HERE / "dist"
RELEASE = HERE / "release"
BUNDLE_ID = "io.github.giacfalk.meetingrecorder"


def version() -> str:
    text = (HERE / "meetingrec" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)


def platform_tag() -> str:
    machine = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(
        machine, machine)
    system = "windows" if WINDOWS else "macos" if MACOS else "linux"
    return f"{system}-{arch}"


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
def build(icon: Path) -> Path:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("Installing PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller", "-q"])

    sep = os.pathsep  # ';' on Windows, ':' elsewhere
    logo = HERE / "assets" / "logo.png"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onedir",                          # folder bundle -- fast startup
        "--windowed",                        # no console window; a .app bundle on macOS
        "--name=MeetingRecorder",
        "--distpath", str(DIST),
        "--workpath", str(HERE / "build"),
        "--specpath", str(HERE),
        # the window icon at runtime (icon.ico on Windows, the PNG elsewhere)
        "--add-data", f"{icon}{sep}.",
        "--add-data", f"{logo}{sep}assets",
        # collect native libs for audio + AI backends
        "--collect-all", "soundcard",
        "--collect-all", "faster_whisper",
        "--collect-all", "ctranslate2",
        "--collect-all", "onnxruntime",
        # The Claude CLI and Ollama backends are external programs; the optional
        # Anthropic API backend is bundled when `anthropic` is installed.
    ]
    if WINDOWS:
        cmd.append(f"--icon={icon}")
    elif MACOS:   # PyInstaller converts the PNG to .icns with Pillow
        cmd += [f"--icon={logo}", "--osx-bundle-identifier", BUNDLE_ID]
    cmd.append(str(HERE / "meeting_recorder.py"))

    print("Running PyInstaller (this takes a few minutes)...")
    if subprocess.run(cmd).returncode != 0:
        print("\n[!] PyInstaller failed. See output above.")
        sys.exit(1)

    if MACOS:
        app = DIST / "MeetingRecorder.app"
        finish_macos_app(app)
        print(f"\n  Done!  {app}")
        return app
    exe = DIST / "MeetingRecorder" / ("MeetingRecorder.exe" if WINDOWS else "MeetingRecorder")
    if not WINDOWS:
        for extra in ("meeting-recorder.desktop", "install.sh"):
            shutil.copy(HERE / "packaging" / "linux" / extra, exe.parent)
    print(f"\n  Done!  {exe}")
    if WINDOWS:
        print("\n  To pin to taskbar:")
        print(f"    1. Open:  {exe.parent}")
        print("    2. Right-click MeetingRecorder.exe -> 'Pin to taskbar'")
    return exe


def finish_macos_app(app: Path) -> None:
    """Add what macOS needs before it allows microphone access, then re-sign."""
    plist_path = app / "Contents" / "Info.plist"
    with open(plist_path, "rb") as f:
        info = plistlib.load(f)
    info.update({
        "CFBundleDisplayName": "Meeting Recorder",
        "CFBundleShortVersionString": version(),
        "CFBundleVersion": version(),
        "NSMicrophoneUsageDescription":
            "Meeting Recorder records your microphone so it can transcribe your meetings.",
        "NSHighResolutionCapable": True,
    })
    with open(plist_path, "wb") as f:
        plistlib.dump(info, f)
    # Editing Info.plist invalidates PyInstaller's ad-hoc signature: sign again.
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)


# -- Release packages --------------------------------------------------------
def _checksum(path: Path) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    (path.parent / f"{path.name}.sha256").write_text(f"{digest}  {path.name}\n")
    print(f"\n  Packaged: {path}  ({path.stat().st_size / 1e6:.1f} MB)\n  SHA-256: {digest}")


def make_zip() -> Path:
    """Package the build into release/: .zip on Windows and macOS, .tar.gz on Linux."""
    RELEASE.mkdir(exist_ok=True)
    name = f"MeetingRecorder-v{version()}-{platform_tag()}"
    if MACOS:
        out = RELEASE / f"{name}.zip"
        # ditto keeps the symlinks and signature inside the .app intact
        subprocess.run(["ditto", "-c", "-k", "--keepParent",
                        str(DIST / "MeetingRecorder.app"), str(out)], check=True)
    elif WINDOWS:
        out = RELEASE / f"{name}.zip"
        src = DIST / "MeetingRecorder"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for f in sorted(src.rglob("*")):
                if f.is_file():
                    z.write(f, Path("MeetingRecorder") / f.relative_to(src))
    else:
        out = RELEASE / f"{name}.tar.gz"
        with tarfile.open(out, "w:gz") as t:
            t.add(DIST / "MeetingRecorder", arcname="MeetingRecorder")
    _checksum(out)
    return out


def find_iscc() -> str:
    """Inno Setup's command-line compiler, if installed."""
    candidates = [
        shutil.which("iscc"),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
    ]
    return next((str(c) for c in candidates if c and Path(c).exists()), "")


def make_installer() -> Path | None:
    """release/MeetingRecorder-vX.Y.Z-windows-x64-setup.exe, built with Inno Setup."""
    iscc = find_iscc()
    if not iscc:
        print("\n[!] Inno Setup 6 not found (https://jrsoftware.org/isinfo.php); "
              "skipping the installer.")
        return None
    RELEASE.mkdir(exist_ok=True)
    name = f"MeetingRecorder-v{version()}-{platform_tag()}-setup"
    subprocess.run([iscc, "/Qp", f"/DAppVersion={version()}",
                    f"/DBundleDir={DIST / 'MeetingRecorder'}", f"/DOutputDir={RELEASE}",
                    f"/DOutputName={name}", str(HERE / "installer" / "MeetingRecorder.iss")],
                   check=True)
    out = RELEASE / f"{name}.exe"
    _checksum(out)
    return out


# -- Main --------------------------------------------------------------------
if __name__ == "__main__":
    print(f"=== Meeting Recorder {version()} -- Build ({platform_tag()}) ===\n")
    print("Step 1: creating icons...")
    icon = make_icon()
    if "--icons" in sys.argv:
        sys.exit(0)
    print("Step 2: packaging with PyInstaller...")
    build(icon)
    if "--zip" in sys.argv:
        print("Step 3: packaging the release...")
        make_zip()
    if "--installer" in sys.argv and WINDOWS:
        print("Step 4: building the installer...")
        make_installer()
    print("\nAll done.")
