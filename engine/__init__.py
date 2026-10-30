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
from .chunk_joiner import join_transcription_chunks
from .commands import CommandMatch, match_command, normalize_command_text
from .config import AppConfig, DictationConfig, NormalizationConfig, WhisperConfig, load_config
from .insertion import FormattedInsertion, format_dictation_insertion
from .models import DictationChunk, EndReason, SessionMode, SessionResult, TranscriptionResult
from .session import SessionManager
from .stop_phrases import StopPhraseMatch, detect_stop_phrase, validate_stop_phrases
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
    "DictationChunk",
    "DictationConfig",
    "EndReason",
    "FormattedInsertion",
    "MLXWhisperTranscriber",
    "NormalizationConfig",
    "SessionManager",
    "SessionMode",
    "SessionResult",
    "SileroVAD",
    "StopPhraseMatch",
    "TranscriptionResult",
    "WhisperConfig",
    "compute_audio_levels",
    "detect_stop_phrase",
    "execute_action",
    "execute_action_async",
    "format_dictation_insertion",
    "get_action",
    "join_transcription_chunks",
    "load_config",
    "match_command",
    "normalize_audio",
    "normalize_command_text",
    "validate_stop_phrases",
]
