"""Configuration loader and schema for the Voice Assistant engine."""

from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Optional


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
class AppConfig:
    hotkeys: HotkeysConfig
    timeouts: TimeoutsConfig
    audio: AudioConfig
    vad: VADConfig
    storage: StorageConfig
    whisper: WhisperConfig


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
    )
