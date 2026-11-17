"""
Utter (macOS Apple Silicon) - Step 2 Entry Point
Runs local menu bar app with global hotkeys, Silero VAD, and MLX Whisper transcription.
"""

import fcntl
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import platform
import sys
from typing import Optional

# The app runs fully offline: never let Hugging Face fetch model weights at runtime.
# Models are downloaded deliberately with scripts/benchmark_whisper.py, which runs separately.
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from engine import AudioCapture, MLXWhisperTranscriber, SessionManager
import engine.config as engine_config
from engine.data_folder import prepare_data_folder
from interface import VoiceAssistantApp, format_permissions_report, get_all_permissions

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("voice_assistant")

LOG_FILE_NAME = "utter.log"
LOG_MAX_BYTES = 1_000_000  # about a week of normal use; three older files are kept
LOG_BACKUPS = 3


def setup_file_logging(log_dir: Path) -> Optional[Path]:
    """Also write log messages (timings, errors; never dictated text) to <log_dir>/utter.log."""
    path = log_dir / LOG_FILE_NAME
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename) == path:
            return path  # already set up (main() called twice in one process)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8")
    except OSError as e:
        logger.warning("Could not create the log file in %s: %s", log_dir, e)
        return None
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                                           datefmt="%Y-%m-%d %H:%M:%S"))
    handler.setLevel(logging.INFO)
    root.addHandler(handler)
    if root.getEffectiveLevel() > logging.INFO:
        root.setLevel(logging.INFO)  # timings are INFO messages
    return path


_exit_code = 0  # for the command-line modes (--check, --self-test)
LOCK_FILE_NAME = ".utter.lock"
_instance_lock = None  # kept open for the whole run; the OS releases it when Utter exits


