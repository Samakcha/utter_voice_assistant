"""
Engine package for Voice Assistant.
Contains audio capture, VAD, session lifecycle, and domain models.
Strictly decoupled from any UI / presentation code.
"""

from .audio_capture import AudioCapture, AudioCaptureError
from .config import AppConfig, WhisperConfig, load_config
from .models import EndReason, SessionMode, SessionResult, TranscriptionResult
from .session import SessionManager
from .transcriber import BaseTranscriber, MLXWhisperTranscriber
from .vad import SileroVAD

__all__ = [
    "AppConfig",
    "AudioCapture",
    "AudioCaptureError",
    "BaseTranscriber",
    "EndReason",
    "MLXWhisperTranscriber",
    "SessionManager",
    "SessionMode",
    "SessionResult",
    "SileroVAD",
    "TranscriptionResult",
    "WhisperConfig",
    "load_config",
]
