"""Configuration loader and schema for the Voice Assistant engine."""

from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import List, Optional


@dataclass(frozen=True)
class HotkeysConfig:
    command_mode: str
    dictation_mode: str
    cancel: str


@dataclass(frozen=True)
class TimeoutsConfig:
    command_silence_sec: float
    command_no_speech_sec: float
    dictation_silence_sec: float


@dataclass(frozen=True)
class AudioConfig:
    sample_rate: int
    chunk_size: int
    channels: int


@dataclass(frozen=True)
class VADConfig:
    speech_threshold: float
    min_speech_chunks: int


@dataclass(frozen=True)
class StorageConfig:
    recordings_dir: Path
    max_recordings_kept: int


@dataclass(frozen=True)
class WhisperConfig:
    model_size: str
    language: str
    initial_prompt: str
    models_dir: Path


@dataclass(frozen=True)
class NormalizationConfig:
    enabled: bool
    target_peak_dbfs: float
    max_gain_db: float


@dataclass(frozen=True)
class DictationConfig:
    chunk_pause_sec: float
    min_speech_ms: int
    stop_phrases: List[str]
    fuzzy_tolerance: float
    context_prompt: bool
    clipboard_restore_delay_ms: int
    modifier_release_timeout_ms: int
    trailing_space: bool


@dataclass(frozen=True)
class NotesConfig:
    stop_phrases: List[str]
    silence_sec: float
    title_template: str
    folder: str
    reveal_after_save: bool
    ready_sound: bool


@dataclass(frozen=True)
class PrivacyConfig:
    redact_names_in_logs: bool = True
    keep_whatsapp_audio: bool = False
    keep_audio_when_whatsapp_front: bool = False


@dataclass(frozen=True)
class AppConfig:
    hotkeys: HotkeysConfig
    timeouts: TimeoutsConfig
    audio: AudioConfig
    vad: VADConfig
    storage: StorageConfig
    whisper: WhisperConfig
    normalization: NormalizationConfig
    dictation: DictationConfig
    notes: NotesConfig
    privacy: PrivacyConfig = PrivacyConfig()


def load_config(config_path: Optional[Path] = None) -> AppConfig:
    """Load and parse the configuration from config.toml."""
    if config_path is None:
        # Default to config.toml in project root
        config_path = Path(__file__).resolve().parent.parent / "config.toml"

    if not config_path.is_file():
        raise FileNotFoundError(f"Config file not found at: {config_path}")

    with open(config_path, "rb") as f:
        data = tomllib.load(f)

    # Resolve recordings directory relative to project root if it is a relative path
    project_root = config_path.parent
    recordings_dir = Path(data["storage"]["recordings_dir"])
    if not recordings_dir.is_absolute():
        recordings_dir = (project_root / recordings_dir).resolve()

    whisper_data = data.get("whisper", {})
    models_dir = Path(whisper_data.get("models_dir", "./models"))
    if not models_dir.is_absolute():
        models_dir = (project_root / models_dir).resolve()

    norm_data = data.get("normalization", {})
    normalization = NormalizationConfig(
        enabled=bool(norm_data.get("enabled", True)),
        target_peak_dbfs=float(norm_data.get("target_peak_dbfs", -3.0)),
        max_gain_db=float(norm_data.get("max_gain_db", 20.0)),
    )

    dict_data = data.get("dictation", {})
    dictation = DictationConfig(
        chunk_pause_sec=float(dict_data.get("chunk_pause_sec", 0.8)),
        min_speech_ms=int(dict_data.get("min_speech_ms", 250)),
        stop_phrases=[str(p) for p in dict_data.get("stop_phrases", ["stop dictation", "end dictation"])],
        fuzzy_tolerance=float(dict_data.get("fuzzy_tolerance", 0.2)),
        context_prompt=bool(dict_data.get("context_prompt", True)),
        clipboard_restore_delay_ms=int(dict_data.get("clipboard_restore_delay_ms", 400)),
        modifier_release_timeout_ms=int(dict_data.get("modifier_release_timeout_ms", 500)),
        trailing_space=bool(dict_data.get("trailing_space", False)),
    )

    notes_data = data.get("notes", {})
    notes = NotesConfig(
        stop_phrases=[str(p) for p in notes_data.get("stop_phrases", ["stop note", "stop dictation", "end dictation"])],
        silence_sec=float(notes_data.get("silence_sec", 10.0)),
        title_template=str(notes_data.get("title_template", "Voice note {date} {time}")),
        folder=str(notes_data.get("folder", "")),
        reveal_after_save=bool(notes_data.get("reveal_after_save", False)),
        ready_sound=bool(notes_data.get("ready_sound", True)),
    )

    privacy_data = data.get("privacy", {})
    privacy = PrivacyConfig(
        redact_names_in_logs=bool(privacy_data.get("redact_names_in_logs", True)),
        keep_whatsapp_audio=bool(privacy_data.get("keep_whatsapp_audio", False)),
        keep_audio_when_whatsapp_front=bool(privacy_data.get("keep_audio_when_whatsapp_front", False)),
    )

    return AppConfig(
        hotkeys=HotkeysConfig(
            command_mode=data["hotkeys"]["command_mode"],
            dictation_mode=data["hotkeys"]["dictation_mode"],
            cancel=data["hotkeys"]["cancel"],
        ),
        timeouts=TimeoutsConfig(
            command_silence_sec=float(data["timeouts"]["command_silence_sec"]),
            command_no_speech_sec=float(data["timeouts"]["command_no_speech_sec"]),
            dictation_silence_sec=float(data["timeouts"]["dictation_silence_sec"]),
        ),
        audio=AudioConfig(
            sample_rate=int(data["audio"]["sample_rate"]),
            chunk_size=int(data["audio"]["chunk_size"]),
            channels=int(data["audio"]["channels"]),
        ),
        vad=VADConfig(
            speech_threshold=float(data["vad"]["speech_threshold"]),
            min_speech_chunks=int(data["vad"]["min_speech_chunks"]),
        ),
        storage=StorageConfig(
            recordings_dir=recordings_dir,
            max_recordings_kept=int(data["storage"]["max_recordings_kept"]),
        ),
        whisper=WhisperConfig(
            model_size=str(whisper_data.get("model_size", "small")),
            language=str(whisper_data.get("language", "en")),
            initial_prompt=str(whisper_data.get("initial_prompt", "")),
            models_dir=models_dir,
        ),
        normalization=normalization,
        dictation=dictation,
        notes=notes,
        privacy=privacy,
    )
