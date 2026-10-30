"""Menu bar application for Voice Assistant using rumps."""

import logging
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Dict, List, Optional
import AppKit
import rumps

from engine.actions import ActionResult, execute_action_async
from engine.chunk_joiner import join_transcription_chunks
from engine.commands import CommandMatch, match_command
from engine.config import AppConfig
from engine.insertion import format_dictation_insertion
from engine.models import DictationChunk, EndReason, SessionMode, SessionResult, TranscriptionResult
from engine.session import SessionManager
from engine.stop_phrases import detect_stop_phrase, validate_stop_phrases
from engine.transcriber import BaseTranscriber
from .clipboard import paste_and_restore
from .hotkeys import HotkeyManager, HotkeyRegistrationError
from .overlay import VoiceAssistantOverlay
from .permissions import format_permissions_report, get_all_permissions, open_settings_pane

logger = logging.getLogger(__name__)


def run_on_main_thread(fn, *args):
    """Run callable on macOS main thread. Executes immediately if already on main thread."""
    if AppKit.NSThread.isMainThread():
        fn(*args)
    else:
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

        # Validate configured stop phrases at startup
        validate_stop_phrases(config.dictation.stop_phrases)

        # Dictation session state tracking
        self._dictation_session_id: Optional[str] = None
        self._dictation_chunks: Dict[int, str] = {}
        self._dictation_latencies: Dict[int, float] = {}
        self._dictation_pending_chunks: int = 0
        self._dictation_session_result: Optional[SessionResult] = None
        self._dictation_session_ended: bool = False
        self._dictation_lock = threading.Lock()

        # Connect session lifecycle callbacks
        self.session_manager.on_session_start = self._on_session_start
        self.session_manager.on_session_end = self._on_session_end
        self.session_manager.on_dictation_chunk = self._on_dictation_chunk

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
        with self._dictation_lock:
            self._dictation_session_id = None
            self._dictation_chunks.clear()
            self._dictation_pending_chunks = 0
            self._dictation_session_ended = False
        if self.overlay is not None:
            self.overlay.hide()
        self.session_manager.cancel_session(EndReason.ESC)

    def _on_session_start(self, mode: SessionMode) -> None:
        """Callback from engine when listening session begins."""
        if mode == SessionMode.DICTATION:
            with self._dictation_lock:
                self._dictation_session_id = self.session_manager.current_session_id
                self._dictation_chunks.clear()
                self._dictation_latencies.clear()
                self._dictation_pending_chunks = 0
                self._dictation_session_result = None
                self._dictation_session_ended = False

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

    def _on_dictation_chunk(self, chunk: DictationChunk) -> None:
        """Callback from engine when a dictation speech chunk is ready for transcription."""
        with self._dictation_lock:
            if chunk.session_id != self._dictation_session_id or self._dictation_session_id is None:
                logger.debug("Dropping chunk %d for inactive/stale dictation session", chunk.chunk_index)
                return
            self._dictation_pending_chunks += 1
            session_id = chunk.session_id
            chunk_idx = chunk.chunk_index
            is_final = chunk.is_final

            # Context prompt from previous chunk
            prompt = None
            if self.config.dictation.context_prompt and chunk_idx > 0:
                completed = [self._dictation_chunks[i] for i in sorted(self._dictation_chunks.keys()) if i < chunk_idx]
                if completed:
                    prompt = completed[-1]

        if self.transcriber is None:
            logger.warning("No transcriber available for dictation chunk %d", chunk_idx)
            self._handle_dictation_chunk_result(
                session_id,
                chunk_idx,
                TranscriptionResult(text="", language="en", audio_duration_sec=0.0, processing_time_sec=0.0),
                is_final,
            )
            return

        def _on_success(tx_res: TranscriptionResult):
            self._handle_dictation_chunk_result(session_id, chunk_idx, tx_res, is_final)

        def _on_err(err: Exception):
            logger.error("Error transcribing dictation chunk %d: %s", chunk_idx, err)
            self._handle_dictation_chunk_result(
                session_id,
                chunk_idx,
                TranscriptionResult(text="", language="en", audio_duration_sec=0.0, processing_time_sec=0.0),
                is_final,
            )

        self.transcriber.transcribe_async(
            audio=chunk.audio,
            callback=_on_success,
            on_error=_on_err,
            prompt=prompt,
            normalize=self.config.normalization.enabled,
        )

    def _handle_dictation_chunk_result(
        self,
        session_id: str,
        chunk_index: int,
        tx_result: TranscriptionResult,
        is_final: bool,
    ) -> None:
        """Process completed transcription of a dictation chunk."""
        should_finalize = False
        stop_phrase_detected = False
        with self._dictation_lock:
            if session_id != self._dictation_session_id or self._dictation_session_id is None:
                logger.debug("Dropping chunk result for stale session %s", session_id)
                return

            self._dictation_pending_chunks = max(0, self._dictation_pending_chunks - 1)
            self._dictation_chunks[chunk_index] = tx_result.text
            self._dictation_latencies[chunk_index] = tx_result.processing_time_sec

            # Check if accumulated text ends with a stop phrase
            ordered_texts = [self._dictation_chunks[i] for i in sorted(self._dictation_chunks.keys())]
            joined_so_far = join_transcription_chunks(ordered_texts)
            match = detect_stop_phrase(
                joined_so_far,
                self.config.dictation.stop_phrases,
                self.config.dictation.fuzzy_tolerance,
            )
            if match is not None and not self._dictation_session_ended:
                stop_phrase_detected = True

            if self._dictation_session_ended and self._dictation_pending_chunks == 0:
                should_finalize = True

        if stop_phrase_detected:
            logger.info("Stop phrase '%s' detected during active dictation; stopping session.", match.matched_phrase)
            self.session_manager.stop_session(EndReason.STOP_PHRASE)

        if should_finalize:
            self._finalize_dictation_insertion(session_id)

    def _on_session_end(self, result: SessionResult) -> None:
        """Callback from engine when listening session ends."""
        # 1. If canceled by Esc:
        if result.end_reason == EndReason.ESC:
            if result.mode == SessionMode.DICTATION:
                with self._dictation_lock:
                    self._dictation_session_id = None
                    self._dictation_chunks.clear()
                    self._dictation_pending_chunks = 0
            if self.overlay is not None:
                self.overlay.hide()
            self._restore_idle_ui()
            logger.info("Session canceled by Esc; skipping transcription/insertion.")
            return

        # 2. DICTATION MODE
        if result.mode == SessionMode.DICTATION:
            if not result.speech_detected and not self._dictation_chunks:
                if self.overlay is not None:
                    self.overlay.show_dictation_empty()
                self._restore_idle_ui()
                logger.info("Dictation ended with no speech; pasting nothing.")
                return

            # Change 5: Show "Transcribing..." in overlay until all chunks finish and paste happens
            if self.overlay is not None:
                self.overlay.show_transcribing()

            def _set_transcribing_ui():
                self.title = self.TITLE_TRANSCRIBING
                self.status_item.title = "Status: Transcribing final dictation..."

            run_on_main_thread(_set_transcribing_ui)

            should_finalize = False
            with self._dictation_lock:
                self._dictation_session_result = result
                self._dictation_session_ended = True
                if self._dictation_pending_chunks == 0:
                    should_finalize = True

            if should_finalize:
                self._finalize_dictation_insertion(result.session_id)
            return

        # 3. COMMAND MODE: verify speech was detected and WAV exists
        if not result.speech_detected or not result.audio_path or not result.audio_path.exists():
            if self.overlay is not None:
                self.overlay.hide()
            self._restore_idle_ui()
            logger.info(
                "Command session ended with no speech (%s) | Levels: Peak=%.1f dBFS, RMS=%.1f dBFS | skipping transcription.",
                result.end_reason.value,
                result.peak_dbfs,
                result.rms_dbfs,
            )
            return

        if self.overlay is not None:
            self.overlay.show_transcribing()

        def _set_transcribing_ui():
            self.title = self.TITLE_TRANSCRIBING
            self.status_item.title = "Status: Transcribing audio..."

        try:
            run_on_main_thread(_set_transcribing_ui)
        except Exception as e:
            logger.error("Failed to set transcribing UI: %s", e, exc_info=True)

        # Asynchronously transcribe on the dedicated background worker thread
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

    def _finalize_dictation_insertion(self, session_id: str) -> None:
        """Join all transcribed chunks, strip stop phrase, format text, and paste."""
        with self._dictation_lock:
            if session_id != self._dictation_session_id:
                return
            chunks_list = [self._dictation_chunks[i] for i in sorted(self._dictation_chunks.keys())]
            latencies = [self._dictation_latencies.get(i, 0.0) for i in sorted(self._dictation_chunks.keys())]
            session_result = self._dictation_session_result
            # Invalidate session_id so late events are discarded
            self._dictation_session_id = None

        raw_joined = join_transcription_chunks(chunks_list)

        # Final stop phrase check and cleanup
        match = detect_stop_phrase(
            raw_joined,
            self.config.dictation.stop_phrases,
            self.config.dictation.fuzzy_tolerance,
        )
        if match is not None:
            final_text = match.cleaned_text
            matched_phrase_str = f'"{match.matched_phrase}"'
            end_reason_str = EndReason.STOP_PHRASE.value
        else:
            final_text = raw_joined
            matched_phrase_str = "None"
            end_reason_str = session_result.end_reason.value if session_result else "unknown"

        formatted = format_dictation_insertion(
            final_text,
            trailing_space=self.config.dictation.trailing_space,
        )

        if not formatted.should_insert:
            self._restore_idle_ui()
            if self.overlay is not None:
                self.overlay.show_dictation_empty()
            logger.info("[Dictation Complete] No speech or empty text; nothing to insert.")
            return

        t_paste_0 = time.perf_counter()

        def _on_paste_done(restored: bool):
            t_paste_1 = time.perf_counter()
            paste_duration_ms = (t_paste_1 - t_paste_0) * 1000.0
            self._restore_idle_ui()
            if self.overlay is not None:
                self.overlay.show_dictation_inserted(formatted.word_count)

            lat_str = ", ".join(f"{l:.3f}s" for l in latencies) if latencies else "0.0s"
            logger.info(
                "[Dictation Complete] Chunks: %d | Transcribe Latencies: [%s] | End Reason: %s (%s) | Words: %d | Paste Duration: %.1fms",
                len(chunks_list),
                lat_str,
                end_reason_str,
                matched_phrase_str,
                formatted.word_count,
                paste_duration_ms,
            )
            print(
                f"\n"
                f"┌─────────────────────────────────────────────────────────\n"
                f"│ [Dictation Inserted] \"{formatted.text}\"\n"
                f"│ Words:            {formatted.word_count}\n"
                f"│ Chunks:           {len(chunks_list)} (Transcribe times: [{lat_str}])\n"
                f"│ End Reason:       {end_reason_str} ({matched_phrase_str})\n"
                f"│ Paste Duration:   {paste_duration_ms:.1f} ms\n"
                f"│ Audio File:       {session_result.audio_path.name if session_result and session_result.audio_path else 'None'}\n"
                f"└─────────────────────────────────────────────────────────\n",
                flush=True,
            )

        run_on_main_thread(
            lambda: paste_and_restore(
                text=formatted.text,
                config=self.config.dictation,
                on_complete=_on_paste_done,
            )
        )

    def _on_transcription_success(
        self,
        tx_result: TranscriptionResult,
        session_result: SessionResult,
    ) -> None:
        """Invoked when command mode transcription completes on worker thread."""
        self._restore_idle_ui()

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
