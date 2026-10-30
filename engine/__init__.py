"""
Engine package for Voice Assistant.
Contains audio capture, VAD, session lifecycle, and domain models.
Strictly decoupled from any UI / presentation code.
"""

from .audio_capture import AudioCapture, AudioCaptureError
from .audio_utils import compute_audio_levels, normalize_audio
from .config import AppConfig, NormalizationConfig, WhisperConfig, load_config
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
    "NormalizationConfig",
    "SessionManager",
    "SessionMode",
    "SessionResult",
    "SileroVAD",
    "TranscriptionResult",
    "WhisperConfig",
    "compute_audio_levels",
    "load_config",
    "normalize_audio",
]
