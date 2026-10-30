"""
Engine package for Voice Assistant.
Contains audio capture, VAD, session lifecycle, and domain models.
Strictly decoupled from any UI / presentation code.
"""

from .actions import (
    ActionDefinition,
    ActionRisk,
    ActionResult,
    execute_action,
    execute_action_async,
    get_action,
)
from .audio_capture import AudioCapture, AudioCaptureError
from .audio_utils import compute_audio_levels, normalize_audio
from .commands import CommandMatch, match_command, normalize_command_text
from .config import AppConfig, NormalizationConfig, WhisperConfig, load_config
from .models import EndReason, SessionMode, SessionResult, TranscriptionResult
from .session import SessionManager
from .transcriber import BaseTranscriber, MLXWhisperTranscriber
from .vad import SileroVAD

__all__ = [
    "ActionDefinition",
    "ActionRisk",
    "ActionResult",
    "AppConfig",
    "AudioCapture",
    "AudioCaptureError",
    "BaseTranscriber",
    "CommandMatch",
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
    "execute_action",
    "execute_action_async",
    "get_action",
    "load_config",
    "match_command",
    "normalize_audio",
    "normalize_command_text",
]
