"""Domain models and data structures for the voice assistant engine."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


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

    @property
    def was_quiet_cancel(self) -> bool:
        """True if the session ended quietly without useful speech."""
        return self.end_reason == EndReason.NO_SPEECH or (
            not self.speech_detected and self.end_reason in (EndReason.ESC, EndReason.STOPPED)
        )
