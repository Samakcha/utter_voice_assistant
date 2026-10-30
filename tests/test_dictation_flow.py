"""Integration and race-condition tests for Dictation Mode chunking and insertion."""

import time
import numpy as np
import pytest

from engine.config import AppConfig, DictationConfig, load_config
from engine.models import DictationChunk, EndReason, SessionMode, SessionResult, TranscriptionResult
from engine.session import SessionManager
from interface.app import VoiceAssistantApp


class MockAudioCapture:
    def __init__(self, sample_rate=16000, chunk_size=512, channels=1):
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self.channels = channels
        self.is_running = False
        self.callback = None
        self.device_name = "Mock Mic"

    def start(self, callback):
        self.is_running = True
        self.callback = callback

    def stop(self):
        self.is_running = False


class MockVAD:
    def __init__(self):
        self.is_speech_return = False
        self.prob_return = 0.0

    def is_speech(self, chunk):
        return self.is_speech_return, self.prob_return

    def reset(self):
        pass


class MockTranscriber:
    def __init__(self):
        self.queue = []
        self.responses = []

    def transcribe_async(self, audio, callback, on_error=None, prompt=None, normalize=None):
        self.queue.append((audio, callback, prompt))
        if self.responses:
            res = self.responses.pop(0)
            callback(res)


def test_dictation_chunking_and_min_speech_filter(tmp_path):
    config = load_config()
    mock_audio = MockAudioCapture()
    mock_vad = MockVAD()

    emitted_chunks = []
    session_manager = SessionManager(
        config=config,
        vad=mock_vad,
        audio_capture=mock_audio,
        on_dictation_chunk=lambda chunk: emitted_chunks.append(chunk),
    )

    # 1. Start dictation session
    started = session_manager.start_session(SessionMode.DICTATION)
    assert started is True
    session_id = session_manager.current_session_id
    assert session_id != ""

    dummy_chunk = np.zeros(512, dtype=np.float32)

    # 2. Simulate brief click / cough (< 250 ms, e.g. 3 frames = 96 ms)
    mock_vad.is_speech_return = True
    for _ in range(3):
        session_manager._on_audio_chunk(dummy_chunk)

    # Followed by pause of > 0.8s (simulate by manipulating last_speech_time)
    mock_vad.is_speech_return = False
    session_manager._last_speech_time = time.monotonic() - 1.0  # 1.0s pause
    session_manager._on_audio_chunk(dummy_chunk)

    # Chunks < 250 ms must be skipped!
    assert len(emitted_chunks) == 0

    # 3. Simulate real speech (10 frames = 320 ms > 250 ms)
    mock_vad.is_speech_return = True
    for _ in range(10):
        session_manager._on_audio_chunk(dummy_chunk)

    # Followed by pause of 1.0s
    mock_vad.is_speech_return = False
    session_manager._last_speech_time = time.monotonic() - 1.0
    session_manager._on_audio_chunk(dummy_chunk)

    # Now a chunk should be emitted!
    assert len(emitted_chunks) == 1
    assert emitted_chunks[0].chunk_index == 0
    assert emitted_chunks[0].session_id == session_id
    assert emitted_chunks[0].is_final is False
    assert emitted_chunks[0].speech_duration_ms >= 250.0

    session_manager.stop_session(EndReason.HOTKEY)


def test_dictation_session_id_race_rejection():
    """Verify that chunk results arriving after Esc or for an old session ID are ignored."""
    config = load_config()
    session_manager = SessionManager(config=config, vad=MockVAD(), audio_capture=MockAudioCapture())
    mock_tx = MockTranscriber()

    app = VoiceAssistantApp(config=config, session_manager=session_manager, transcriber=mock_tx)
    app.overlay = None  # Bypass UI in unit test

    # Start session 1
    session_manager.start_session(SessionMode.DICTATION)
    session_id_1 = session_manager.current_session_id
    app._on_session_start(SessionMode.DICTATION)

    # Enqueue a chunk for session 1
    chunk_1 = DictationChunk(
        session_id=session_id_1,
        chunk_index=0,
        audio=np.zeros(1600, dtype=np.float32),
        speech_duration_ms=300.0,
        is_final=False,
    )
    app._on_dictation_chunk(chunk_1)
    assert app._dictation_pending_chunks == 1

    # Now user presses Esc (cancels session 1)
    app._on_cancel_hotkey()

    # Verify that app discarded session 1
    assert app._dictation_session_id is None
    assert len(app._dictation_chunks) == 0

    # Stale result arrives from worker thread for cancelled session 1
    stale_result = TranscriptionResult(
        text="Stale dictation text from session 1",
        language="en",
        audio_duration_sec=1.0,
        processing_time_sec=0.2,
    )
    app._handle_dictation_chunk_result(session_id_1, 0, stale_result, is_final=False)

    # It must NOT be added or pasted!
    assert len(app._dictation_chunks) == 0


def test_dictation_stop_phrase_integration_flow():
    """Simulate chunks arriving -> stop phrase detected -> final text excludes phrase and is pasted once."""
    config = load_config()
    session_manager = SessionManager(config=config, vad=MockVAD(), audio_capture=MockAudioCapture())
    mock_tx = MockTranscriber()

    app = VoiceAssistantApp(config=config, session_manager=session_manager, transcriber=mock_tx)
    app.overlay = None

    # Track what is pasted
    pasted_texts = []

    # Monkeypatch paste_and_restore in interface.app module
    import interface.app as app_mod
    orig_paste = app_mod.paste_and_restore
    app_mod.paste_and_restore = lambda text, config=None, on_complete=None, **kwargs: pasted_texts.append(text)

    try:
        session_manager.start_session(SessionMode.DICTATION)
        session_id = session_manager.current_session_id
        app._on_session_start(SessionMode.DICTATION)

        # Chunk 0 arrives: "Please prepare the presentation."
        tx_0 = TranscriptionResult(text="Please prepare the presentation.", language="en", audio_duration_sec=2.0, processing_time_sec=0.2)
        chunk_0 = DictationChunk(session_id=session_id, chunk_index=0, audio=np.zeros(1600, dtype=np.float32), speech_duration_ms=500.0, is_final=False)
        app._on_dictation_chunk(chunk_0)
        app._handle_dictation_chunk_result(session_id, 0, tx_0, is_final=False)

        # Chunk 1 arrives with stop phrase: "And send it tomorrow, stop dictation."
        tx_1 = TranscriptionResult(text="And send it tomorrow, stop dictation.", language="en", audio_duration_sec=2.0, processing_time_sec=0.2)
        chunk_1 = DictationChunk(session_id=session_id, chunk_index=1, audio=np.zeros(1600, dtype=np.float32), speech_duration_ms=500.0, is_final=False)
        app._on_dictation_chunk(chunk_1)
        app._handle_dictation_chunk_result(session_id, 1, tx_1, is_final=False)

        # Session stops on STOP_PHRASE
        res = SessionResult(
            mode=SessionMode.DICTATION,
            end_reason=EndReason.STOP_PHRASE,
            duration_sec=4.0,
            audio_path=None,
            speech_detected=True,
            sample_rate=16000,
            total_samples=32000,
            session_id=session_id,
        )
        app._on_session_end(res)

        # Verify:
        # 1. Pasted once
        assert len(pasted_texts) == 1
        # 2. Stop phrase stripped, dangling connector/comma cleaned, joined smoothly
        assert pasted_texts[0] == "Please prepare the presentation, and send it tomorrow."
    finally:
        app_mod.paste_and_restore = orig_paste
