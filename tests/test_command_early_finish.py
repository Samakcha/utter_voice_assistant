"""Command early finish: fixed commands run as soon as they are recognized during a pause."""

import csv
import dataclasses
from pathlib import Path
import threading
import time
from typing import List, Optional

import numpy as np
import pytest

from engine.commands import EARLY_FINISH_ACTIONS, allows_early_finish, match_command
from engine.config import load_config
from engine.models import EndReason, SessionMode, SessionResult, TranscriptionResult
from engine.session import SessionManager
from interface.app import VoiceAssistantApp

FRAME = np.full(512, 0.1, dtype=np.float32)


class MockAudioCapture:
    def __init__(self):
        self.is_running = False
        self.device_name = "Mock Mic"

    def start(self, callback):
        self.is_running = True

    def stop(self):
        self.is_running = False


class MockVAD:
    def __init__(self):
        self.speech = False

    def is_speech(self, chunk):
        return self.speech, 0.9 if self.speech else 0.0

    def reset(self):
        pass


def _config(early_sec: float = 0.6, silence_sec: float = 1.5):
    base = load_config()
    timeouts = dataclasses.replace(
        base.timeouts, command_early_check_sec=early_sec, command_silence_sec=silence_sec
    )
    return dataclasses.replace(base, timeouts=timeouts)


def _manager(config, pauses: list, ended: list):
    vad = MockVAD()
    mgr = SessionManager(
        config=config,
        vad=vad,
        audio_capture=MockAudioCapture(),
        on_session_end=ended.append,
        on_command_pause=lambda sid, pid, audio: pauses.append((sid, pid, audio)),
    )
    return mgr, vad


def _speak(mgr: SessionManager, vad: MockVAD, frames: int = 5) -> None:
    vad.speech = True
    for _ in range(frames):
        mgr._on_audio_chunk(FRAME)


def _silence(mgr: SessionManager, vad: MockVAD, seconds: float) -> None:
    """One silent frame arriving `seconds` after the last speech."""
    vad.speech = False
    mgr._last_speech_time = time.monotonic() - seconds
    mgr._on_audio_chunk(FRAME)


# --------------------------------------------------------------------------------------
# Which commands may finish early
# --------------------------------------------------------------------------------------

def test_only_fixed_commands_allow_early_finish():
    assert EARLY_FINISH_ACTIONS == {"open_notes", "take_note", "open_whatsapp"}
    assert allows_early_finish(match_command("open notes"))
    assert allows_early_finish(match_command("start taking notes"))
    assert allows_early_finish(match_command("open WhatsApp"))
    assert not allows_early_finish(match_command("search WhatsApp for Alex Test"))
    assert not allows_early_finish(match_command("hello there"))
    assert not allows_early_finish(None)


def test_no_whatsapp_search_phrase_in_the_eval_set_finishes_early():
    rows = list(csv.DictReader(open(Path(__file__).parent / "data" / "eval_rules.csv", encoding="utf-8")))
    searches = [r["phrase"] for r in rows if r["expected"] == "whatsapp_search"]
    assert searches
    for phrase in searches:
        assert not allows_early_finish(match_command(phrase)), phrase


# --------------------------------------------------------------------------------------
# Session manager
# --------------------------------------------------------------------------------------

def test_pause_is_reported_once_with_all_audio_so_far():
    pauses, ended = [], []
    mgr, vad = _manager(_config(), pauses, ended)
    mgr.start_session(SessionMode.COMMAND)
    sid = mgr.current_session_id
    _speak(mgr, vad, 5)

    _silence(mgr, vad, 0.3)
    assert pauses == []  # not yet

    _silence(mgr, vad, 0.7)
    assert len(pauses) == 1
    assert pauses[0][0] == sid and pauses[0][1] == 1
    assert pauses[0][2].size == 7 * 512  # every frame captured so far

    _silence(mgr, vad, 0.9)
    assert len(pauses) == 1  # same pause: not reported again
    assert mgr.is_listening and ended == []
    mgr.stop_session(EndReason.ESC)


