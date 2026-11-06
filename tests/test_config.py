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

    # Privacy: no audio kept by default, and personal data lives outside the project folder
    project_root = Path(__file__).resolve().parent.parent
    assert config.privacy.keep_recordings is False
    for data_dir in (config.storage.recordings_dir, config.storage.note_backups_dir):
        assert data_dir.is_absolute()
        assert "~" not in str(data_dir)
        assert project_root not in data_dir.parents, f"{data_dir} is inside the project folder"
        assert "Library/Application Support/VoiceAssistant" in str(data_dir)

    # Personal word list: on by default, stored with the user's other data
    assert config.vocabulary.file is not None
    assert config.vocabulary.file.is_absolute()
    assert project_root not in config.vocabulary.file.parents
    assert str(config.vocabulary.file).endswith("Library/Application Support/VoiceAssistant/vocabulary.txt")
    assert config.vocabulary.max_prompt_terms == 50
    assert config.vocabulary.auto_correct is True

    # Whisper
    assert config.whisper.model_size == "large-v3-turbo"
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

    # Notes
    assert config.notes.stop_phrases == ["stop note", "stop dictation", "end dictation"]
    assert config.notes.silence_sec == 10.0
    assert config.notes.title_template == "Voice note {date} {time}"
    assert config.notes.folder == ""
    assert config.notes.reveal_after_save is False
    assert config.notes.ready_sound is True


def test_word_list_can_be_turned_off(tmp_path):
    source = (Path(__file__).resolve().parent.parent / "config.toml").read_text(encoding="utf-8")
    assert "[vocabulary]\n" in source
    turned_off = source.replace("[vocabulary]\n", "[vocabulary]\nenabled = false\n", 1)
    # Drop the original enabled line so the key isn't defined twice
    turned_off = turned_off.replace("\nenabled = true\nfile =", "\nfile =", 1)
    path = tmp_path / "config.toml"
    path.write_text(turned_off, encoding="utf-8")

    config = load_config(path)
    assert config.vocabulary.file is None
