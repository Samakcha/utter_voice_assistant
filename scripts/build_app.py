#!/usr/bin/env python3
"""
Build Utter.app: a self-contained Mac app (its own Python and packages inside).

  uv run python scripts/build_app.py            build dist/Utter.app
  uv run python scripts/build_app.py --install  build, then copy it to /Applications

Nothing is downloaded: the app is assembled from this project's code, the Python that
`uv` already installed, and the packages already in .venv. The Whisper models and your
settings are NOT inside the app; they live in ~/Library/Application Support/Utter.

Signing: if a code-signing certificate named "Utter Developer" is in your keychain
(see SIGNING in the PRD, section 6.15), the app is signed with it, so macOS remembers
Utter's permissions across rebuilds. Otherwise it is signed "ad hoc": it runs, but every
rebuild looks like a new app and the permissions have to be granted again.
"""

import argparse
import compileall
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

APP_NAME = "Utter"
BUNDLE_ID = "com.utter.mac"  # changing it later makes macOS treat Utter as a new app
VERSION = "0.4.0"
MIN_MACOS = "14.0"
SIGNING_IDENTITY = "Utter Developer"

DIST = PROJECT_ROOT / "dist"
APP_CODE = ["main.py", "config.toml", "engine", "interface", "assets"]
ICON_SOURCE = PROJECT_ROOT / "assets" / "utter-app-icon.png"
LAUNCHER_SOURCE = PROJECT_ROOT / "packaging" / "launcher.c"

# Not needed to run Utter: tests, installers, editors and GUI toolkits it never uses
STDLIB_EXCLUDE = ["test", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3",
                  "unittest/test", "config-3.12-darwin", "__phello__"]
SITE_EXCLUDE = ["pip", "PyObjCTest", "_pytest", "pytest", "py.py", "pluggy", "iniconfig",
                "pygments", "_distutils_hack", "distutils-precedence.pth"]
SITE_EXCLUDE_PREFIXES = ["pip-", "pytest-", "pluggy-", "iniconfig-", "pygments-"]

INFO_PLIST = {
    "CFBundleName": APP_NAME,
    "CFBundleDisplayName": APP_NAME,
    "CFBundleIdentifier": BUNDLE_ID,
    "CFBundleExecutable": APP_NAME,
    "CFBundleIconFile": APP_NAME,
    "CFBundlePackageType": "APPL",
    "CFBundleShortVersionString": VERSION,
    "CFBundleVersion": VERSION,
    "CFBundleInfoDictionaryVersion": "6.0",
    "LSMinimumSystemVersion": MIN_MACOS,
    "LSArchitecturePriority": ["arm64"],
    "LSUIElement": True,  # menu bar only: no Dock icon, no app menu
    "NSHighResolutionCapable": True,
    "NSHumanReadableCopyright": "Utter",
    # Shown in the permission prompts. Without these macOS stops the app when it asks.
    "NSMicrophoneUsageDescription":
        "Utter listens only while you hold or tap its hotkey, and turns your speech into "
        "text on this Mac. Audio never leaves your computer.",
    "NSAppleEventsUsageDescription":
        "Utter opens apps you ask for and saves your voice notes in the Notes app.",
}


def log(message: str) -> None:
    print(message, flush=True)