def test_new_pause_after_speech_resumes_gets_a_new_id():
    pauses, ended = [], []
    mgr, vad = _manager(_config(), pauses, ended)
    mgr.start_session(SessionMode.COMMAND)
    _speak(mgr, vad)
    _silence(mgr, vad, 0.7)
    _speak(mgr, vad, 2)
    _silence(mgr, vad, 0.7)
    assert [p[1] for p in pauses] == [1, 2]
    mgr.stop_session(EndReason.ESC)


def test_finish_early_while_still_silent_ends_the_session():
    pauses, ended = [], []
    mgr, vad = _manager(_config(), pauses, ended)
    mgr.start_session(SessionMode.COMMAND)
    sid = mgr.current_session_id
    _speak(mgr, vad)
    _silence(mgr, vad, 0.7)

    result = mgr.finish_command_early(sid, 1)
    assert result is not None
    assert result.early_finish is True
    assert result.end_reason == EndReason.SILENCE
    assert result.mode == SessionMode.COMMAND
    assert result.speech_detected is True
    assert result.trailing_silence_sec >= 0.6
    assert not mgr.is_listening
    assert ended == [result]


def test_finish_early_is_refused_once_the_user_speaks_again():
    pauses, ended = [], []
    mgr, vad = _manager(_config(), pauses, ended)
    mgr.start_session(SessionMode.COMMAND)
    sid = mgr.current_session_id
    _speak(mgr, vad)
    _silence(mgr, vad, 0.7)
    _speak(mgr, vad, 1)  # "...and search for Alex"

    assert mgr.finish_command_early(sid, 1) is None
    assert mgr.is_listening and ended == []

    _silence(mgr, vad, 0.7)  # pause 2
    assert mgr.finish_command_early(sid, 1) is None  # stale pause id
    assert mgr.finish_command_early(sid, 2) is not None


def test_finish_early_is_refused_for_wrong_or_finished_sessions():
    pauses, ended = [], []
    mgr, vad = _manager(_config(), pauses, ended)
    mgr.start_session(SessionMode.COMMAND)
    sid = mgr.current_session_id
    _speak(mgr, vad)
    _silence(mgr, vad, 0.7)
    assert mgr.finish_command_early("other-session", 1) is None

    mgr.stop_session(EndReason.ESC)
    assert mgr.finish_command_early(sid, 1) is None
    assert len(ended) == 1 and ended[0].early_finish is False


def test_normal_timeout_still_ends_the_session_without_early_flag():
    pauses, ended = [], []
    mgr, vad = _manager(_config(), pauses, ended)
    mgr.start_session(SessionMode.COMMAND)
    _speak(mgr, vad)
    _silence(mgr, vad, 0.7)
    _silence(mgr, vad, 1.6)
    deadline = time.monotonic() + 2.0
    while not ended and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(ended) == 1
    assert ended[0].early_finish is False
    assert ended[0].end_reason == EndReason.SILENCE


@pytest.mark.parametrize("early_sec", [0.0, 1.5, 2.0])
def test_early_check_off_when_zero_or_not_shorter_than_timeout(early_sec):
    pauses, ended = [], []
    mgr, vad = _manager(_config(early_sec=early_sec), pauses, ended)
    mgr.start_session(SessionMode.COMMAND)
    _speak(mgr, vad)
    _silence(mgr, vad, 1.2)
    assert pauses == []
    mgr.stop_session(EndReason.ESC)


def test_no_pause_report_before_speech_or_in_dictation():
    pauses, ended = [], []
    mgr, vad = _manager(_config(), pauses, ended)
    mgr.start_session(SessionMode.COMMAND)
    vad.speech = False
    mgr._on_audio_chunk(FRAME)
    assert pauses == []
    mgr.stop_session(EndReason.ESC)

    mgr.start_session(SessionMode.DICTATION)
    _speak(mgr, vad)
    _silence(mgr, vad, 0.7)
    assert pauses == []
    assert mgr.finish_command_early(mgr.current_session_id, 1) is None
    mgr.stop_session(EndReason.ESC)


