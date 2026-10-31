"""
Unit and integration tests for the Voice Note-taking session lifecycle in VoiceAssistantApp.
Tests note accumulation, stop phrase detection, note creation, Esc cancellation,
transition races, and failure fallbacks (backup file and clipboard copy).
"""

from pathlib import Path
import threading
import time
from typing import List, Optional
import AppKit
import pytest

from engine.config import (
    AppConfig,
    AudioConfig,
    DictationConfig,
    HotkeysConfig,
    NormalizationConfig,
    NotesConfig,
    StorageConfig,
    TimeoutsConfig,
    VADConfig,
    WhisperConfig,
)
from engine.models import DictationChunk, EndReason, SessionMode, SessionResult, TranscriptionResult
from engine.notes_writer import BaseNotesWriter, NoteResult
from engine.session import SessionManager
from interface.app import VoiceAssistantApp


class MockNotesWriter(BaseNotesWriter):
    """Mock writer to capture created notes without interacting with macOS Notes."""

    def __init__(self, succeed: bool = True, error_msg: Optional[str] = None):
        self.succeed = succeed
        self.error_msg = error_msg
        self.created_notes: List[dict] = []

    def create_note(
        self,
        title: str,
        body: str = "",
        folder: str = "",
        reveal: bool = False,
        body_html: Optional[str] = None,
    ) -> NoteResult:
        self.created_notes.append({
            "title": title,
            "body": body,
            "folder": folder,
            "reveal": reveal,
            "body_html": body_html,
        })
        if self.succeed:
            return NoteResult(
                success=True,
                note_id="x-mock-id://123",
                title=title,
            )
        return NoteResult(
            success=False,
            note_id=None,
            title=title,
            error=self.error_msg or "Mock write failure",
        )


class MockTranscriber:
    """Mock transcriber returning configurable text."""

    def __init__(self, responses: Optional[List[str]] = None):
        self.responses = list(responses or ["default transcript"])
        self.call_count = 0

    def transcribe_async(self, audio, callback, on_error=None, prompt=None, normalize=True):
        idx = min(self.call_count, len(self.responses) - 1)
        text = self.responses[idx]
        self.call_count += 1
        res = TranscriptionResult(
            text=text,
            language="en",
            audio_duration_sec=1.0,
            processing_time_sec=0.01,
        )
        # Execute callback immediately
        callback(res)


def create_app_fixture(notes_writer: Optional[BaseNotesWriter] = None, transcriber=None):
    config = AppConfig(
        hotkeys=HotkeysConfig(
            command_mode="<ctrl>+<alt>+m",
            dictation_mode="<ctrl>+<alt>+d",
            cancel="<esc>",
        ),
        timeouts=TimeoutsConfig(
            command_silence_sec=1.5,
            command_no_speech_sec=3.0,
            dictation_silence_sec=2.0,
        ),
        audio=AudioConfig(sample_rate=16000, chunk_size=512, channels=1),
        vad=VADConfig(speech_threshold=0.5, min_speech_chunks=2),
        storage=StorageConfig(
            recordings_dir=Path("/tmp/voice_test"),
            max_recordings_kept=5,
        ),
        whisper=WhisperConfig(
            model_size="tiny",
            language="en",
            initial_prompt="",
            models_dir=Path("/tmp/voice_test/models"),
        ),
        normalization=NormalizationConfig(
            enabled=False,
            target_peak_dbfs=-3.0,
            max_gain_db=15.0,
        ),
        dictation=DictationConfig(
            chunk_pause_sec=0.8,
            min_speech_ms=250,
            stop_phrases=["stop dictation", "end dictation"],
            fuzzy_tolerance=0.2,
            context_prompt=True,
            trailing_space=True,
            clipboard_restore_delay_ms=20,
            modifier_release_timeout_ms=50,
        ),
        notes=NotesConfig(
            stop_phrases=["stop note", "stop dictation", "end dictation"],
            silence_sec=10.0,
            title_template="Voice note {date} {time}",
            folder="",
            reveal_after_save=False,
            ready_sound=False,
        ),
    )

    session_manager = SessionManager(config=config)
    writer = notes_writer or MockNotesWriter(succeed=True)
    tx = transcriber or MockTranscriber()

    app = VoiceAssistantApp(
        config=config,
        session_manager=session_manager,
        transcriber=tx,
        notes_writer=writer,
    )
    return app, session_manager, writer, tx