def run(args: List[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=True, **kwargs)


# ---- Pieces ----------------------------------------------------------------------------

def python_home() -> Path:
    """The standalone Python that uv installed (what .venv was made from)."""
    return Path(sys.base_prefix).resolve()


def site_packages() -> Path:
    return Path(sysconfig.get_paths()["purelib"]).resolve()


def clone_tree(source: Path, dest: Path) -> None:
    """Copy a folder as an APFS clone: instant, and no extra disk space until changed."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        run(["cp", "-cR", str(source), str(dest)])
    except subprocess.CalledProcessError:
        shutil.copytree(source, dest, symlinks=True)


def remove(paths: List[Path]) -> None:
    for path in paths:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()


def make_icon(dest: Path, source: Path = ICON_SOURCE) -> None:
    """1024 px PNG -> Utter.icns with every size macOS uses (16 to 1024 px)."""
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / f"{APP_NAME}.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = size * scale
                name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
                run(["sips", "-z", str(px), str(px), str(source), "--out", str(iconset / name)],
                    stdout=subprocess.DEVNULL)
        run(["iconutil", "-c", "icns", str(iconset), "-o", str(dest)])


def copy_python(resources: Path) -> Path:
    """A private copy of Python with this project's packages inside its site-packages."""
    home = resources / "python"
    clone_tree(python_home() / "lib", home / "lib")
    stdlib = home / "lib" / "python3.12"
    remove([stdlib / name for name in STDLIB_EXCLUDE])
    remove([p for p in (home / "lib").iterdir() if p.name not in ("python3.12", "libpython3.12.dylib")])
    target = stdlib / "site-packages"
    remove([target])
    clone_tree(site_packages(), target)
    remove([target / name for name in SITE_EXCLUDE])
    remove([p for p in target.iterdir() if any(p.name.startswith(x) for x in SITE_EXCLUDE_PREFIXES)])
    # The dylib names its own absolute path; make it relative to the app instead
    dylib = home / "lib" / "libpython3.12.dylib"
    run(["install_name_tool", "-id", "@rpath/libpython3.12.dylib", str(dylib)], stderr=subprocess.DEVNULL)
    run(["codesign", "--force", "--sign", "-", str(dylib)], stderr=subprocess.DEVNULL)
    return home


def copy_app_code(resources: Path) -> Path:
    app = resources / "app"
    app.mkdir(parents=True)
    for name in APP_CODE:
        source = PROJECT_ROOT / name
        if source.is_dir():
            shutil.copytree(source, app / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
        else:
            shutil.copy2(source, app / name)
    return app


def build_launcher(dest: Path, python: Path) -> None:
    include = sysconfig.get_paths()["include"]
    run([
        "clang", "-O2", f"-mmacosx-version-min={MIN_MACOS}", "-arch", "arm64",
        "-I", include, str(LAUNCHER_SOURCE),
        "-L", str(python / "lib"), "-lpython3.12",
        "-Wl,-rpath,@executable_path/../Resources/python/lib",
        "-o", str(dest),
    ])


def precompile(paths: List[Path]) -> None:
    """Compile .pyc files now: the app never writes into itself at run time."""
    for path in paths:
        compileall.compile_dir(str(path), quiet=2, workers=0)


def find_identity(name: str = SIGNING_IDENTITY) -> Optional[str]:
    """The keychain certificate to sign with, or None (then: ad hoc)."""
    out = subprocess.run(["security", "find-identity", "-p", "codesigning"],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        if f'"{name}"' in line:
            return line.split()[1]  # its SHA-1 fingerprint (unambiguous)
    return None


def sign(app: Path, identity: Optional[str]) -> str:
    who = identity or "-"
    run(["codesign", "--force", "--sign", who, "--identifier", BUNDLE_ID, str(app)],
        stderr=subprocess.DEVNULL)
    run(["codesign", "--verify", "--strict", str(app)])
    return SIGNING_IDENTITY if identity else "ad hoc (permissions reset on every rebuild)"


def folder_size(path: Path) -> int:
    return sum(p.lstat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


# ---- Build -----------------------------------------------------------------------------

def build(dist: Path = DIST) -> Path:
    start = time.perf_counter()
    app = dist / f"{APP_NAME}.app"
    staging = dist / f".{APP_NAME}.app.building"
    remove([staging])
    contents = staging / "Contents"
    resources = contents / "Resources"
    (contents / "MacOS").mkdir(parents=True)
    resources.mkdir()

    log("1/6 Info.plist and icon")
    with open(contents / "Info.plist", "wb") as f:
        plistlib.dump(INFO_PLIST, f)
    make_icon(resources / f"{APP_NAME}.icns")

    log("2/6 Python and packages (cloned from .venv: no download)")
    python = copy_python(resources)
    log("3/6 Utter's code")
    app_code = copy_app_code(resources)
    log("4/6 Launcher")
    build_launcher(contents / "MacOS" / APP_NAME, python)
    log("5/6 Compiling Python files")
    precompile([app_code, python / "lib" / "python3.12"])

    log("6/6 Signing")
    signed_as = sign(staging, find_identity())
    remove([app])
    os.rename(staging, app)
    log(f"\nBuilt {app}")
    log(f"  size {folder_size(app) / 1e9:.2f} GB, signed: {signed_as}, {time.perf_counter() - start:.0f} s")
    return app


def install(app: Path, applications: Path = Path("/Applications")) -> Path:
    dest = applications / app.name
    if dest.exists():
        running = subprocess.run(["pgrep", "-f", str(dest / "Contents" / "MacOS")], capture_output=True)
        if running.returncode == 0:
            raise SystemExit("Utter is running from /Applications. Quit it first (menu bar icon > Quit).")
        remove([dest])
    run(["ditto", str(app), str(dest)])
    log(f"Installed {dest}")
    return dest


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build Utter.app (no downloads).")
    parser.add_argument("--install", action="store_true", help="Also copy it to /Applications.")
    args = parser.parse_args(argv)
    if sys.platform != "darwin" or os.uname().machine != "arm64":
        raise SystemExit("Utter.app can only be built on an Apple Silicon Mac.")
    app = build()
    if args.install:
        install(app)
    return 0


if __name__ == "__main__":
    sys.exit(main())
