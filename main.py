"""
Voice Assistant (macOS Apple Silicon) - Step 2 Entry Point
Runs local menu bar app with global hotkeys, Silero VAD, and MLX Whisper transcription.
"""

import logging
import os
import platform
import sys

# The app runs fully offline: never let Hugging Face fetch model weights at runtime.
# Models are downloaded deliberately with scripts/benchmark_whisper.py, which runs separately.
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from engine import AudioCapture, MLXWhisperTranscriber, SessionManager, load_config
from interface import VoiceAssistantApp, format_permissions_report, get_all_permissions

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("voice_assistant")


def main(run_loop: bool = True) -> VoiceAssistantApp | None:
    # 0. Ensure NSApplication is initialized on macOS Main Thread first
    # This prevents Carbon TIS/TSM concurrency aborts when background threads start
    import AppKit
    _ = AppKit.NSApplication.sharedApplication()

    # 1. Architecture and Environment Verification
    arch = platform.machine()
    python_ver = sys.version.split()[0]
    executable = sys.executable

    logger.info("Starting Voice Assistant...")
    logger.info("Python interpreter: %s (%s)", executable, python_ver)
    logger.info("Host architecture: %s (Apple Silicon: %s)", arch, arch == "arm64")

    if arch != "arm64":
        logger.warning("Expected native arm64 architecture, but running on %s.", arch)

    # 2. Load Configuration
    config = load_config()
    logger.info("Configuration loaded from config.toml.")
    logger.info("Audio input device: %s", AudioCapture.get_input_device_name())
    logger.info(
        "Hotkeys: Command='%s', Dictation='%s', Cancel='%s'",
        config.hotkeys.command_mode,
        config.hotkeys.dictation_mode,
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

    # 4. Initialize Engine and Interface
    session_manager = SessionManager(config=config)
    transcriber = MLXWhisperTranscriber(
        config=config.whisper,
        normalization_config=config.normalization,
    )
    app = VoiceAssistantApp(config=config, session_manager=session_manager, transcriber=transcriber)
    transcriber.on_ready = app.on_model_ready

    # 5. Start Global Hotkeys and Run App
    app.start_listeners()
    logger.info("Menu bar app initialized. Ready for voice sessions.")

    if not run_loop:
        return app

    app.run()
    return app


if __name__ == "__main__":
    main()
