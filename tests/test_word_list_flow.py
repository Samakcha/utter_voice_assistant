"""App-level tests: the personal word list in dictation, notes, and (not) commands."""

import dataclasses
import os
from pathlib import Path
import subprocess
import time
from typing import List, Optional

import numpy as np
import pytest

from engine.config import VocabularyConfig, load_config
from engine.models import DictationChunk, EndReason, SessionMode, SessionResult, TranscriptionResult
from engine.notes_writer import BaseNotesWriter, NoteResult
from engine.session import SessionManager
from engine.vocabulary import PROMPT_HEADER, load_known_words
import interface.app as app_mod
from interface.app import VoiceAssistantApp

needs_dictionary = pytest.mark.skipif(load_known_words() is None, reason="system dictionary not available")


class ScriptedTranscriber:
    """Returns scripted text per call and records the prompt each call received."""

    def __init__(self, texts: List[str]):
        self.texts = list(texts)
        self.prompts: List[Optional[str]] = []

    def transcribe_async(self, audio, callback, on_error=None, prompt=None, normalize=None):
        self.prompts.append(prompt)
        text = self.texts.pop(0) if self.texts else ""
        callback(TranscriptionResult(text=text, language="en", audio_duration_sec=1.0, processing_time_sec=0.01))


class CapturingNotesWriter(BaseNotesWriter):
    def __init__(self):
        self.bodies: List[str] = []

    def create_note(self, title, body="", folder="", reveal=False, body_html=None) -> NoteResult:
        self.bodies.append(body_html or body)
        return NoteResult(success=True, note_id="x-mock-id://1", title=title)


def _make_app(tmp_path: Path, words: str, texts: List[str], auto_correct: bool = True, writer=None):
    word_file = tmp_path / "vocabulary.txt"
    word_file.write_text(words, encoding="utf-8")
    config = dataclasses.replace(
        load_config(),
        vocabulary=VocabularyConfig(file=word_file, max_prompt_terms=50, auto_correct=auto_correct),
    )
    tx = ScriptedTranscriber(texts)
    app = VoiceAssistantApp(
        config=config,
        session_manager=SessionManager(config=config),
        transcriber=tx,
        notes_writer=writer or CapturingNotesWriter(),
    )
    app.overlay = None
    return app, tx


def _chunk(session_id: str, idx: int) -> DictationChunk:
    return DictationChunk(
        session_id=session_id,
        chunk_index=idx,
        audio=np.zeros(1600, dtype=np.float32),
        speech_duration_ms=500.0,
        is_final=False,
    )


def _end(session_id: str, mode: SessionMode, reason=EndReason.HOTKEY) -> SessionResult:
    return SessionResult(
        mode=mode,
        end_reason=reason,
        duration_sec=3.0,
        audio_path=None,
        speech_detected=True,
        sample_rate=16000,
        total_samples=48000,
        session_id=session_id,
    )


@pytest.fixture
def pasted(monkeypatch):
    texts: List[str] = []
    monkeypatch.setattr(
        app_mod, "paste_and_restore",
        lambda text, config=None, on_complete=None, **kwargs: texts.append(text),
    )
    return texts


def _dictate(app: VoiceAssistantApp, n_chunks: int, reason=EndReason.HOTKEY) -> None:
    app.session_manager.start_session(SessionMode.DICTATION)
    sid = app.session_manager.current_session_id
    app._on_session_start(SessionMode.DICTATION)
    for i in range(n_chunks):
        app._on_dictation_chunk(_chunk(sid, i))
    app.session_manager.stop_session(reason)
    app._on_session_end(_end(sid, SessionMode.DICTATION, reason))


@needs_dictionary
def test_dictation_hints_words_and_fixes_mishearings(tmp_path, pasted):
    app, tx = _make_app(tmp_path, "Rahul\nPRD\n", ["Send the prd to Rahool.", "He needs it today."])
    _dictate(app, 2)

    assert tx.prompts[0] == f"{PROMPT_HEADER} Rahul, PRD."
    assert tx.prompts[1] == f"{PROMPT_HEADER} Rahul, PRD. Send the prd to Rahool."
    assert pasted == ["Send the PRD to Rahul. He needs it today."]


