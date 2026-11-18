"""Configuration loader and schema for the Voice Assistant engine."""

from dataclasses import dataclass, field
from pathlib import Path
import tomllib
from typing import Dict, List, Optional


@dataclass(frozen=True)
class HotkeysConfig:
    command_mode: str
    dictation_mode: str
    cancel: str
    # Hold-to-talk for dictation: tap the dictation hotkey to toggle (as before), or hold it
    # at least hold_min_sec and dictation stops when you let go. False keeps toggle only.
    hold_to_talk: bool = False
    hold_min_sec: float = 0.4
    # Keep recording this long after the keys are released so the last word isn't clipped
    hold_release_tail_sec: float = 0.3


@dataclass(frozen=True)
class TimeoutsConfig:
    command_silence_sec: float
    command_no_speech_sec: float
    dictation_silence_sec: float
    # After this much silence, command mode transcribes in the background; a complete fixed
    # command (no name in it) then runs without waiting for command_silence_sec. 0 turns it off.
    command_early_check_sec: float = 0.0


@dataclass(frozen=True)
class AudioConfig:
    sample_rate: int
    chunk_size: int
    channels: int


@dataclass(frozen=True)
class VADConfig:
    speech_threshold: float
    min_speech_chunks: int


# Per-user data lives outside the project folder, in the standard macOS locations.
DEFAULT_DATA_DIR = Path("~/Library/Application Support/Utter")
# Before the app was named Utter; renamed to DEFAULT_DATA_DIR on first start (data_folder.py)
LEGACY_DATA_DIR = Path("~/Library/Application Support/VoiceAssistant")
DEFAULT_LOG_DIR = Path("~/Library/Logs/Utter")
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def running_as_app(project_root: Optional[Path] = None) -> bool:
    """True inside Utter.app (code in Utter.app/Contents/Resources/app), False from the project folder."""
    root = project_root or PROJECT_ROOT
    return root.parent.name == "Resources" and root.parent.parent.name == "Contents"


# The settings that ship with Utter. The user's own copy is <data folder>/config.toml.
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.toml"
USER_CONFIG_NAME = "config.toml"
# Where models were kept before they moved into the data folder (still searched as a fallback)
LEGACY_MODELS_DIR = PROJECT_ROOT / "models"


@dataclass(frozen=True)
class DataPaths:
    """Every place Utter keeps things outside the app itself (tests point these at tmp_path)."""
    data_dir: Path
    legacy_data_dir: Path
    log_dir: Path
    legacy_models_dir: Path
    default_config: Path

    @property
    def user_config(self) -> Path:
        return self.data_dir / USER_CONFIG_NAME

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"


def data_paths() -> DataPaths:
    return DataPaths(
        data_dir=DEFAULT_DATA_DIR.expanduser(),
        legacy_data_dir=LEGACY_DATA_DIR.expanduser(),
        log_dir=DEFAULT_LOG_DIR.expanduser(),
        legacy_models_dir=LEGACY_MODELS_DIR,
        default_config=DEFAULT_CONFIG_PATH,
    )


def _resolve_dir(value: str, project_root: Path) -> Path:
    """Expand ~ and resolve relative paths against the project root."""
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (project_root / path).resolve()
    return path


@dataclass(frozen=True)
class StorageConfig:
    recordings_dir: Path
    max_recordings_kept: int
    # Failed-note text backups (never pruned)
    note_backups_dir: Path = DEFAULT_DATA_DIR.expanduser() / "note_backups"


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
    # When False (default), session audio is never written to disk: it is transcribed
    # from memory and discarded. Turn on only for debugging or building a benchmark set.
    keep_recordings: bool = False


@dataclass(frozen=True)
class VocabularyConfig:
    """Personal word list (dictation and notes only). file=None turns it off."""
    file: Optional[Path] = None
    max_prompt_terms: int = 50
    auto_correct: bool = True


@dataclass(frozen=True)
class AppsConfig:
    """'Open <app>' by voice. aliases maps a spoken name to an installed app's name."""
    enabled: bool = True
    aliases: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CleanupConfig:
    """Tidy dictation and notes before they are pasted or saved (rules only, offline)."""
    enabled: bool = True
    fillers: bool = True  # "um", "uh"
    repeats: bool = True  # "the the"
    corrections: bool = True  # "Monday, no wait, Tuesday", "scratch that"


# Utter's Supabase project (accounts). The key is the project's *publishable* key
# (sb_publishable_..., formerly "anon"): public by design, it only lets the app ask Supabase
# to sign people in. Never put the secret key (sb_secret_..., formerly service_role) here.
SUPABASE_URL = ""
SUPABASE_ANON_KEY = ""