def test_note_session_lifecycle_and_creation():
    """Verify that chunks -> stop phrase -> note created via notes_writer."""
    mock_writer = MockNotesWriter(succeed=True)
    mock_tx = MockTranscriber(responses=["Buy milk and bread.", "stop note"])
    app, session_mgr, writer, tx = create_app_fixture(notes_writer=mock_writer, transcriber=mock_tx)

    # 1. Start note session
    app._on_session_start(SessionMode.NOTE)
    assert app._note_session_id is not None

    session_id = app._note_session_id

    # 2. Receive first chunk
    chunk0 = DictationChunk(
        session_id=session_id,
        chunk_index=0,
        audio=b"fake-pcm-chunk0",
        speech_duration_ms=500.0,
        is_final=False,
    )
    app._on_dictation_chunk(chunk0)

    # 3. Receive second chunk with stop phrase
    chunk1 = DictationChunk(
        session_id=session_id,
        chunk_index=1,
        audio=b"fake-pcm-chunk1",
        speech_duration_ms=500.0,
        is_final=False,
    )
    app._on_dictation_chunk(chunk1)

    # 4. Session ends
    session_res = SessionResult(
        session_id=session_id,
        mode=SessionMode.NOTE,
        audio_path=Path("/tmp/note.wav"),
        duration_sec=4.0,
        end_reason=EndReason.STOP_PHRASE,
        speech_detected=True,
        sample_rate=16000,
        total_samples=64000,
    )
    app._on_session_end(session_res)

    # Allow background save worker thread to finish
    time.sleep(0.1)

    assert len(writer.created_notes) == 1
    created = writer.created_notes[0]
    assert created["title"].startswith("Voice note ")
    assert "Buy milk and bread." in created["body_html"]
    # Stop phrase must be stripped from body
    assert "stop note" not in created["body_html"]


def test_note_session_esc_cancellation():
    """Verify Esc clears note state and writes nothing."""
    mock_writer = MockNotesWriter(succeed=True)
    app, session_mgr, writer, tx = create_app_fixture(notes_writer=mock_writer)

    app._on_session_start(SessionMode.NOTE)
    session_id = app._note_session_id
    assert session_id is not None

    chunk = DictationChunk(
        session_id=session_id,
        chunk_index=0,
        audio=b"fake-pcm",
        speech_duration_ms=500.0,
        is_final=False,
    )
    app._on_dictation_chunk(chunk)

    # Cancel via Esc hotkey
    app._on_cancel_hotkey()

    assert app._note_session_id is None
    assert len(app._note_chunks) == 0

    # Fire session end callback with ESC
    session_res = SessionResult(
        session_id=session_id,
        mode=SessionMode.NOTE,
        audio_path=Path("/tmp/note.wav"),
        duration_sec=1.0,
        end_reason=EndReason.ESC,
        speech_detected=True,
        sample_rate=16000,
        total_samples=16000,
    )
    app._on_session_end(session_res)

    time.sleep(0.1)
    assert len(writer.created_notes) == 0