def test_dictation_without_word_list_keeps_old_prompts(tmp_path, pasted):
    app, tx = _make_app(tmp_path, "# nothing yet\n", ["First part.", "Second part."])
    _dictate(app, 2)

    assert tx.prompts == [None, "First part."]
    assert pasted == ["First part. Second part."]


def test_auto_correct_off_still_hints_but_never_rewrites(tmp_path, pasted):
    app, tx = _make_app(tmp_path, "Rahul\nPRD\n", ["Send the prd to Rahool."], auto_correct=False)
    _dictate(app, 1)

    assert tx.prompts[0] == f"{PROMPT_HEADER} Rahul, PRD."
    assert pasted == ["Send the prd to Rahool."]


@needs_dictionary
def test_echoed_hint_cannot_hide_a_stop_phrase(tmp_path, pasted):
    app, _ = _make_app(
        tmp_path, "Rahul\nPRD\nGitHub\n",
        ["Call Rahool tomorrow, stop dictation.", "Vocabulary: Rahul, PRD, GitHub."],
    )
    _dictate(app, 2, reason=EndReason.STOP_PHRASE)

    assert pasted == ["Call Rahul tomorrow."]


def test_word_list_edits_apply_without_restart(tmp_path, pasted):
    app, tx = _make_app(tmp_path, "Rahul\n", ["One.", "Two."])
    _dictate(app, 1)
    assert tx.prompts[-1] == f"{PROMPT_HEADER} Rahul."

    word_file = tmp_path / "vocabulary.txt"
    word_file.write_text("Rahul\nSaanvi\n", encoding="utf-8")
    st = word_file.stat()
    os.utime(word_file, (st.st_atime, st.st_mtime + 5))

    _dictate(app, 1)
    assert tx.prompts[-1] == f"{PROMPT_HEADER} Rahul, Saanvi."


@needs_dictionary
def test_note_text_is_corrected_before_saving(tmp_path):
    writer = CapturingNotesWriter()
    app, tx = _make_app(tmp_path, "Saanvi\nAntigravity\n", ["Ask Saanvee about anti gravity.", "stop note"], writer=writer)
    app._on_session_start(SessionMode.NOTE)
    sid = app._note_session_id
    app._on_dictation_chunk(_chunk(sid, 0))
    app._on_dictation_chunk(_chunk(sid, 1))
    app._on_session_end(_end(sid, SessionMode.NOTE, EndReason.STOP_PHRASE))
    time.sleep(0.2)

    assert tx.prompts[0] == f"{PROMPT_HEADER} Saanvi, Antigravity."
    assert len(writer.bodies) == 1
    assert "Ask Saanvi about Antigravity." in writer.bodies[0]
    assert "Saanvee" not in writer.bodies[0]


def test_command_mode_never_gets_the_word_list(tmp_path):
    app, tx = _make_app(tmp_path, "Rahul\nPRD\n", ["open notes"])
    app._on_command_hotkey = lambda: None
    res = SessionResult(
        mode=SessionMode.COMMAND,
        end_reason=EndReason.SILENCE,
        duration_sec=1.0,
        audio_path=None,
        speech_detected=True,
        sample_rate=16000,
        total_samples=16000,
        session_id="cmd-1",
        audio=np.full(16000, 0.2, dtype=np.float32),
    )
    app._on_transcription_success = lambda tx_res, result: None  # don't run the action
    app._on_session_end(res)

    assert tx.prompts == [None]


def test_edit_word_list_menu_creates_file_and_opens_editor(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda args, *a, **k: calls.append(args))
    word_file = tmp_path / "new" / "vocabulary.txt"
    config = dataclasses.replace(load_config(), vocabulary=VocabularyConfig(file=word_file))
    app = VoiceAssistantApp(config=config, session_manager=SessionManager(config=config))
    app.overlay = None

    app.open_word_list()

    assert word_file.exists()
    assert calls == [["open", "-t", str(word_file)]]
