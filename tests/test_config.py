"""Unit tests verifying configuration loading and schema."""

from pathlib import Path
from engine.config import load_config


def test_config_loading():
    config = load_config()

    # Hotkeys
    assert config.hotkeys.command_mode == "<ctrl>+<alt>+m"
    assert config.hotkeys.dictation_mode == "<ctrl>+<alt>+d"
    assert config.hotkeys.cancel == "<esc>"

    # Timeouts
    assert config.timeouts.command_silence_sec == 1.5
    assert config.timeouts.command_no_speech_sec == 5.0
    assert config.timeouts.dictation_silence_sec == 8.0

    # Audio
    assert config.audio.sample_rate == 16000
    assert config.audio.chunk_size == 512
    assert config.audio.channels == 1

    # VAD
    assert 0.0 < config.vad.speech_threshold < 1.0
    assert config.vad.min_speech_chunks >= 1

    # Storage
    assert isinstance(config.storage.recordings_dir, Path)
    assert config.storage.max_recordings_kept == 20

    # Whisper
    assert config.whisper.model_size == "small"
    assert config.whisper.language == "en"
    assert config.whisper.initial_prompt == ""
    assert isinstance(config.whisper.models_dir, Path)

    # Normalization
    assert config.normalization.enabled is True
    assert config.normalization.target_peak_dbfs == -3.0
    assert config.normalization.max_gain_db == 20.0

    # Dictation
    assert config.dictation.chunk_pause_sec == 0.8
    assert config.dictation.min_speech_ms == 250
    assert config.dictation.stop_phrases == ["stop dictation", "end dictation"]
    assert config.dictation.fuzzy_tolerance == 0.2
    assert config.dictation.context_prompt is True
    assert config.dictation.clipboard_restore_delay_ms == 400
    assert config.dictation.modifier_release_timeout_ms == 500
    assert config.dictation.trailing_space is False
