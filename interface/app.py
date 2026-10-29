"""Menu bar application for Voice Assistant using rumps."""

import logging
from pathlib import Path
import subprocess
import sys
from typing import Optional
import AppKit
import rumps

from engine.config import AppConfig
from engine.models import EndReason, SessionMode, SessionResult, TranscriptionResult
from engine.session import SessionManager
from engine.transcriber import BaseTranscriber
from .hotkeys import HotkeyManager, HotkeyRegistrationError
from .permissions import format_permissions_report, get_all_permissions, open_settings_pane

logger = logging.getLogger(__name__)


def run_on_main_thread(fn, *args):
    """Schedule a callable to run on macOS main UI thread."""
    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(lambda: fn(*args))


class VoiceAssistantApp(rumps.App):
    """
    macOS Menu Bar interface.
    Interacts with the engine via SessionManager and BaseTranscriber.
    Strictly separated: engine knows nothing about this class.
    """

    TITLE_LOADING = "⏳ Loading model..."
    TITLE_IDLE = "🎙️ Voice Assistant"
    TITLE_COMMAND = "🔴 Listening (Command)..."
    TITLE_DICTATION = "🔵 Listening (Dictation)..."
    TITLE_TRANSCRIBING = "⏳ Transcribing..."
    TITLE_STOPPED = "⚪ [Stopped]"

    def __init__(
        self,
        config: AppConfig,
        session_manager: SessionManager,
        transcriber: Optional[BaseTranscriber] = None,
    ):
        initial_title = self.TITLE_LOADING if transcriber is not None else self.TITLE_IDLE
        super().__init__(name="Voice Assistant", title=initial_title, quit_button=None)

        self.config = config
        self.session_manager = session_manager
        self.transcriber = transcriber
        self.is_running = True
        self.model_ready = False if transcriber is not None else True

        # Connect session lifecycle callbacks
        self.session_manager.on_session_start = self._on_session_start
        self.session_manager.on_session_end = self._on_session_end

        # Initialize hotkey manager
        self.hotkey_manager = HotkeyManager(
            command_hotkey_str=config.hotkeys.command_mode,
            dictation_hotkey_str=config.hotkeys.dictation_mode,
            cancel_hotkey_str=config.hotkeys.cancel,
            on_command=self._on_command_hotkey,
            on_dictation=self._on_dictation_hotkey,
            on_cancel=self._on_cancel_hotkey,
            is_session_active=lambda: self.session_manager.is_listening,
        )

        # Build menu items
        status_text = "Status: Loading Whisper model..." if not self.model_ready else "Status: Ready (Mic closed)"
        self.status_item = rumps.MenuItem(status_text, callback=None)
        self.toggle_item = rumps.MenuItem("Stop Assistant", callback=self.toggle_service)
        self.permissions_item = rumps.MenuItem("Check Permissions", callback=self.show_permissions_dialog)
        self.recordings_item = rumps.MenuItem("Open Recordings Folder", callback=self.open_recordings_folder)
        self.quit_item = rumps.MenuItem("Quit", callback=self.quit_app)

        self.menu = [
            self.status_item,
            None,  # Separator
            self.toggle_item,
            self.permissions_item,
            self.recordings_item,
            None,  # Separator
            self.quit_item,
        ]

    def on_model_ready(self) -> None:
        """Called when transcriber completes loading and warmup on worker thread."""
        self.model_ready = True

        def _update_ui():
            if self.is_running and not self.session_manager.is_listening:
                self.title = self.TITLE_IDLE
                self.status_item.title = "Status: Ready (Mic closed)"

        run_on_main_thread(_update_ui)
        logger.info("Voice Assistant is ready for voice sessions.")

    def start_listeners(self) -> None:
        """Start hotkey listener."""
        try:
            self.hotkey_manager.start()
        except HotkeyRegistrationError as e:
            logger.error("Hotkey registration error:\n%s", e)
            rumps.alert("Hotkey Registration Error", str(e))
        except Exception as e:
            logger.error("Failed to start hotkey listener: %s", e)

    def toggle_service(self, sender: rumps.MenuItem) -> None:
        """Start or Stop the voice assistant."""
        self.is_running = not self.is_running
        if self.is_running:
            self.hotkey_manager.enabled = True
            self.title = self.TITLE_IDLE if self.model_ready else self.TITLE_LOADING
            self.toggle_item.title = "Stop Assistant"
            self.status_item.title = "Status: Ready (Mic closed)" if self.model_ready else "Status: Loading model..."
            logger.info("Voice assistant resumed.")
        else:
            # When stopped, microphone must not be open at all
            self.hotkey_manager.enabled = False
            if self.session_manager.is_listening:
                self.session_manager.stop_session(EndReason.STOPPED)
            self.title = self.TITLE_STOPPED
            self.toggle_item.title = "Start Assistant"
            self.status_item.title = "Status: Stopped (Inactive)"
            logger.info("Voice assistant stopped.")

    def _on_command_hotkey(self) -> None:
        """Handle command mode hotkey trigger."""
        if not self.is_running:
            return
        self.session_manager.start_session(SessionMode.COMMAND)

    def _on_dictation_hotkey(self) -> None:
        """Handle dictation mode hotkey trigger."""
        if not self.is_running:
            return
        self.session_manager.start_session(SessionMode.DICTATION)

    def _on_cancel_hotkey(self) -> None:
        """Handle Esc cancellation."""
        self.session_manager.cancel_session(EndReason.ESC)

    def _on_session_start(self, mode: SessionMode) -> None:
        """Callback from engine when listening session begins."""
        def _update_ui():
            if mode == SessionMode.COMMAND:
                self.title = self.TITLE_COMMAND
                self.status_item.title = "Status: Listening for command..."
            else:
                self.title = self.TITLE_DICTATION
                self.status_item.title = "Status: Listening for dictation..."

        run_on_main_thread(_update_ui)

    def _on_session_end(self, result: SessionResult) -> None:
        """Callback from engine when listening session ends."""
        # 1. If NO speech was detected (or canceled), do NOT transcribe (avoids hallucinations)
        if not result.speech_detected or not result.audio_path or not result.audio_path.exists():
            def _reset_idle():
                if self.is_running:
                    self.title = self.TITLE_IDLE
                    self.status_item.title = "Status: Ready (Mic closed)"
                else:
                    self.title = self.TITLE_STOPPED
                    self.status_item.title = "Status: Stopped (Inactive)"

            try:
                run_on_main_thread(_reset_idle)
            except Exception as e:
                logger.error("Failed to reset UI on session end: %s", e, exc_info=True)

            logger.info(
                "Session ended with no speech (%s); skipping transcription.",
                result.end_reason.value,
            )
            return

        # 2. Speech was detected: switch menu bar to Transcribing... state
        def _set_transcribing_ui():
            self.title = self.TITLE_TRANSCRIBING
            self.status_item.title = "Status: Transcribing audio..."

        try:
            run_on_main_thread(_set_transcribing_ui)
        except Exception as e:
            logger.error("Failed to set transcribing UI: %s", e, exc_info=True)

        # 3. Asynchronously transcribe on the dedicated background worker thread
        if self.transcriber is not None:
            self.transcriber.transcribe_async(
                audio=result.audio_path,
                callback=lambda tx_res: self._on_transcription_success(tx_res, result),
                on_error=lambda err: self._on_transcription_error(err, result),
            )
        else:
            logger.warning("No transcriber configured; returning to idle.")
            self._restore_idle_ui()

    def _on_transcription_success(
        self,
        tx_result: TranscriptionResult,
        session_result: SessionResult,
    ) -> None:
        """Invoked when transcription completes on worker thread."""
        self._restore_idle_ui()

        # Print formatted report to console
        print(
            f"\n"
            f"┌─────────────────────────────────────────────────────────\n"
            f"│ [Heard] \"{tx_result.text}\"\n"
            f"│ Mode:          {session_result.mode.value.capitalize()}\n"
            f"│ End Reason:    {session_result.end_reason.value}\n"
            f"│ Audio Length:  {tx_result.audio_duration_sec:.2f} s\n"
            f"│ Latency:       {tx_result.processing_time_sec:.3f} s (RTF: {tx_result.real_time_factor:.2f}x)\n"
            f"│ Audio File:    {session_result.audio_path.name if session_result.audio_path else 'None'}\n"
            f"└─────────────────────────────────────────────────────────\n",
            flush=True,
        )

        logger.info(
            "[Heard] \"%s\" | Audio: %.2fs | Latency: %.3fs (RTF: %.2fx)",
            tx_result.text,
            tx_result.audio_duration_sec,
            tx_result.processing_time_sec,
            tx_result.real_time_factor,
        )

        # Show notification with Heard text
        try:
            rumps.notification(
                title="Voice Assistant",
                subtitle="Heard",
                message=f"\"{tx_result.text}\" ({tx_result.processing_time_sec:.2f}s)",
            )
        except Exception as e:
            logger.debug("Could not display notification: %s", e)

    def _on_transcription_error(
        self,
        error: Exception,
        session_result: SessionResult,
    ) -> None:
        """Invoked if transcription encounters an error."""
        self._restore_idle_ui()
        logger.error("Transcription error on session %s: %s", session_result.audio_path, error, exc_info=True)

        try:
            rumps.notification(
                title="Voice Assistant",
                subtitle="Transcription Error",
                message=str(error),
            )
        except Exception as e:
            logger.debug("Could not display notification: %s", e)

    def _restore_idle_ui(self) -> None:
        """Helper to reset title and status item to idle."""
        def _update_ui():
            if self.is_running:
                self.title = self.TITLE_IDLE
                self.status_item.title = "Status: Ready (Mic closed)"
            else:
                self.title = self.TITLE_STOPPED
                self.status_item.title = "Status: Stopped (Inactive)"

        try:
            run_on_main_thread(_update_ui)
        except Exception as e:
            logger.error("Failed to restore idle UI: %s", e, exc_info=True)

    def show_permissions_dialog(self, _sender=None) -> None:
        """Display status of required permissions and provide deep links to fix them."""
        permissions = get_all_permissions()
        report = format_permissions_report(permissions)
        missing = [p for p in permissions.values() if not p.granted]

        if not missing:
            rumps.alert("Permissions OK", "All required permissions are granted:\n- Microphone\n- Accessibility\n- Input Monitoring")
            return

        choice = rumps.alert(
            title="Missing macOS Permissions",
            message=report,
            ok="Open System Settings",
            cancel="Dismiss",
        )
        if choice == 1:
            open_settings_pane(missing[0].name.lower())

    def open_recordings_folder(self, _sender=None) -> None:
        """Open the recordings directory in Finder."""
        rec_dir = self.config.storage.recordings_dir
        rec_dir.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(["open", str(rec_dir)], check=False)
        except Exception as e:
            logger.error("Could not open recordings directory: %s", e)

    def quit_app(self, _sender=None) -> None:
        """Clean shutdown of engine and interface."""
        logger.info("Quitting Voice Assistant...")
        self.is_running = False
        self.hotkey_manager.stop()
        if self.session_manager.is_listening:
            self.session_manager.stop_session(EndReason.STOPPED)
        if self.transcriber is not None and hasattr(self.transcriber, "close"):
            try:
                self.transcriber.close()
            except Exception as e:
                logger.debug("Error closing transcriber: %s", e)
        rumps.quit_application()
