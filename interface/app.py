"""Menu bar application for Voice Assistant using rumps."""

import logging
from pathlib import Path
import re
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
from engine.notes_writer import (
    AppleScriptNotesWriter,
    BaseNotesWriter,
    format_note_html,
    generate_note_title,
    save_note_failure_backup,
)
from engine.session import SessionManager
from engine.stop_phrases import detect_stop_phrase, validate_stop_phrases
from engine.transcriber import BaseTranscriber
from .clipboard import get_general_pasteboard, paste_and_restore
from .hotkeys import HotkeyManager, HotkeyRegistrationError
from .overlay import VoiceAssistantOverlay
from .permissions import format_permissions_report, get_all_permissions, open_settings_pane
from .whatsapp_control import (
    WhatsAppActionResult,
    check_whatsapp_frontmost_dual,
    open_whatsapp_async,
    search_whatsapp_async,
)

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
    TITLE_NOTE = "📝 Taking a note..."
    TITLE_TRANSCRIBING = "⏳ Transcribing..."
    TITLE_STOPPED = "⚪ [Stopped]"

    def __init__(
        self,
        config: AppConfig,
        session_manager: SessionManager,
        transcriber: Optional[BaseTranscriber] = None,
        notes_writer: Optional[BaseNotesWriter] = None,
    ):
        initial_title = self.TITLE_LOADING if transcriber is not None else self.TITLE_IDLE
        super().__init__(name="Voice Assistant", title=initial_title, quit_button=None)

        self.config = config
        self.session_manager = session_manager
        self.transcriber = transcriber
        self.notes_writer = notes_writer if notes_writer is not None else AppleScriptNotesWriter()
        self.is_running = True
        self.model_ready = False if transcriber is not None else True
        self.overlay: Optional[VoiceAssistantOverlay] = None
        try:
            self.overlay = VoiceAssistantOverlay()
        except Exception as e:
            logger.warning("Could not initialize overlay: %s", e)

        # Validate configured stop phrases at startup
        validate_stop_phrases(config.dictation.stop_phrases)
        validate_stop_phrases(config.notes.stop_phrases)

        # Dictation session state tracking
        self._dictation_session_id: Optional[str] = None
        self._dictation_chunks: Dict[int, str] = {}
        self._dictation_latencies: Dict[int, float] = {}
        self._dictation_pending_chunks: int = 0
        self._dictation_session_result: Optional[SessionResult] = None
        self._dictation_session_ended: bool = False
        self._dictation_lock = threading.Lock()

        # Note session state tracking
        self._note_session_id: Optional[str] = None
        self._note_chunks: Dict[int, str] = {}
        self._note_latencies: Dict[int, float] = {}
        self._note_pending_chunks: int = 0
        self._note_session_result: Optional[SessionResult] = None
        self._note_session_ended: bool = False
        self._note_lock = threading.Lock()
        self._cancel_pending_note_start: bool = False
        self._session_whatsapp_front: bool = False

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
        if self.session_manager.is_listening and self.session_manager.current_mode == SessionMode.NOTE:
            self.session_manager.stop_session(EndReason.HOTKEY)
            return
        self.session_manager.start_session(SessionMode.COMMAND)

    def _on_dictation_hotkey(self) -> None:
        """Handle dictation mode hotkey trigger."""
        if not self.is_running:
            return
        if self.session_manager.is_listening and self.session_manager.current_mode == SessionMode.NOTE:
            self.session_manager.stop_session(EndReason.HOTKEY)
            return
        self.session_manager.start_session(SessionMode.DICTATION)

    def _on_cancel_hotkey(self) -> None:
        """Handle Esc cancellation."""
        self._cancel_pending_note_start = True
        with self._dictation_lock:
            self._dictation_session_id = None
            self._dictation_chunks.clear()
            self._dictation_pending_chunks = 0
            self._dictation_session_ended = False
        with self._note_lock:
            self._note_session_id = None
            self._note_chunks.clear()
            self._note_pending_chunks = 0
            self._note_session_ended = False
        if self.overlay is not None:
            self.overlay.hide()
        self.session_manager.cancel_session(EndReason.ESC)

    def _on_session_start(self, mode: SessionMode) -> None:
        """Callback from engine when listening session begins."""
        try:
            both_front, _, _ = check_whatsapp_frontmost_dual()
            self._session_whatsapp_front = bool(both_front)
        except Exception as e:
            logger.debug("Failed dual frontmost check at session start: %s", e)
            self._session_whatsapp_front = False

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

            def _update_dictation_ui():
                self.title = self.TITLE_DICTATION
                self.status_item.title = "Status: Listening for dictation..."

            run_on_main_thread(_update_dictation_ui)

        elif mode == SessionMode.NOTE:
            with self._note_lock:
                self._note_session_id = self.session_manager.current_session_id
                self._note_chunks.clear()
                self._note_latencies.clear()
                self._note_pending_chunks = 0
                self._note_session_result = None
                self._note_session_ended = False

            # Show overlay only after audio stream is confirmed open
            if self.overlay is not None:
                self.overlay.show_note_listening()

            if self.config.notes.ready_sound:
                try:
                    snd = AppKit.NSSound.soundNamed_("Tink")
                    if snd is not None:
                        snd.play()
                except Exception as e:
                    logger.debug("Could not play ready sound: %s", e)

            def _update_note_ui():
                self.title = self.TITLE_NOTE
                self.status_item.title = "Status: Taking a note..."

            run_on_main_thread(_update_note_ui)

        else:
            if self.overlay is not None:
                self.overlay.show_listening(mode.value)

            def _update_command_ui():
                self.title = self.TITLE_COMMAND
                self.status_item.title = "Status: Listening for command..."

            run_on_main_thread(_update_command_ui)

    def _on_dictation_chunk(self, chunk: DictationChunk) -> None:
        """Callback from engine when a dictation or note speech chunk is ready for transcription."""
        is_dictation = False
        is_note = False
        with self._dictation_lock:
            if chunk.session_id == self._dictation_session_id and self._dictation_session_id is not None:
                is_dictation = True
        with self._note_lock:
            if chunk.session_id == self._note_session_id and self._note_session_id is not None:
                is_note = True

        if not is_dictation and not is_note:
            logger.debug("Dropping chunk %d for inactive/stale session %s", chunk.chunk_index, chunk.session_id)
            return

        if is_dictation:
            self._dispatch_dictation_chunk(chunk)
        else:
            self._dispatch_note_chunk(chunk)

    def _dispatch_dictation_chunk(self, chunk: DictationChunk) -> None:
        """Dispatch a speech chunk for dictation transcription."""
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

    def _dispatch_note_chunk(self, chunk: DictationChunk) -> None:
        """Dispatch a speech chunk for note transcription."""
        with self._note_lock:
            if chunk.session_id != self._note_session_id or self._note_session_id is None:
                logger.debug("Dropping chunk %d for inactive/stale note session", chunk.chunk_index)
                return
            self._note_pending_chunks += 1
            session_id = chunk.session_id
            chunk_idx = chunk.chunk_index
            is_final = chunk.is_final

            # Context prompt from previous chunk
            prompt = None
            if self.config.dictation.context_prompt and chunk_idx > 0:
                completed = [self._note_chunks[i] for i in sorted(self._note_chunks.keys()) if i < chunk_idx]
                if completed:
                    prompt = completed[-1]

        if self.transcriber is None:
            logger.warning("No transcriber available for note chunk %d", chunk_idx)
            self._handle_note_chunk_result(
                session_id,
                chunk_idx,
                TranscriptionResult(text="", language="en", audio_duration_sec=0.0, processing_time_sec=0.0),
                is_final,
            )
            return

        def _on_success(tx_res: TranscriptionResult):
            self._handle_note_chunk_result(session_id, chunk_idx, tx_res, is_final)

        def _on_err(err: Exception):
            logger.error("Error transcribing note chunk %d: %s", chunk_idx, err)
            self._handle_note_chunk_result(
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

    def _handle_note_chunk_result(
        self,
        session_id: str,
        chunk_index: int,
        tx_result: TranscriptionResult,
        is_final: bool,
    ) -> None:
        """Process completed transcription of a note chunk."""
        should_finalize = False
        stop_phrase_detected = False
        with self._note_lock:
            if session_id != self._note_session_id or self._note_session_id is None:
                logger.debug("Dropping chunk result for stale note session %s", session_id)
                return

            self._note_pending_chunks = max(0, self._note_pending_chunks - 1)
            self._note_chunks[chunk_index] = tx_result.text
            self._note_latencies[chunk_index] = tx_result.processing_time_sec

            # Check if accumulated text ends with a configured note stop phrase
            ordered_texts = [self._note_chunks[i] for i in sorted(self._note_chunks.keys())]
            joined_so_far = join_transcription_chunks(ordered_texts)
            match = detect_stop_phrase(
                joined_so_far,
                self.config.notes.stop_phrases,
                self.config.dictation.fuzzy_tolerance,
            )
            if match is not None and not self._note_session_ended:
                stop_phrase_detected = True

            if self._note_session_ended and self._note_pending_chunks == 0:
                should_finalize = True

        if stop_phrase_detected:
            logger.info("Stop phrase '%s' detected during note recording; stopping session.", match.matched_phrase)
            self.session_manager.stop_session(EndReason.STOP_PHRASE)

        if should_finalize:
            self._finalize_note_save(session_id)

    def _on_session_end(self, result: SessionResult) -> None:
        """Callback from engine when listening session ends."""
        # 1. If canceled by Esc:
        if result.end_reason == EndReason.ESC:
            if result.mode == SessionMode.DICTATION:
                with self._dictation_lock:
                    self._dictation_session_id = None
                    self._dictation_chunks.clear()
                    self._dictation_pending_chunks = 0
            elif result.mode == SessionMode.NOTE:
                with self._note_lock:
                    self._note_session_id = None
                    self._note_chunks.clear()
                    self._note_pending_chunks = 0
            if self.overlay is not None:
                self.overlay.hide()
            self._restore_idle_ui()
            logger.info("Session canceled by Esc; skipping transcription/action.")
            return

        # 2. DICTATION MODE
        if result.mode == SessionMode.DICTATION:
            if not result.speech_detected and not self._dictation_chunks:
                if self.overlay is not None:
                    self.overlay.show_dictation_empty()
                self._restore_idle_ui()
                logger.info("Dictation ended with no speech; pasting nothing.")
                return

            # Show "Transcribing..." in overlay until all chunks finish and paste happens
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

        # 3. NOTE MODE
        if result.mode == SessionMode.NOTE:
            if not result.speech_detected and not self._note_chunks:
                if self.overlay is not None:
                    self.overlay.show_note_empty()
                self._restore_idle_ui()
                logger.info("Note session ended with no speech; creating nothing.")
                return

            if self.overlay is not None:
                self.overlay.show_transcribing()

            def _set_transcribing_note_ui():
                self.title = self.TITLE_TRANSCRIBING
                self.status_item.title = "Status: Transcribing final note..."

            run_on_main_thread(_set_transcribing_note_ui)

            should_finalize = False
            with self._note_lock:
                self._note_session_result = result
                self._note_session_ended = True
                if self._note_pending_chunks == 0:
                    should_finalize = True

            if should_finalize:
                self._finalize_note_save(result.session_id)
            return

        # 4. COMMAND MODE: verify speech was detected and WAV exists
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

        # Privacy guard for WhatsApp frontmost
        if self._session_whatsapp_front and not self.config.privacy.keep_audio_when_whatsapp_front:
            display_text = f"[REDACTED (WhatsApp active: {formatted.word_count} words)]"
            if session_result and session_result.audio_path and session_result.audio_path.exists():
                try:
                    session_result.audio_path.unlink()
                except Exception as e:
                    logger.debug("Failed to delete WhatsApp dictation audio: %s", e)
        else:
            display_text = formatted.text

        t_paste_0 = time.perf_counter()

        def _on_paste_done(restored: bool):
            t_paste_1 = time.perf_counter()
            paste_duration_ms = (t_paste_1 - t_paste_0) * 1000.0
            self._restore_idle_ui()
            if self.overlay is not None:
                self.overlay.show_dictation_inserted(formatted.word_count)

            lat_str = ", ".join(f"{l:.3f}s" for l in latencies) if latencies else "0.0s"
            if self._session_whatsapp_front and not self.config.privacy.keep_audio_when_whatsapp_front:
                logger.info(
                    "[Dictation Complete] WhatsApp Frontmost | Words: %d | Chunks: %d | Latencies: [%s] | End Reason: %s (%s) | Paste Duration: %.1fms",
                    formatted.word_count,
                    len(chunks_list),
                    lat_str,
                    end_reason_str,
                    matched_phrase_str,
                    paste_duration_ms,
                )
            else:
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
                f"│ [Dictation Inserted] \"{display_text}\"\n"
                f"│ Words:            {formatted.word_count}\n"
                f"│ Chunks:           {len(chunks_list)} (Transcribe times: [{lat_str}])\n"
                f"│ End Reason:       {end_reason_str} ({matched_phrase_str})\n"
                f"│ Paste Duration:   {paste_duration_ms:.1f} ms\n"
                f"│ Audio File:       {session_result.audio_path.name if session_result and session_result.audio_path and session_result.audio_path.exists() else 'Deleted (Privacy)'}\n"
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

    def _finalize_note_save(self, session_id: str) -> None:
        """Join note chunks, strip stop phrase, format HTML, and write to macOS Notes."""
        with self._note_lock:
            if session_id != self._note_session_id:
                return
            chunks_list = [self._note_chunks[i] for i in sorted(self._note_chunks.keys())]
            latencies = [self._note_latencies.get(i, 0.0) for i in sorted(self._note_chunks.keys())]
            session_result = self._note_session_result
            self._note_session_id = None

        raw_joined = join_transcription_chunks(chunks_list)

        match = detect_stop_phrase(
            raw_joined,
            self.config.notes.stop_phrases,
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

        if not final_text.strip():
            self._restore_idle_ui()
            if self.overlay is not None:
                self.overlay.show_note_empty()
            logger.info("[Note Complete] Empty note text; nothing to create.")
            return

        # Privacy guard for WhatsApp frontmost
        if self._session_whatsapp_front and not self.config.privacy.keep_audio_when_whatsapp_front:
            if session_result and session_result.audio_path and session_result.audio_path.exists():
                try:
                    session_result.audio_path.unlink()
                except Exception as e:
                    logger.debug("Failed to delete WhatsApp note audio: %s", e)

        word_count = len(final_text.split())
        if self.overlay is not None:
            self.overlay.show_saving_note()

        def _set_saving_ui():
            self.title = self.TITLE_TRANSCRIBING
            self.status_item.title = "Status: Saving note to Notes app..."

        run_on_main_thread(_set_saving_ui)

        def _save_worker():
            title = generate_note_title(self.config.notes.title_template)
            body_html = format_note_html(title, final_text)

            result = self.notes_writer.create_note(
                title=title,
                body_html=body_html,
                folder=self.config.notes.folder,
                reveal=self.config.notes.reveal_after_save,
            )

            lat_str = ", ".join(f"{l:.3f}s" for l in latencies) if latencies else "0.0s"

            if result.success:
                def _on_success():
                    self._restore_idle_ui()
                    if self.overlay is not None:
                        self.overlay.show_note_saved(word_count)

                run_on_main_thread(_on_success)
                logger.info(
                    "[Note Saved] Title: \"%s\" | Words: %d | Chunks: %d [%s] | End Reason: %s (%s)",
                    title,
                    word_count,
                    len(chunks_list),
                    lat_str,
                    end_reason_str,
                    matched_phrase_str,
                )
                print(
                    f"\n"
                    f"┌─────────────────────────────────────────────────────────\n"
                    f"│ [Note Saved] \"{title}\"\n"
                    f"│ Words:       {word_count}\n"
                    f"│ Chunks:      {len(chunks_list)} (Transcribe times: [{lat_str}])\n"
                    f"│ End Reason:  {end_reason_str} ({matched_phrase_str})\n"
                    f"│ Status:      {result.message}\n"
                    f"└─────────────────────────────────────────────────────────\n",
                    flush=True,
                )
            else:
                # Save failure backup to note_backups/
                backup_path = save_note_failure_backup(
                    text=final_text,
                    title=title,
                    error_msg=result.error or result.message,
                    backup_dir=Path("note_backups"),
                )

                # Copy text to clipboard as normal text (no transient flag)
                try:
                    pb = get_general_pasteboard()
                    pb.clearContents()
                    pb.setString_forType_(final_text, AppKit.NSPasteboardTypeString)
                except Exception as e:
                    logger.error("Failed to copy failed note to pasteboard: %s", e)

                def _notify_ui():
                    self._restore_idle_ui()
                    if self.overlay is not None:
                        self.overlay.show_note_failed_copied(result.error or result.message)

                run_on_main_thread(_notify_ui)
                logger.error(
                    "[Note Save FAILED] %s | Text backed up to %s and copied to clipboard.",
                    result.error or result.message,
                    backup_path,
                )
                print(
                    f"\n"
                    f"┌─────────────────────────────────────────────────────────\n"
                    f"│ [Note Save FAILED] {result.error or result.message}\n"
                    f"│ Text copied to clipboard and backed up to: {backup_path}\n"
                    f"└─────────────────────────────────────────────────────────\n",
                    flush=True,
                )

        threading.Thread(target=_save_worker, name="note-save-worker", daemon=True).start()

    def _on_transcription_success(
        self,
        tx_result: TranscriptionResult,
        session_result: SessionResult,
    ) -> None:
        """Invoked when command mode transcription completes on worker thread."""
        if self._cancel_pending_note_start:
            self._cancel_pending_note_start = False
            self._restore_idle_ui()
            if self.overlay is not None:
                self.overlay.hide()
            logger.info("Command transcription completed, but Esc was pressed; ignoring.")
            return

        self._restore_idle_ui()

        # Handle Command Mode: Evaluate rule layer FIRST before any logging or notification
        t_rule_0 = time.perf_counter()
        match = match_command(tx_result.text)
        t_rule_1 = time.perf_counter()
        rule_match_time_sec = t_rule_1 - t_rule_0

        silence_wait_sec = (
            session_result.trailing_silence_sec
            if session_result.trailing_silence_sec > 0.0
            else self.config.timeouts.command_silence_sec
        )

        is_wa_search = (match is not None and match.action_id == "whatsapp_search")
        is_wa_open = (match is not None and match.action_id == "open_whatsapp")

        # Determine log and notification display text (redact at source for privacy)
        if is_wa_search and match.extracted_name and self.config.privacy.redact_names_in_logs:
            redacted_name = f"<name: {len(match.extracted_name)} chars>"
            log_text = re.sub(re.escape(match.extracted_name), redacted_name, tx_result.text, flags=re.IGNORECASE)
        elif self._session_whatsapp_front and not self.config.privacy.keep_audio_when_whatsapp_front:
            words = len(tx_result.text.split())
            log_text = f"<redacted (WhatsApp active: {words} words)>"
        else:
            log_text = tx_result.text

        # Audio file cleanup for WhatsApp commands or WhatsApp frontmost
        if (is_wa_search or is_wa_open) and not self.config.privacy.keep_whatsapp_audio:
            if session_result.audio_path and session_result.audio_path.exists():
                try:
                    session_result.audio_path.unlink()
                except Exception as e:
                    logger.debug("Failed to remove WhatsApp audio file: %s", e)
        elif self._session_whatsapp_front and not self.config.privacy.keep_audio_when_whatsapp_front:
            if session_result.audio_path and session_result.audio_path.exists():
                try:
                    session_result.audio_path.unlink()
                except Exception as e:
                    logger.debug("Failed to remove WhatsApp-front audio file: %s", e)

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
                f"│ Utterance:        \"{log_text}\"\n"
                f"│ Audio File:       {session_result.audio_path.name if session_result.audio_path and session_result.audio_path.exists() else 'Deleted (Privacy)'}\n"
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
                log_text,
                silence_wait_sec,
                tx_result.processing_time_sec,
                rule_match_time_sec,
                decision_to_result_sec,
            )

            try:
                rumps.notification(
                    title="Voice Assistant",
                    subtitle="Command Unrecognized",
                    message=f'Heard: "{log_text}" (Nothing recognized)',
                )
            except Exception as e:
                logger.debug("Could not display notification: %s", e)
            return

        # Case B: take_note command matched
        if match.action_id == "take_note":
            logger.info(
                "[Command Matched: take_note] \"%s\" | Silence: %.2fs | Tx: %.3fs | Rule: %.4fs",
                log_text,
                silence_wait_sec,
                tx_result.processing_time_sec,
                rule_match_time_sec,
            )

            def _start_note():
                if self._cancel_pending_note_start:
                    self._cancel_pending_note_start = False
                    self._restore_idle_ui()
                    if self.overlay is not None:
                        self.overlay.hide()
                    logger.info("take_note canceled by pending Esc before note start.")
                    return
                self.session_manager.start_session(SessionMode.NOTE)

            run_on_main_thread(_start_note)
            return

        # Case C: open_whatsapp command matched
        if match.action_id == "open_whatsapp":
            if self.overlay is not None:
                self.overlay.show_whatsapp_opening()

            def _on_wa_open_done(act_res: WhatsAppActionResult):
                run_on_main_thread(
                    lambda: self._handle_whatsapp_action_result(
                        act_res,
                        match,
                        tx_result,
                        session_result,
                        rule_match_time_sec,
                        log_text,
                    )
                )

            open_whatsapp_async(callback=_on_wa_open_done)
            return

        # Case D: whatsapp_search command matched
        if match.action_id == "whatsapp_search":
            extracted_name = match.extracted_name or ""
            if self.overlay is not None:
                self.overlay.show_whatsapp_searching(extracted_name)

            def _on_wa_search_done(act_res: WhatsAppActionResult):
                run_on_main_thread(
                    lambda: self._handle_whatsapp_action_result(
                        act_res,
                        match,
                        tx_result,
                        session_result,
                        rule_match_time_sec,
                        log_text,
                    )
                )

            search_whatsapp_async(
                extracted_name,
                callback=_on_wa_search_done,
                clipboard_restore_delay_ms=self.config.dictation.clipboard_restore_delay_ms,
                modifier_release_timeout_ms=self.config.dictation.modifier_release_timeout_ms,
            )
            return

        # Case E: open_notes (or other command) matched
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
                    log_text,
                )
            )

        execute_action_async(
            action_id=match.action_id,
            callback=_on_action_done,
            timeout_sec=5.0,
        )

    def _handle_whatsapp_action_result(
        self,
        act_res: WhatsAppActionResult,
        match: CommandMatch,
        tx_result: TranscriptionResult,
        session_result: SessionResult,
        rule_match_time_sec: float,
        log_text: str,
    ) -> None:
        """Handle completion of a WhatsApp action on main thread."""
        if act_res.success:
            if self.overlay is not None:
                if match.action_id == "open_whatsapp":
                    self.overlay.show_whatsapp_ready()
                else:
                    self.overlay.show_whatsapp_search_success(match.extracted_name)
        else:
            if self.overlay is not None:
                err_msg = act_res.error or act_res.message
                if "UI isn't readable" in err_msg or "Main window not found" in err_msg or "Search field not found" in err_msg:
                    self.overlay.show_whatsapp_ui_unreadable()
                elif "Couldn't reach" in err_msg:
                    self.overlay.show_whatsapp_unresponsive()
                elif "isn't in front" in err_msg:
                    self.overlay.show_whatsapp_not_front()
                elif "Couldn't focus" in err_msg:
                    self.overlay.show_whatsapp_focus_failed()
                elif match.action_id == "open_whatsapp":
                    self.overlay.show_whatsapp_open_failed(err_msg)
                else:
                    self.overlay.show_error(err_msg)

        silence_wait_sec = (
            session_result.trailing_silence_sec
            if session_result.trailing_silence_sec > 0.0
            else self.config.timeouts.command_silence_sec
        )
        decision_to_wa_sec = (
            (time.monotonic() - session_result.auto_stop_time)
            if session_result.auto_stop_time > 0.0
            else (tx_result.processing_time_sec + rule_match_time_sec + act_res.execution_time_sec)
        )
        speech_to_wa_sec = silence_wait_sec + decision_to_wa_sec

        status_str = "SUCCESS" if act_res.success else f"FAILED ({act_res.error or act_res.message})"
        print(
            f"\n"
            f"┌─────────────────────────────────────────────────────────\n"
            f"│ [Command Executed] \"{match.action_id}\" -> {status_str}\n"
            f"│ Utterance:        \"{log_text}\"\n"
            f"│ Action Message:   {act_res.message}\n"
            f"│ Audio File:       {session_result.audio_path.name if session_result.audio_path and session_result.audio_path.exists() else 'Deleted (Privacy)'}\n"
            f"├─────────────────────────────────────────────────────────\n"
            f"│ Latency Breakdown:\n"
            f"│ • Silence Wait:   {silence_wait_sec:.3f} s (VAD trailing silence)\n"
            f"│ • Transcription:  {tx_result.processing_time_sec:.3f} s\n"
            f"│ • Rule Matching:  {rule_match_time_sec:.4f} s\n"
            f"│ • Action Exec:    {act_res.execution_time_sec:.4f} s (Keys posted: {act_res.keys_posted})\n"
            f"│ ────────────────────────────────────────────────────────\n"
            f"│ Total Latency:    {decision_to_wa_sec:.3f} s\n"
            f"└─────────────────────────────────────────────────────────\n",
            flush=True,
        )

        logger.info(
            "[Command: %s] %s | Utterance: \"%s\" | Silence: %.2fs | Tx: %.3fs | Rule: %.4fs | Action: %.3fs | Total: %.3fs",
            match.action_id,
            "OK" if act_res.success else "FAIL",
            log_text,
            silence_wait_sec,
            tx_result.processing_time_sec,
            rule_match_time_sec,
            act_res.execution_time_sec,
            decision_to_wa_sec,
        )

        try:
            subtitle = "WhatsApp Ready" if (act_res.success and match.action_id == "open_whatsapp") else (
                "WhatsApp Search" if act_res.success else "Command Failed"
            )
            rumps.notification(
                title="Voice Assistant",
                subtitle=subtitle,
                message=f'Heard: "{log_text}" → {act_res.message}',
            )
        except Exception as e:
            logger.debug("Could not display notification: %s", e)

    def _handle_action_result(
        self,
        act_res: ActionResult,
        match: CommandMatch,
        tx_result: TranscriptionResult,
        session_result: SessionResult,
        rule_match_time_sec: float,
        log_text: Optional[str] = None,
    ) -> None:
        """Handle completion of an action on main thread."""
        display_text = log_text if log_text is not None else tx_result.text
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
            f"│ Utterance:        \"{display_text}\"\n"
            f"│ Action Message:   {act_res.message}\n"
            f"│ Audio File:       {session_result.audio_path.name if session_result.audio_path and session_result.audio_path.exists() else 'None'}\n"
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
                message=f'Heard: "{display_text}" → {act_res.message}',
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
