"""Unit and integration tests for SessionManager state machine and timeouts."""

from pathlib import Path
import tempfile
import time
from typing import Callable, Optional
import numpy as np

from engine.config import (
    AppConfig,
    AudioConfig,
    HotkeysConfig,
    StorageConfig,
    TimeoutsConfig,
    VADConfig,
)
from engine.models import EndReason, SessionMode
from engine.session import SessionManager


class MockAudioCapture:
    """Mock audio capture that simulates audio chunks without opening hardware."""

    def __init__(self, sample_rate=16000, chunk_size=512, channels=1):
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self.channels = channels
        self._callback: Optional[Callable[[np.ndarray], None]] = None
        self._is_active = False

    @property
    def is_active(self) -> bool:
        return self._is_active

    def start(self, on_chunk: Callable[[np.ndarray], None]) -> None:
        self._callback = on_chunk
        self._is_active = True

    def stop(self) -> None:
        self._is_active = False
        self._callback = None

    def feed_chunk(self, chunk: np.ndarray) -> None:
        if self._is_active and self._callback:
            self._callback(chunk)


class MockVAD:
    """Mock VAD with controllable speech probability."""

    def __init__(self, sample_rate=16000, threshold=0.5):
        self.sample_rate = sample_rate
        self.threshold = threshold
        self.next_prob = 0.0

    def reset(self):
        pass

    def is_speech(self, chunk: np.ndarray):
        return (self.next_prob >= self.threshold, self.next_prob)


def create_test_config(temp_dir: Path) -> AppConfig:
    return AppConfig(
        hotkeys=HotkeysConfig(
            command_mode="<ctrl>+<alt>+m",
            dictation_mode="<ctrl>+<alt>+d",
            cancel="<esc>",
        ),
        timeouts=TimeoutsConfig(
            command_silence_sec=0.2,     # Short for fast testing
            command_no_speech_sec=0.3,   # Short for fast testing
            dictation_silence_sec=0.4,   # Short for fast testing
        ),
        audio=AudioConfig(sample_rate=16000, chunk_size=512, channels=1),
        vad=VADConfig(speech_threshold=0.5, min_speech_chunks=2),
        storage=StorageConfig(recordings_dir=temp_dir, max_recordings_kept=3),
    )


def test_command_no_speech_timeout():
    with tempfile.TemporaryDirectory() as td:
        config = create_test_config(Path(td))
        mock_audio = MockAudioCapture()
        mock_vad = MockVAD()
        manager = SessionManager(config=config, vad=mock_vad, audio_capture=mock_audio)

        manager.start_session(SessionMode.COMMAND)
        assert manager.is_listening
        assert mock_audio.is_active

        # Feed silence chunks until initial timeout (0.3s)
        silence = np.zeros(512, dtype=np.float32)
        mock_vad.next_prob = 0.0

        for _ in range(8):
            mock_audio.feed_chunk(silence)
            time.sleep(0.05)

        assert not manager.is_listening
        assert not mock_audio.is_active


def test_command_speech_and_silence_autostop():
    with tempfile.TemporaryDirectory() as td:
        config = create_test_config(Path(td))
        mock_audio = MockAudioCapture()
        mock_vad = MockVAD()
        last_result = None

        def on_end(res):
            nonlocal last_result
            last_result = res

        manager = SessionManager(
            config=config,
            vad=mock_vad,
            audio_capture=mock_audio,
            on_session_end=on_end,
        )

        manager.start_session(SessionMode.COMMAND)

        # 1. Feed speech chunks
        speech_chunk = np.ones(512, dtype=np.float32) * 0.1
        mock_vad.next_prob = 0.9
        for _ in range(3):
            mock_audio.feed_chunk(speech_chunk)
            time.sleep(0.01)

        assert manager._speech_detected

        # 2. Feed silence chunks until trailing silence timeout (0.2s)
        silence_chunk = np.zeros(512, dtype=np.float32)
        mock_vad.next_prob = 0.0
        for _ in range(6):
            mock_audio.feed_chunk(silence_chunk)
            time.sleep(0.05)

        assert not manager.is_listening
        assert last_result is not None
        assert last_result.end_reason == EndReason.SILENCE
        assert last_result.speech_detected
        assert last_result.audio_path is not None
        assert last_result.audio_path.exists()


def test_dictation_toggle_hotkey():
    with tempfile.TemporaryDirectory() as td:
        config = create_test_config(Path(td))
        mock_audio = MockAudioCapture()
        mock_vad = MockVAD()
        last_result = None

        def on_end(res):
            nonlocal last_result
            last_result = res

        manager = SessionManager(
            config=config,
            vad=mock_vad,
            audio_capture=mock_audio,
            on_session_end=on_end,
        )

        manager.start_session(SessionMode.DICTATION)
        assert manager.is_listening

        # Triggering dictation mode again ends the session
        manager.start_session(SessionMode.DICTATION)
        assert not manager.is_listening
        assert last_result is not None
        assert last_result.end_reason == EndReason.HOTKEY


def test_esc_cancellation():
    with tempfile.TemporaryDirectory() as td:
        config = create_test_config(Path(td))
        mock_audio = MockAudioCapture()
        mock_vad = MockVAD()
        last_result = None

        def on_end(res):
            nonlocal last_result
            last_result = res

        manager = SessionManager(
            config=config,
            vad=mock_vad,
            audio_capture=mock_audio,
            on_session_end=on_end,
        )

        manager.start_session(SessionMode.COMMAND)
        assert manager.is_listening

        res = manager.cancel_session(EndReason.ESC)
        assert not manager.is_listening
        assert res is not None
        assert res.end_reason == EndReason.ESC


def test_recordings_retention_pruning():
    with tempfile.TemporaryDirectory() as td:
        rec_dir = Path(td)
        config = create_test_config(rec_dir)
        mock_audio = MockAudioCapture()
        mock_vad = MockVAD()
        manager = SessionManager(config=config, vad=mock_vad, audio_capture=mock_audio)

        # Generate 5 sessions (max kept is 3)
        for i in range(5):
            manager.start_session(SessionMode.COMMAND)
            mock_audio.feed_chunk(np.zeros(512, dtype=np.float32))
            time.sleep(0.01)
            manager.stop_session(EndReason.ESC)

        wav_files = list(rec_dir.glob("*.wav"))
        assert len(wav_files) <= 3, f"Expected at most 3 WAV files, found {len(wav_files)}"