def acquire_single_instance_lock(data_dir: Path):
    """Only one Utter at a time (the app and the terminal version would both grab the hotkeys)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    handle = open(data_dir / LOCK_FILE_NAME, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def self_test(config, wav_paths) -> bool:
    """`Utter --self-test [file.wav ...]`: load the voice detector and Whisper and transcribe,
    without the menu bar, hotkeys or microphone (checks a built Utter.app works)."""
    import faulthandler
    import time
    from engine.vad import SileroVAD
    # Never hang: after 45 s, print every thread's stack and exit (a broken build shows where)
    faulthandler.dump_traceback_later(45, exit=True)
    ok = True
    t0 = time.perf_counter()
    SileroVAD(sample_rate=config.audio.sample_rate, threshold=config.vad.speech_threshold)
    print(f"[self-test] voice detector loaded in {time.perf_counter() - t0:.2f}s", flush=True)
    t0 = time.perf_counter()
    transcriber = MLXWhisperTranscriber(config=config.whisper, normalization_config=config.normalization)
    ready = transcriber.wait_until_ready(timeout=120.0)
    print(f"[self-test] Whisper '{getattr(transcriber, 'active_model_size', '?')}' ready={ready} "
          f"in {time.perf_counter() - t0:.2f}s", flush=True)
    ok = ok and ready
    for path in wav_paths:
        result = transcriber.transcribe(path)
        print(f"[self-test] {Path(path).name}: \"{result.text}\" ({result.processing_time_sec:.2f}s)", flush=True)
        ok = ok and bool(result.text.strip())
    transcriber.close()
    print(f"[self-test] {'PASSED' if ok else 'FAILED'}", flush=True)
    return ok


def main(run_loop: bool = True) -> VoiceAssistantApp | None:
    # 0. Ensure NSApplication is initialized on macOS Main Thread first
    # This prevents Carbon TIS/TSM concurrency aborts when background threads start
    import AppKit
    _ = AppKit.NSApplication.sharedApplication()

    # 1. Data folder, settings file and log file
    paths = engine_config.data_paths()
    log_file = setup_file_logging(paths.log_dir)

    # Architecture and Environment Verification
    arch = platform.machine()
    python_ver = sys.version.split()[0]
    executable = sys.executable

    logger.info("Starting Utter%s...", " (app)" if engine_config.running_as_app() else " (from the project folder)")
    logger.info("Python interpreter: %s (%s)", executable, python_ver)
    logger.info("Host architecture: %s (Apple Silicon: %s)", arch, arch == "arm64")

    if arch != "arm64":
        logger.warning("Expected native arm64 architecture, but running on %s.", arch)

    # 2. Load Configuration (after the one-time move into the Utter data folder)
    for message in prepare_data_folder(paths):
        logger.info("[Data folder] %s", message)
    config = engine_config.load_user_config(paths)
    settings_file = paths.user_config if paths.user_config.is_file() else None
    logger.info("Settings: %s", settings_file or f"{paths.default_config} (built-in defaults)")
    logger.info("Log file: %s", log_file or "none (terminal only)")
    logger.info("Audio input device: %s", AudioCapture.get_input_device_name())
    logger.info(
        "Hotkeys: Command='%s', Dictation='%s' (%s), Cancel='%s'",
        config.hotkeys.command_mode,
        config.hotkeys.dictation_mode,
        f"tap to toggle, hold {config.hotkeys.hold_min_sec:.1f}s+ to talk"
        if config.hotkeys.hold_to_talk else "tap to toggle",
        config.hotkeys.cancel,
    )
    logger.info(
        "Timeouts: Command Silence=%.1fs (early check %.1fs), Command No-Speech=%.1fs, Dictation Silence=%.1fs",
        config.timeouts.command_silence_sec,
        config.timeouts.command_early_check_sec,
        config.timeouts.command_no_speech_sec,
        config.timeouts.dictation_silence_sec,
    )
    logger.info(
        "Whisper: configured model='%s', language='%s'",
        config.whisper.model_size,
        config.whisper.language,
    )
    logger.info(
        "Normalization: Enabled=%s, Target=%.1fdBFS, Max Gain=%.1fdB",
        config.normalization.enabled,
        config.normalization.target_peak_dbfs,
        config.normalization.max_gain_db,
    )

    # 3. macOS Permissions Audit
    permissions = get_all_permissions()
    report = format_permissions_report(permissions)
    print("\n" + report + "\n", flush=True)

    if "--permissions" in sys.argv or "--check" in sys.argv:
        return None
    if "--self-test" in sys.argv:
        global _exit_code
        _exit_code = 0 if self_test(config, sys.argv[sys.argv.index("--self-test") + 1:]) else 1
        return None

    # 4. One Utter at a time
    global _instance_lock
    if _instance_lock is None:
        _instance_lock = acquire_single_instance_lock(paths.data_dir)
        if _instance_lock is None:
            logger.error("Utter is already running (menu bar). Quit that one first.")
            try:
                import rumps
                rumps.alert("Utter is already running",
                            "Utter is already in your menu bar (maybe started from the terminal or the "
                            "Applications folder). Quit that one first, then open Utter again.")
            except Exception:
                pass
            return None

    # Initialize Engine and Interface
    session_manager = SessionManager(config=config)
    transcriber = MLXWhisperTranscriber(
        config=config.whisper,
        normalization_config=config.normalization,
    )
    app = VoiceAssistantApp(config=config, session_manager=session_manager, transcriber=transcriber)
    transcriber.on_ready = app.on_model_ready
    app.settings_file = settings_file
    app.log_file = log_file

    # 5. Start Global Hotkeys and Run App
    app.start_listeners()
    logger.info("Menu bar app initialized. Ready for voice sessions.")

    if not run_loop:
        return app

    app.run()
    return app


if __name__ == "__main__":
    if main() is None:
        # Command-line modes (--check, --self-test) or "already running": nothing to save, so
        # skip Python's exit clean-up, which can wait on CoreAudio the first time a new build
        # of Utter.app runs. (The menu bar app quits through macOS and never gets here.)
        logging.shutdown()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(_exit_code)