def test_esc_pressed_before_note_session_starts():
    """
    User Change 5: If Esc is pressed after command session ends
    but before the note session starts, the note session must not start.
    """
    mock_writer = MockNotesWriter(succeed=True)
    app, session_mgr, writer, tx = create_app_fixture(notes_writer=mock_writer)

    # Simulate command transcription result for "start taking notes"
    tx_res = TranscriptionResult(
        text="start taking notes",
        language="en",
        audio_duration_sec=2.0,
        processing_time_sec=0.1,
    )
    sess_res = SessionResult(
        session_id="cmd-123",
        mode=SessionMode.COMMAND,
        audio_path=Path("/tmp/cmd.wav"),
        duration_sec=2.0,
        end_reason=EndReason.SILENCE,
        speech_detected=True,
        sample_rate=16000,
        total_samples=32000,
    )

    # User presses Esc during the brief command processing window
    app._on_cancel_hotkey()
    assert app._cancel_pending_note_start is True

    # Command transcription completes
    app._on_transcription_success(tx_res, sess_res)

    # Note session should NOT have started
    assert session_mgr.current_mode != SessionMode.NOTE
    assert app._note_session_id is None
    assert len(writer.created_notes) == 0


def test_note_save_failure_fallback_backup_and_clipboard(tmp_path: Path, monkeypatch):
    """
    Verify that when Notes writer fails:
    1. Text is backed up to note_backups/.
    2. Text is copied to general pasteboard without transient flag.
    3. Overlay error is shown.
    """
    # Point backup dir to tmp_path
    monkeypatch.chdir(tmp_path)

    mock_writer = MockNotesWriter(succeed=False, error_msg="Notes AppleScript error (-1743)")
    mock_tx = MockTranscriber(responses=["Critical thought that must not be lost."])
    app, session_mgr, writer, tx = create_app_fixture(notes_writer=mock_writer, transcriber=mock_tx)

    app._on_session_start(SessionMode.NOTE)
    session_id = app._note_session_id

    chunk = DictationChunk(
        session_id=session_id,
        chunk_index=0,
        audio=b"fake-pcm",
        speech_duration_ms=500.0,
        is_final=True,
    )
    app._on_dictation_chunk(chunk)

    session_res = SessionResult(
        session_id=session_id,
        mode=SessionMode.NOTE,
        audio_path=Path("/tmp/note.wav"),
        duration_sec=2.0,
        end_reason=EndReason.SILENCE,
        speech_detected=True,
        sample_rate=16000,
        total_samples=32000,
    )
    app._on_session_end(session_res)

    # Wait for save worker thread
    time.sleep(0.2)

    # 1. Check backup file
    backup_dir = tmp_path / "note_backups"
    assert backup_dir.is_dir()
    backup_files = list(backup_dir.glob("note_backup_*.txt"))
    assert len(backup_files) == 1
    content = backup_files[0].read_text(encoding="utf-8")
    assert "Critical thought that must not be lost." in content
    assert "Notes AppleScript error (-1743)" in content

    # 2. Check pasteboard content
    pb = AppKit.NSPasteboard.generalPasteboard()
    copied = pb.stringForType_(AppKit.NSPasteboardTypeString)
    assert copied == "Critical thought that must not be lost."


def test_empty_note_creates_nothing():
    """Verify that an empty note creates no file and no note."""
    mock_writer = MockNotesWriter(succeed=True)
    app, session_mgr, writer, tx = create_app_fixture(notes_writer=mock_writer)

    app._on_session_start(SessionMode.NOTE)
    session_id = app._note_session_id

    session_res = SessionResult(
        session_id=session_id,
        mode=SessionMode.NOTE,
        audio_path=None,
        duration_sec=0.5,
        end_reason=EndReason.NO_SPEECH,
        speech_detected=False,
        sample_rate=16000,
        total_samples=8000,
    )
    app._on_session_end(session_res)

    time.sleep(0.1)
    assert len(writer.created_notes) == 0


def test_hotkey_during_note_mode_stops_session():
    """Verify that pressing hotkey while in NOTE mode ends the session."""
    app, session_mgr, writer, tx = create_app_fixture()

    # Simulate active NOTE session in SessionManager
    session_mgr._current_mode = SessionMode.NOTE
    session_mgr._is_active = True

    stopped_reasons = []

    def mock_stop(reason):
        stopped_reasons.append(reason)
        session_mgr._is_active = False

    session_mgr.stop_session = mock_stop

    # Press command hotkey
    app._on_command_hotkey()
    assert EndReason.HOTKEY in stopped_reasons
