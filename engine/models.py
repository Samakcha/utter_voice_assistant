"""Domain models and data structures for the voice assistant engine."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional
import numpy as np


class SessionMode(str, Enum):
    """Voice session mode."""
    COMMAND = "command"
    DICTATION = "dictation"


class EndReason(str, Enum):
    """Reason why an active listening session ended."""
    SILENCE = "silence"          # Trailing silence timeout elapsed
    HOTKEY = "hotkey"            # Stopped by pressing hotkey again (Dictation)
    ESC = "esc"                  # Canceled by pressing Esc
    NO_SPEECH = "no_speech"      # Canceled quietly because no speech detected within time limit
    STOPPED = "stopped"          # Stopped because the app was stopped/paused
    STOP_PHRASE = "stop_phrase"  # Ended by spoken stop phrase at tail of dictation


@dataclass(frozen=True)
class DictationChunk:
    """Chunk of audio extracted during dictation pause for progressive transcription."""
    session_id: str
    chunk_index: int
    audio: np.ndarray
    speech_duration_ms: float
    is_final: bool


@dataclass(frozen=True)
class SessionResult:
    """Summary of a completed or canceled recording session."""
    mode: SessionMode
    end_reason: EndReason
    duration_sec: float
    audio_path: Optional[Path]
    speech_detected: bool
    sample_rate: int
    total_samples: int
    session_id: str = ""
    peak_dbfs: float = -100.0
    rms_dbfs: float = -100.0
    input_device: str = "Unknown"
    trailing_silence_sec: float = 0.0
    auto_stop_time: float = 0.0

    @property
    def was_quiet_cancel(self) -> bool:
        """True if the session ended quietly without useful speech."""
        return self.end_reason == EndReason.NO_SPEECH or (
            not self.speech_detected and self.end_reason in (EndReason.ESC, EndReason.STOPPED)
        )


@dataclass(frozen=True)
class TranscriptionResult:
    """Result of speech-to-text transcription."""
    text: str
    language: str
    audio_duration_sec: float
    processing_time_sec: float
    applied_gain_db: float = 0.0

    @property
    def real_time_factor(self) -> float:
        """Ratio of processing time to audio duration. Lower is faster (<1.0 is real-time)."""
        if self.audio_duration_sec <= 0:
            return 0.0
        return self.processing_time_sec / self.audio_duration_sec
