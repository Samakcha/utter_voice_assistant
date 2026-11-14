"""App-level tests: cleanup runs on dictation and notes, never on commands, and logs no words."""

import dataclasses
import logging
import time
from pathlib import Path
from typing import List

import numpy as np
import pytest

from engine.config import CleanupConfig, VocabularyConfig, load_config
from engine.models import DictationChunk, EndReason, SessionMode, SessionResult, TranscriptionResult
from engine.notes_writer import BaseNotesWriter, NoteResult
from engine.session import SessionManager
import interface.app as app_mod
from interface.app import VoiceAssistantApp


class ScriptedTranscriber:
    def __init__(self, texts: List[str]):
        self.texts = list(texts)

    def transcribe_async(self, audio, callback, on_error=None, prompt=None, normalize=None):
        text = self.texts.pop(0) if self.texts else ""
        callback(TranscriptionResult(text=text, language="en", audio_duration_sec=1.0, processing_time_sec=0.01))


class CapturingNotesWriter(BaseNotesWriter):
    def __init__(self):
        self.bodies: List[str] = []

    def create_note(self, title, body="", folder="", reveal=False, body_html=None) -> NoteResult:
        self.bodies.append(body_html or body)
        return NoteResult(success=True, note_id="x-mock-id://1", title=title)


def _make_app(tmp_path: Path, texts: List[str], cleanup=CleanupConfig(), redact=True, writer=None):
    config = dataclasses.replace(
        load_config(),
        vocabulary=VocabularyConfig(file=None),
        cleanup=cleanup,
        privacy=dataclasses.replace(load_config().privacy, redact_names_in_logs=redact),
    )
    app = VoiceAssistantApp(
        config=config,
        session_manager=SessionManager(config=config),
        transcriber=ScriptedTranscriber(texts),
        notes_writer=writer or CapturingNotesWriter(),
    )
    app.overlay = None
    return app


def _chunk(session_id: str, idx: int) -> DictationChunk:
    return DictationChunk(session_id=session_id, chunk_index=idx, audio=np.zeros(1600, dtype=np.float32),
                          speech_duration_ms=500.0, is_final=False)


def _end(session_id: str, mode: SessionMode, reason=EndReason.HOTKEY) -> SessionResult:
    return SessionResult(mode=mode, end_reason=reason, duration_sec=3.0, audio_path=None, speech_detected=True,
                         sample_rate=16000, total_samples=48000, session_id=session_id)


@pytest.fixture
def pasted(monkeypatch):
    texts: List[str] = []
    monkeypatch.setattr(app_mod, "paste_and_restore",
                        lambda text, config=None, on_complete=None, **kwargs: texts.append(text))
    return texts


def _dictate(app: VoiceAssistantApp, n_chunks: int, reason=EndReason.HOTKEY) -> None:
    app.session_manager.start_session(SessionMode.DICTATION)
    sid = app.session_manager.current_session_id
    app._on_session_start(SessionMode.DICTATION)
    for i in range(n_chunks):
        app._on_dictation_chunk(_chunk(sid, i))
    app.session_manager.stop_session(reason)
    app._on_session_end(_end(sid, SessionMode.DICTATION, reason))


def test_dictation_is_cleaned_across_chunks_before_pasting(tmp_path, pasted):
    # The pause after "Monday" split the speech into two chunks
    app = _make_app(tmp_path, ["Um, send the the report on Monday.", "No wait, Tuesday."])
    _dictate(app, 2)
    assert pasted == ["Send the report on Tuesday."]


def test_cleanup_off_pastes_exactly_what_was_heard(tmp_path, pasted):
    app = _make_app(tmp_path, ["Um, send the the report."], cleanup=CleanupConfig(enabled=False))
    _dictate(app, 1)
    assert pasted == ["Um, send the the report."]


def test_stop_phrase_still_works_with_cleanup(tmp_path, pasted):
    app = _make_app(tmp_path, ["Um, call Mum tomorrow, stop dictation."])
    _dictate(app, 1, reason=EndReason.STOP_PHRASE)
    assert pasted == ["Call Mum tomorrow."]


def test_dictation_that_was_only_um_pastes_nothing(tmp_path, pasted):
    app = _make_app(tmp_path, ["Um."])
    _dictate(app, 1)
    assert pasted == []


def test_note_is_cleaned_before_saving(tmp_path):
    writer = CapturingNotesWriter()
    app = _make_app(tmp_path, ["Uh, buy eggs. Scratch that. Buy bread at 6, I mean 7.", "stop note"], writer=writer)
    app._on_session_start(SessionMode.NOTE)
    sid = app._note_session_id
    app._on_dictation_chunk(_chunk(sid, 0))
    app._on_dictation_chunk(_chunk(sid, 1))
    app._on_session_end(_end(sid, SessionMode.NOTE, EndReason.STOP_PHRASE))
    time.sleep(0.2)

    assert len(writer.bodies) == 1
    assert "Buy bread at 7." in writer.bodies[0]
    assert "eggs" not in writer.bodies[0] and "Uh" not in writer.bodies[0]


def test_log_has_counts_but_no_words_when_names_are_redacted(tmp_path, pasted, caplog):
    app = _make_app(tmp_path, ["Um, call Saanvi on Monday, no wait, Tuesday."])
    with caplog.at_level(logging.INFO, logger=app_mod.logger.name):
        _dictate(app, 1)
    assert pasted == ["Call Saanvi on Tuesday."]
    lines = [r.getMessage() for r in caplog.records if "Cleanup" in r.getMessage()]
    assert lines == ["[Dictation] Cleanup: 1 filler, 1 correction."]


def test_log_shows_the_changes_when_redaction_is_off(tmp_path, pasted, caplog):
    app = _make_app(tmp_path, ["Meet on Monday, no wait, Tuesday."], redact=False)
    with caplog.at_level(logging.INFO, logger=app_mod.logger.name):
        _dictate(app, 1)
    lines = [r.getMessage() for r in caplog.records if "Cleanup" in r.getMessage()]
    assert lines == ["[Dictation] Cleanup: 1 correction: 'Monday' -> 'Tuesday'"]
