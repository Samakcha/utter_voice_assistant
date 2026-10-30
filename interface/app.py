"""Menu bar application for Voice Assistant using rumps."""

import logging
from pathlib import Path
import subprocess
import sys
import time
from typing import Optional
import AppKit
import rumps

from engine.actions import ActionResult, execute_action_async
from engine.commands import CommandMatch, match_command
from engine.config import AppConfig
from engine.models import EndReason, SessionMode, SessionResult, TranscriptionResult
from engine.session import SessionManager
from engine.transcriber import BaseTranscriber
from .hotkeys import HotkeyManager, HotkeyRegistrationError
from .overlay import VoiceAssistantOverlay
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
        self.overlay: Optional[VoiceAssistantOverlay] = None
        try:
            self.overlay = VoiceAssistantOverlay()
        except Exception as e:
            logger.warning("Could not initialize overlay: %s", e)

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
        if self.overlay is not None:
            self.overlay.hide()
        self.session_manager.cancel_session(EndReason.ESC)

    def _on_session_start(self, mode: SessionMode) -> None:
        """Callback from engine when listening session begins."""
        if self.overlay is not None:
            self.overlay.show_listening(mode.value)

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
        # 1. If NO speech was detected or canceled by Esc, do NOT transcribe
        if result.end_reason == EndReason.ESC or not result.speech_detected or not result.audio_path or not result.audio_path.exists():
            if self.overlay is not None:
                self.overlay.hide()

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
                "Session ended with no speech (%s) | Levels: Peak=%.1f dBFS, RMS=%.1f dBFS | skipping transcription.",
                result.end_reason.value,
                result.peak_dbfs,
                result.rms_dbfs,
            )
            return

        # 2. Speech was detected: switch overlay and menu bar to Transcribing... state
        if self.overlay is not None:
            self.overlay.show_transcribing()

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
            if self.overlay is not None:
                self.overlay.hide()
            self._restore_idle_ui()

    def _on_transcription_success(
        self,
        tx_result: TranscriptionResult,
        session_result: SessionResult,
    ) -> None:
        """Invoked when transcription completes on worker thread."""
        self._restore_idle_ui()

        # Handle Dictation Mode (Step 3: dictation never triggers the rule layer)
        if session_result.mode == SessionMode.DICTATION:
            if self.overlay is not None:
                self.overlay.hide()

            gain_str = f" (+{tx_result.applied_gain_db:.1f} dB gain applied)" if tx_result.applied_gain_db > 0.05 else ""
            print(
                f"\n"
                f"┌─────────────────────────────────────────────────────────\n"
                f"│ [Dictation Heard] \"{tx_result.text}\"\n"
                f"│ Mode:          Dictation\n"
                f"│ End Reason:    {session_result.end_reason.value}\n"
                f"│ Audio Length:  {tx_result.audio_duration_sec:.2f} s\n"
                f"│ Audio Levels:  Peak: {session_result.peak_dbfs:.1f} dBFS | RMS: {session_result.rms_dbfs:.1f} dBFS{gain_str}\n"
                f"│ Input Device:  {session_result.input_device}\n"
                f"│ Latency:       {tx_result.processing_time_sec:.3f} s (RTF: {tx_result.real_time_factor:.2f}x)\n"
                f"│ Audio File:    {session_result.audio_path.name if session_result.audio_path else 'None'}\n"
                f"└─────────────────────────────────────────────────────────\n",
                flush=True,
            )
            try:
                rumps.notification(
                    title="Voice Assistant",
                    subtitle="Dictation Heard",
                    message=f"\"{tx_result.text}\"",
                )
            except Exception as e:
                logger.debug("Could not display notification: %s", e)
            return

        # Handle Command Mode: Evaluate rule layer
        t_rule_0 = time.perf_counter()
        match = match_command(tx_result.text)
        t_rule_1 = time.perf_counter()
        rule_match_time_sec = t_rule_1 - t_rule_0

        silence_wait_sec = (
            session_result.trailing_silence_sec
            if session_result.trailing_silence_sec > 0.0
            else self.config.timeouts.command_silence_sec
        )

        # Case A: No recognized rule
        if match is None:
            if self.overlay is not None:
                self.overlay.show_unrecognized(tx_result.text)

            decision_to_result_sec = (
                (time.monotonic() - session_result.auto_stop_time)
                if session_result.auto_stop_time > 0.0
                else (tx_result.processing_time_sec + rule_match_time_sec)
            )
            speech_to_result_sec = silence_wait_sec + decision_to_result_sec

            print(
                f"\n"
                f"┌─────────────────────────────────────────────────────────\n"
                f"│ [Command Unrecognized] No rule matched\n"
                f"│ Utterance:        \"{tx_result.text}\"\n"
                f"│ Audio File:       {session_result.audio_path.name if session_result.audio_path else 'None'}\n"
                f"├─────────────────────────────────────────────────────────\n"
                f"│ Latency Breakdown:\n"
                f"│ • Silence Wait:   {silence_wait_sec:.3f} s (VAD trailing silence)\n"
                f"│ • Transcription:  {tx_result.processing_time_sec:.3f} s (RTF: {tx_result.real_time_factor:.2f}x)\n"
                f"│ • Rule Matching:  {rule_match_time_sec:.4f} s\n"
                f"│ ────────────────────────────────────────────────────────\n"
                f"│ Total (Decision -> Result):   {decision_to_result_sec:.3f} s\n"
                f"│ Total (Speech End -> Result): {speech_to_result_sec:.3f} s\n"
                f"└─────────────────────────────────────────────────────────\n",
                flush=True,
            )

            logger.info(
                "[Command Unrecognized] \"%s\" | Silence: %.2fs | Tx: %.3fs | Rule: %.4fs | Total: %.3fs",
                tx_result.text,
                silence_wait_sec,
                tx_result.processing_time_sec,
                rule_match_time_sec,
                decision_to_result_sec,
            )

            try:
                rumps.notification(
                    title="Voice Assistant",
                    subtitle="Command Unrecognized",
                    message=f'Heard: "{tx_result.text}" (Nothing recognized)',
                )
            except Exception as e:
                logger.debug("Could not display notification: %s", e)
            return

        # Case B: Command matched (e.g. "open_notes")
        if self.overlay is not None:
            self.overlay.show_action_start(tx_result.text, "Opening Notes...")

        # Run action asynchronously on worker thread with timeout (never blocks UI)
        def _on_action_done(act_res: ActionResult):
            run_on_main_thread(
                lambda: self._handle_action_result(
                    act_res,
                    match,
                    tx_result,
                    session_result,
                    rule_match_time_sec,
                )
            )

        execute_action_async(
            action_id=match.action_id,
            callback=_on_action_done,
            timeout_sec=5.0,
        )

    def _handle_action_result(
        self,
        act_res: ActionResult,
        match: CommandMatch,
        tx_result: TranscriptionResult,
        session_result: SessionResult,
        rule_match_time_sec: float,
    ) -> None:
        """Handle completion of an action on main thread."""
        if act_res.success:
            if self.overlay is not None:
                self.overlay.show_action_success(tx_result.text, act_res.message)
        else:
            if self.overlay is not None:
                self.overlay.show_error(act_res.error or act_res.message)

        # Calculate exact latency breakdown
        silence_wait_sec = (
            session_result.trailing_silence_sec
            if session_result.trailing_silence_sec > 0.0
            else self.config.timeouts.command_silence_sec
        )
        decision_to_notes_sec = (
            (time.monotonic() - session_result.auto_stop_time)
            if session_result.auto_stop_time > 0.0
            else (tx_result.processing_time_sec + rule_match_time_sec + act_res.execution_time_sec)
        )
        speech_to_notes_sec = silence_wait_sec + decision_to_notes_sec

        status_str = "SUCCESS" if act_res.success else f"FAILED ({act_res.error})"
        print(
            f"\n"
            f"┌─────────────────────────────────────────────────────────\n"
            f"│ [Command Executed] \"{match.action_id}\" -> {status_str}\n"
            f"│ Utterance:        \"{tx_result.text}\"\n"
            f"│ Action Message:   {act_res.message}\n"
            f"│ Audio File:       {session_result.audio_path.name if session_result.audio_path else 'None'}\n"
            f"├─────────────────────────────────────────────────────────\n"
            f"│ Latency Breakdown:\n"
            f"│ • Silence Wait:   {silence_wait_sec:.3f} s (VAD trailing silence)\n"
            f"│ • Transcription:  {tx_result.processing_time_sec:.3f} s (MLX Whisper '{self.config.whisper.model_size}')\n"
            f"│ • Rule Matching:  {rule_match_time_sec:.4f} s\n"
            f"│ • Action Exec:    {act_res.execution_time_sec:.4f} s (`open -a Notes`)\n"
            f"│ ────────────────────────────────────────────────────────\n"
            f"│ Total (Decision -> Notes launched):   {decision_to_notes_sec:.3f} s\n"
            f"│ Total (Speech End -> Notes launched): {speech_to_notes_sec:.3f} s\n"
            f"└─────────────────────────────────────────────────────────\n",
            flush=True,
        )

        logger.info(
            "[Command: %s] %s | Silence: %.2fs | Tx: %.3fs | Rule: %.4fs | Action: %.3fs | Total: %.3fs",
            match.action_id,
            "OK" if act_res.success else "FAIL",
            silence_wait_sec,
            tx_result.processing_time_sec,
            rule_match_time_sec,
            act_res.execution_time_sec,
            decision_to_notes_sec,
        )

        # Fallback system notification
        try:
            subtitle = "Command Success" if act_res.success else "Command Failed"
            rumps.notification(
                title="Voice Assistant",
                subtitle=subtitle,
                message=f'Heard: "{tx_result.text}" → {act_res.message}',
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
        if self.overlay is not None:
            self.overlay.hide()
        self.hotkey_manager.stop()
        if self.session_manager.is_listening:
            self.session_manager.stop_session(EndReason.STOPPED)
        if self.transcriber is not None and hasattr(self.transcriber, "close"):
            try:
                self.transcriber.close()
            except Exception as e:
                logger.debug("Error closing transcriber: %s", e)
        rumps.quit_application()