@dataclass(frozen=True)
class AccountConfig:
    """Sign-in (engine/account.py). Without a url and key, accounts are off and nobody has
    to sign in (developer copies before the project is set up)."""
    url: str = SUPABASE_URL
    anon_key: str = SUPABASE_ANON_KEY

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.anon_key)


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
    vocabulary: VocabularyConfig = VocabularyConfig()
    apps: AppsConfig = AppsConfig()
    cleanup: CleanupConfig = CleanupConfig()
    account: AccountConfig = AccountConfig()


def load_user_config(paths: Optional[DataPaths] = None) -> AppConfig:
    """The user's settings (<data folder>/config.toml), or the shipped defaults if there are none yet."""
    paths = paths or data_paths()
    return load_config(paths.user_config if paths.user_config.is_file() else paths.default_config)


def load_config(config_path: Optional[Path] = None) -> AppConfig:
    """Load and parse a config.toml (default: the settings that ship with Utter)."""
    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH

    if not config_path.is_file():
        raise FileNotFoundError(f"Config file not found at: {config_path}")

    with open(config_path, "rb") as f:
        data = tomllib.load(f)

    # Resolve storage directories (~ expanded; relative paths are relative to the project root)
    project_root = config_path.parent
    storage_data = data.get("storage", {})
    recordings_dir = _resolve_dir(
        str(storage_data.get("recordings_dir", DEFAULT_DATA_DIR / "recordings")), project_root
    )
    note_backups_dir = _resolve_dir(
        str(storage_data.get("note_backups_dir", DEFAULT_DATA_DIR / "note_backups")), project_root
    )

    whisper_data = data.get("whisper", {})
    models_dir = _resolve_dir(str(whisper_data.get("models_dir", DEFAULT_DATA_DIR / "models")), project_root)

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
        keep_recordings=bool(privacy_data.get("keep_recordings", False)),
    )

    vocab_data = data.get("vocabulary", {})
    vocab_enabled = bool(vocab_data.get("enabled", True))
    vocabulary = VocabularyConfig(
        file=_resolve_dir(
            str(vocab_data.get("file", DEFAULT_DATA_DIR / "vocabulary.txt")), project_root
        ) if vocab_enabled else None,
        max_prompt_terms=int(vocab_data.get("max_prompt_terms", 50)),
        auto_correct=bool(vocab_data.get("auto_correct", True)),
    )

    apps_data = data.get("apps", {})
    raw_aliases = apps_data.get("aliases", {})
    apps = AppsConfig(
        enabled=bool(apps_data.get("enabled", True)),
        aliases={
            str(k): str(v) for k, v in (raw_aliases.items() if isinstance(raw_aliases, dict) else [])
            if str(k).strip() and str(v).strip()
        },
    )

    cleanup_data = data.get("cleanup", {})
    cleanup = CleanupConfig(
        enabled=bool(cleanup_data.get("enabled", True)),
        fillers=bool(cleanup_data.get("fillers", True)),
        repeats=bool(cleanup_data.get("repeats", True)),
        corrections=bool(cleanup_data.get("corrections", True)),
    )

    return AppConfig(
        hotkeys=HotkeysConfig(
            command_mode=data["hotkeys"]["command_mode"],
            dictation_mode=data["hotkeys"]["dictation_mode"],
            cancel=data["hotkeys"]["cancel"],
            hold_to_talk=bool(data["hotkeys"].get("hold_to_talk", True)),
            hold_min_sec=float(data["hotkeys"].get("hold_min_sec", 0.4)),
            hold_release_tail_sec=float(data["hotkeys"].get("hold_release_tail_sec", 0.3)),
        ),
        timeouts=TimeoutsConfig(
            command_silence_sec=float(data["timeouts"]["command_silence_sec"]),
            command_no_speech_sec=float(data["timeouts"]["command_no_speech_sec"]),
            dictation_silence_sec=float(data["timeouts"]["dictation_silence_sec"]),
            command_early_check_sec=float(data["timeouts"].get("command_early_check_sec", 0.6)),
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
            max_recordings_kept=int(storage_data.get("max_recordings_kept", 20)),
            note_backups_dir=note_backups_dir,
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
        vocabulary=vocabulary,
        apps=apps,
        cleanup=cleanup,
        account=_load_account(data.get("account", {})),
    )


def _load_account(data: dict) -> AccountConfig:
    """[account] in config.toml can point a developer copy at another project; normally empty."""
    return AccountConfig(
        url=str(data.get("url") or SUPABASE_URL).strip(),
        anon_key=str(data.get("anon_key") or SUPABASE_ANON_KEY).strip(),
    )
