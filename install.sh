#!/bin/bash
# Install Utter on this Mac, or update it. Run it from the folder you cloned:
#
#   ./install.sh
#
# It checks the Mac, sets up Python and the packages Utter needs, downloads the speech model
# once, builds Utter.app on this Mac and puts it in Applications. It asks before every
# download and before adding anything to your keychain. Run it again later to update Utter:
# your settings, words and the model are kept.
#
# Options:
#   --yes                 answer yes to every question (the recommended choices)
#   --small               use the smaller speech model (0.5 GB instead of 1.6 GB)
#   --no-update           don't look for a newer version
#   --no-open             don't open Utter at the end
#   --applications DIR    install Utter.app into DIR

set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"

YES=0
SMALL=0
NO_UPDATE=0
NO_OPEN=0
APPLICATIONS=""
while [ $# -gt 0 ]; do
    case "$1" in
        --yes) YES=1 ;;
        --small) SMALL=1 ;;
        --no-update) NO_UPDATE=1 ;;
        --no-open) NO_OPEN=1 ;;
        --applications) APPLICATIONS="${2:?--applications needs a folder}"; shift ;;
        -h|--help) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $1 (see ./install.sh --help)" >&2; exit 2 ;;
    esac
    shift
done

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
note() { printf '    %s\n' "$1"; }
fail() { printf '\n\033[1;31mStopped:\033[0m %s\n' "$1" >&2; exit 1; }
ask() {
    if [ "$YES" = 1 ]; then return 0; fi
    local answer=""
    if ! read -r -p "    $1 [Y/n] " answer </dev/tty; then
        fail "No answer possible here. Run ./install.sh in Terminal, or add --yes."
    fi
    case "$answer" in [nN]*) return 1 ;; *) return 0 ;; esac
}

printf '\033[1mUtter: private voice typing and voice commands for your Mac\033[0m\n'

