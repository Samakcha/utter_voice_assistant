"""Menu bar application for Voice Assistant using rumps."""

import logging
from pathlib import Path
import subprocess
import sys
from typing import Optional
import AppKit
import rumps

from engine.config import AppConfig
from engine.models import EndReason, SessionMode, SessionResult
from engine.session import SessionManager
from .hotkeys import HotkeyManager, HotkeyRegistrationError
from .permissions import format_permissions_report, get_all_permissions, open_settings_pane

logger = logging.getLogger(__name__)


def run_on_main_thread(fn, *args):
    """Schedule a callable to run on macOS main UI thread."""
    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(lambda: fn(*args))


class VoiceAssistantApp(rumps.App):
    """
    macOS Menu Bar interface.
    Interacts with the engine via SessionManager.
    Strictly separated: engine knows nothing about this class.
    """

    TITLE_IDLE = "🎙️ Voice Assistant"
    TITLE_COMMAND = "🔴 Listening (Command)..."
    TITLE_DICTATION = "🔵 Listening (Dictation)..."
    TITLE_STOPPED = "⚪ [Stopped]"

    def __init__(self, config: AppConfig, session_manager: SessionManager):
        super().__init__(name="Voice Assistant", title=self.TITLE_IDLE, quit_button=None)
        self.config = config
        self.session_manager = session_manager
        self.is_running = True

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
        self.status_item = rumps.MenuItem("Status: Running (Mic closed)", callback=None)
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
            self.title = self.TITLE_IDLE
            self.toggle_item.title = "Stop Assistant"
            self.status_item.title = "Status: Running (Mic closed)"
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
        def _update_ui():
            if self.is_running:
                self.title = self.TITLE_IDLE
                self.status_item.title = "Status: Running (Mic closed)"
            else:
                self.title = self.TITLE_STOPPED
                self.status_item.title = "Status: Stopped (Inactive)"

        run_on_main_thread(_update_ui)

        # Log session report to console
        audio_name = result.audio_path.name if result.audio_path else "None"
        speech_text = "Yes" if result.speech_detected else "No"
        print(
            f"\n"
            f"┌─────────────────────────────────────────────────────────\n"
            f"│ [Session Result]\n"
            f"│ Mode:          {result.mode.value.capitalize()}\n"
            f"│ End Reason:    {result.end_reason.value}\n"
            f"│ Duration:      {result.duration_sec:.2f} s\n"
            f"│ Speech Heard:  {speech_text}\n"
            f"│ Audio File:    {audio_name}\n"
            f"└─────────────────────────────────────────────────────────\n",
            flush=True,
        )

        # Show notification (unless it was a quiet cancel with no speech)
        if not result.was_quiet_cancel:
            try:
                rumps.notification(
                    title="Voice Assistant",
                    subtitle=f"{result.mode.value.capitalize()} ended ({result.end_reason.value})",
                    message=f"Duration: {result.duration_sec:.1f}s | Saved: {audio_name}",
                )
            except Exception as e:
                logger.debug("Could not display notification: %s", e)

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
            # Open the first missing permission pane
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
        rumps.quit_application()
