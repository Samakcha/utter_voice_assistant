"""
Voice Assistant (macOS Apple Silicon) - Step 1 Entry Point
Runs local menu bar app with global hotkeys, Silero VAD, and mic capture auto-stops.
"""

import logging
import platform
import sys
from engine import SessionManager, load_config
from interface import VoiceAssistantApp, format_permissions_report, get_all_permissions

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("voice_assistant")


def main() -> None:
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
    logger.info(
        "Hotkeys: Command='%s', Dictation='%s', Cancel='%s'",
        config.hotkeys.command_mode,
        config.hotkeys.dictation_mode,
        config.hotkeys.cancel,
    )
    logger.info(
        "Timeouts: Command Silence=%.1fs, Command No-Speech=%.1fs, Dictation Silence=%.1fs",
        config.timeouts.command_silence_sec,
        config.timeouts.command_no_speech_sec,
        config.timeouts.dictation_silence_sec,
    )

    # 3. macOS Permissions Audit
    permissions = get_all_permissions()
    report = format_permissions_report(permissions)
    print("\n" + report + "\n", flush=True)

    if "--permissions" in sys.argv or "--check" in sys.argv:
        return

    # 4. Initialize Engine and Interface
    session_manager = SessionManager(config=config)
    app = VoiceAssistantApp(config=config, session_manager=session_manager)

    # 5. Start Global Hotkeys and Run App
    app.start_listeners()
    logger.info("Menu bar app initialized. Ready for voice sessions.")
    app.run()


if __name__ == "__main__":
    main()