# ---- 1. This Mac ---------------------------------------------------------------------------
step "Checking this Mac"
if [ "$(uname -s)" != "Darwin" ]; then fail "Utter only runs on macOS."; fi
if [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" != "1" ]; then
    fail "Utter needs a Mac with Apple silicon (M1 or newer)."
fi
MACOS="$(sw_vers -productVersion)"
if [ "${MACOS%%.*}" -lt 14 ]; then fail "Utter needs macOS 14 Sonoma or newer (this Mac has $MACOS)."; fi
note "macOS $MACOS on Apple silicon"
if ! xcode-select -p >/dev/null 2>&1; then
    note "Apple's command line tools are needed to build Utter (free, from Apple)."
    xcode-select --install >/dev/null 2>&1 || true
    fail "Finish the installer that just opened, then run ./install.sh again."
fi
note "Apple's command line tools are installed"
FREE_GB=$(( $(df -k "$HOME" | awk 'NR==2 {print $4}') / 1024 / 1024 ))
if [ "$FREE_GB" -lt 3 ]; then fail "Utter needs about 3 GB of free space; this Mac has ${FREE_GB} GB."; fi
note "${FREE_GB} GB free"

# ---- 2. A newer version? -------------------------------------------------------------------
if [ "$NO_UPDATE" = 0 ] && git -C "$ROOT" rev-parse --abbrev-ref '@{u}' >/dev/null 2>&1; then
    step "Looking for a newer version"
    if git -C "$ROOT" fetch --quiet 2>/dev/null; then
        BEHIND="$(git -C "$ROOT" rev-list --count 'HEAD..@{u}')"
        if [ "$BEHIND" = 0 ]; then
            note "You have the newest version."
        elif [ -n "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ]; then
            note "A newer version exists, but files in this folder were changed, so it was not fetched."
        elif ask "A newer version of Utter is available. Get it?"; then
            git -C "$ROOT" pull --ff-only --quiet
            note "Updated. Starting the new install.sh..."
            RERUN=(--no-update)
            [ "$YES" = 1 ] && RERUN+=(--yes)
            [ "$SMALL" = 1 ] && RERUN+=(--small)
            [ "$NO_OPEN" = 1 ] && RERUN+=(--no-open)
            [ -n "$APPLICATIONS" ] && RERUN+=(--applications "$APPLICATIONS")
            exec "$ROOT/install.sh" "${RERUN[@]}"
        fi
    else
        note "Couldn't reach GitHub; going on with this version."
    fi
fi

# ---- 3. uv (sets up Python) ------------------------------------------------------------------
step "Python"
UV="$(command -v uv || true)"
for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" /opt/homebrew/bin/uv; do
    if [ -z "$UV" ] && [ -x "$candidate" ]; then UV="$candidate"; fi
done
if [ -z "$UV" ]; then
    note "Utter uses uv (free, from astral.sh) to set up its own copy of Python."
    ask "Download and install uv?" || fail "Utter can't be set up without uv."
    curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null
    UV="$HOME/.local/bin/uv"
    [ -x "$UV" ] || fail "uv didn't install. See https://docs.astral.sh/uv/ and run ./install.sh again."
fi
note "uv: $("$UV" --version)"

PY="$ROOT/.venv/bin/python"
if [ ! -x "$PY" ] || [ "$("$PY" -c 'import sys; print(sys.version_info[:2] == (3, 12))' 2>/dev/null)" != "True" ]; then
    note "Setting up Python 3.12 for Utter (in this folder, .venv)..."
    "$UV" venv --quiet --python 3.12 "$ROOT/.venv"
fi
note "Installing the packages Utter needs (the first time takes a few minutes)..."
"$UV" pip install --quiet --python "$PY" -r "$ROOT/requirements.txt"
# mlx-whisper lists PyTorch, but only its model-conversion code uses it (never Utter): 1 GB less
"$UV" pip install --quiet --python "$PY" --no-deps "mlx-whisper>=0.4.0"
note "Python $("$PY" -c 'import platform; print(platform.python_version())') and Utter's packages are ready"

# ---- 4. The speech model -------------------------------------------------------------------
step "The speech model"
MODEL_ARGS=()
[ "$YES" = 1 ] && MODEL_ARGS+=(--yes)
[ "$SMALL" = 1 ] && MODEL_ARGS+=(--small)
"$PY" "$ROOT/scripts/download_model.py" ${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"} \
    || fail "Utter needs the speech model. Run ./install.sh again to carry on."

# ---- 5. Signing certificate -----------------------------------------------------------------
step "Signing"
if security find-identity -p codesigning 2>/dev/null | grep -q '"Utter Developer"'; then
    note "Your \"Utter Developer\" certificate is there: Utter keeps its permissions after updates."
else
    note "A free certificate of your own lets macOS remember Utter's permissions (microphone,"
    note "Accessibility, Input Monitoring) after an update. Without it you allow them again each time."
    if ask "Make the \"Utter Developer\" certificate in your keychain? (recommended)"; then
        "$ROOT/scripts/make_certificate.sh"
    else
        note "Skipped: Utter will be signed without a certificate."
    fi
fi

# ---- 6. Build and install -------------------------------------------------------------------
step "Building Utter.app"
if [ -z "$APPLICATIONS" ]; then
    APPLICATIONS="$(cd "$ROOT" && "$PY" -c 'from scripts.build_app import applications_folder; print(applications_folder())')"
fi
APP="$APPLICATIONS/Utter.app"
if pgrep -f "$APP/Contents/MacOS/Utter" >/dev/null 2>&1; then
    ask "Utter is running. Quit it so it can be updated?" || fail "Quit Utter (menu bar icon → Quit) and run ./install.sh again."
    osascript -e 'tell application id "com.utter.mac" to quit' >/dev/null 2>&1 || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        pgrep -f "$APP/Contents/MacOS/Utter" >/dev/null 2>&1 || break
        sleep 1
    done
    pkill -f "$APP/Contents/MacOS/Utter" 2>/dev/null || true
    sleep 1
fi
note "If macOS asks whether codesign may use the key \"Utter Developer\", click Always Allow."
(cd "$ROOT" && "$PY" scripts/build_app.py --install --applications "$APPLICATIONS") \
    || fail "The build failed (see above)."

# ---- 7. Done --------------------------------------------------------------------------------
step "Done"
note "Utter is in $APPLICATIONS. It lives in the menu bar (top right of the screen)."
note "Dictation: ⌃⌥D (Control+Option+D)   Commands: ⌃⌥M   Stop: Esc   (change them in Settings)"
note "To update later, run ./install.sh in this folder again."
if [ "$NO_OPEN" = 0 ]; then open "$APP"; fi
