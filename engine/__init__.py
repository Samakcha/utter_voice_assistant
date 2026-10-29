"""
Engine package for Voice Assistant.
Contains audio capture, VAD, session lifecycle, and domain models.
Strictly decoupled from any UI / presentation code.
"""

from .audio_capture import AudioCapture, AudioCaptureError
from .config import AppConfig, load_config
from .models import EndReason, SessionMode, SessionResult
from .session import SessionManager
from .vad import SileroVAD

__all__ = [
    "AppConfig",
    "AudioCapture",
    "AudioCaptureError",
    "EndReason",
    "SessionManager",
    "SessionMode",
    "SessionResult",
    "SileroVAD",
    "load_config",
]