# --------------------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------------------

class DeferredTranscriber:
    """Holds callbacks so a test can decide when each transcription 'finishes'."""

    def __init__(self, texts: List[str]):
        self.texts = list(texts)
        self.pending = []
        self.calls = 0

    def transcribe_async(self, audio, callback, on_error=None, prompt=None, normalize=None):
        self.calls += 1
        text = self.texts.pop(0) if self.texts else ""
        self.pending.append((callback, TranscriptionResult(
            text=text, language="en", audio_duration_sec=1.0, processing_time_sec=0.4,
        )))

    def finish_next(self):
        callback, res = self.pending.pop(0)
        callback(res)


def _app(texts: List[str]):
    config = _config()
    vad = MockVAD()
    mgr = SessionManager(config=config, vad=vad, audio_capture=MockAudioCapture())
    tx = DeferredTranscriber(texts)
    app = VoiceAssistantApp(config=config, session_manager=mgr, transcriber=tx)
    app.overlay = None
    handled: List[tuple] = []
    app._on_transcription_success = lambda tx_res, result: handled.append((tx_res, result))
    return app, mgr, vad, tx, handled


def _wait_for(cond, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not cond() and time.monotonic() < deadline:
        time.sleep(0.01)
    return cond()


def test_fixed_command_runs_at_the_pause_without_a_second_transcription():
    app, mgr, vad, tx, handled = _app(["Open notes."])
    mgr.start_session(SessionMode.COMMAND)
    _speak(mgr, vad)
    _silence(mgr, vad, 0.7)
    assert tx.calls == 1

    tx.finish_next()  # transcript arrives while the user is still silent
    assert _wait_for(lambda: handled)
    tx_res, result = handled[0]
    assert tx_res.text == "Open notes."
    assert result.early_finish is True
    assert not mgr.is_listening
    assert tx.calls == 1  # reused, not transcribed again


def test_whatsapp_search_still_waits_for_the_full_timeout():
    app, mgr, vad, tx, handled = _app(["Search WhatsApp for Alex.", "Search WhatsApp for Alex Test."])
    mgr.start_session(SessionMode.COMMAND)
    _speak(mgr, vad)
    _silence(mgr, vad, 0.7)
    tx.finish_next()
    time.sleep(0.05)
    assert mgr.is_listening and handled == []

    _speak(mgr, vad, 2)        # the rest of the name
    _silence(mgr, vad, 1.6)    # normal timeout
    assert _wait_for(lambda: tx.calls == 2 and tx.pending)  # the full transcription
    tx.finish_next()
    assert _wait_for(lambda: handled)
    tx_res, result = handled[-1]
    assert result.early_finish is False
    assert "Alex Test" in tx_res.text


def test_unrecognized_speech_does_not_finish_early():
    app, mgr, vad, tx, handled = _app(["Open the notes folder."])
    mgr.start_session(SessionMode.COMMAND)
    _speak(mgr, vad)
    _silence(mgr, vad, 0.7)
    tx.finish_next()
    time.sleep(0.05)
    assert mgr.is_listening and handled == []
    mgr.stop_session(EndReason.ESC)


def test_speaking_again_before_the_transcript_arrives_cancels_early_finish():
    app, mgr, vad, tx, handled = _app(["Open WhatsApp.", "Open WhatsApp and search Alex."])
    mgr.start_session(SessionMode.COMMAND)
    _speak(mgr, vad)
    _silence(mgr, vad, 0.7)
    _speak(mgr, vad, 2)  # user keeps talking
    tx.finish_next()     # "Open WhatsApp." arrives late
    time.sleep(0.05)
    assert mgr.is_listening and handled == []
    assert app._early_command is None  # stale transcript dropped

    _silence(mgr, vad, 1.6)
    assert _wait_for(lambda: tx.calls == 2 and tx.pending)  # the full transcription
    tx.finish_next()
    assert _wait_for(lambda: handled)
    tx_res, result = handled[-1]
    assert result.early_finish is False
    assert tx_res.text == "Open WhatsApp and search Alex."
