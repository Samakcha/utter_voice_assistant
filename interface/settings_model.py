"""
What the Settings window shows and what it allows, as plain logic (testable without a
screen). interface/settings_window.py draws it; engine/settings_file.py writes the file.

Every change is checked first (a shortcut that clashes, a stop phrase that is too short),
then saved straight away and handed to the app, which uses it from the next dictation or
command on (no restart; only switching the speech model needs one).
"""

from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from engine.config import AppConfig
from engine.notes_writer import generate_note_title
from engine.settings_file import SettingsSaveError, save_settings
from engine.stop_phrases import COMMON_WORDS
from engine.vocabulary import VOCABULARY_FILE_TEMPLATE, parse_vocabulary
from .key_events import FLAG_COMMAND, FLAG_CONTROL, FLAG_OPTION, FLAG_SHIFT, KEYCODES, parse_key_combo
from .setup_flow import format_hotkey

logger = logging.getLogger(__name__)

# ---- Pages ----------------------------------------------------------------------------------

GENERAL = "general"
SHORTCUTS = "shortcuts"
DICTATION = "dictation"
NOTES = "notes"
WORDS = "words"
PRIVACY = "privacy"
ADVANCED = "advanced"

# (page, sidebar title, SF Symbol)
PAGES: List[Tuple[str, str, str]] = [
    (GENERAL, "General", "gearshape"),
    (SHORTCUTS, "Shortcuts", "keyboard"),
    (DICTATION, "Dictation", "text.cursor"),
    (NOTES, "Notes", "note.text"),
    (WORDS, "Words & Apps", "character.book.closed"),
    (PRIVACY, "Privacy", "hand.raised"),
    (ADVANCED, "Advanced", "slider.horizontal.3"),
]


@dataclass(frozen=True)
class Toggle:
    """An on/off switch for one setting."""
    section: str
    key: str
    title: str
    detail: str = ""
    page: str = ""
    depends_on: Optional[Tuple[str, str]] = None  # greyed out while that switch is off


@dataclass(frozen=True)
class Choice:
    """A pop-up menu of values for one setting."""
    section: str
    key: str
    title: str
    options: Tuple[Tuple[str, object], ...]
    detail: str = ""
    page: str = ""


def _seconds(values: Sequence[float]) -> Tuple[Tuple[str, float], ...]:
    return tuple((f"{v:g} seconds" if v != 1 else "1 second", float(v)) for v in values)


TOGGLES: List[Toggle] = [
    Toggle("hotkeys", "hold_to_talk", "Hold to talk",
           "Hold the dictation shortcut while you speak; the text is typed when you let go. "
           "A quick tap still starts and stops dictation.", SHORTCUTS),
    Toggle("cleanup", "enabled", "Tidy up dictation",
           "Fix what you say before it is typed. Rules only; nothing leaves this Mac.", DICTATION),
    Toggle("cleanup", "fillers", "Remove filler sounds", "“um”, “uh”, “er”, “hmm”",
           DICTATION, depends_on=("cleanup", "enabled")),
    Toggle("cleanup", "repeats", "Remove accidental repeats", "“the the” becomes “the”",
           DICTATION, depends_on=("cleanup", "enabled")),
    Toggle("cleanup", "corrections", "Follow your corrections",
           "“Monday, no wait, Tuesday” becomes “Tuesday”; “scratch that” removes the last sentence.",
           DICTATION, depends_on=("cleanup", "enabled")),
    Toggle("dictation", "trailing_space", "Add a space after the text",
           "Handy when you dictate several sentences one after another.", DICTATION),
    Toggle("notes", "reveal_after_save", "Show Notes after saving",
           "Off: the note is saved quietly and you stay where you are.", NOTES),
    Toggle("notes", "ready_sound", "Play a sound when Utter is listening",
           "A short tick when the voice note starts recording.", NOTES),
    Toggle("vocabulary", "enabled", "Use my word list",
           "Names and terms below are spelled the way you write them (dictation and notes only).", WORDS),
    Toggle("vocabulary", "auto_correct", "Fix close mishearings",
           "“Rahool” becomes “Rahul”. Ordinary English words are never changed.",
           WORDS, depends_on=("vocabulary", "enabled")),
    Toggle("apps", "enabled", "Open apps by voice",
           "“Open Safari”, “launch Calculator” with the command shortcut.", WORDS),
    Toggle("privacy", "keep_recordings", "Keep recordings",
           "Off: your voice is turned into text in memory and never saved. "
           "On: the last 20 recordings are kept, for checking problems.", PRIVACY),
    Toggle("privacy", "redact_names_in_logs", "Hide names in the log",
           "Contact names and searched names are written as ••• in the log.", PRIVACY),
    Toggle("privacy", "keep_whatsapp_audio", "Keep audio of WhatsApp commands",
           "Off: recordings of “search WhatsApp for …” are always deleted.", PRIVACY,
           depends_on=("privacy", "keep_recordings")),
    Toggle("privacy", "keep_audio_when_whatsapp_front", "Keep audio while WhatsApp is open",
           "Off: dictation into WhatsApp is never kept.", PRIVACY,
           depends_on=("privacy", "keep_recordings")),
]

CHOICES: List[Choice] = [
    Choice("timeouts", "dictation_silence_sec", "Stop dictation after a silence of",
           _seconds([3, 5, 8, 10, 15, 20, 30]),
           "Or say “stop dictation”, or tap the shortcut again.", DICTATION),
    Choice("timeouts", "command_silence_sec", "Run a command after a pause of",
           _seconds([1, 1.5, 2, 2.5, 3]),
           "Short commands like “open notes” still run as soon as they are recognised.", SHORTCUTS),
    Choice("notes", "silence_sec", "Save a voice note after a silence of",
           _seconds([5, 10, 15, 20, 30, 60]), "Or say “stop note”.", NOTES),
]

# Speech models Utter knows (model_size, what it is called in the window)
MODELS: List[Tuple[str, str]] = [
    ("large-v3-turbo", "Best accuracy (1.5 GB)"),
    ("small", "Faster, less accurate"),
]


def toggle_value(config: AppConfig, section: str, key: str) -> bool:
    if (section, key) == ("vocabulary", "enabled"):
        return config.vocabulary.file is not None
    return bool(getattr(getattr(config, section), key))


def choice_options(choice: Choice, config: AppConfig) -> List[Tuple[str, object]]:
    """The menu's entries, with the current value added if it was set by hand."""
    options = list(choice.options)
    current = getattr(getattr(config, choice.section), choice.key)
    if all(abs(float(v) - float(current)) > 1e-9 for _, v in options):
        options.append((f"{float(current):g} seconds", float(current)))
        options.sort(key=lambda o: float(o[1]))
    return options


def choice_index(choice: Choice, config: AppConfig) -> int:
    current = float(getattr(getattr(config, choice.section), choice.key))
    for i, (_, v) in enumerate(choice_options(choice, config)):
        if abs(float(v) - current) < 1e-9:
            return i
    return 0


# ---- Shortcuts --------------------------------------------------------------------------------

_KEY_NAMES: Dict[int, str] = {}
for _name, _code in KEYCODES.items():
    _KEY_NAMES.setdefault(_code, _name)  # first name wins: "enter" over "return", "esc"

ESC_KEYCODE = KEYCODES["esc"]
SHORTCUT_NAMES = {"command_mode": "commands", "dictation_mode": "dictation"}


def combo_from_key(keycode: int, flags: int) -> Optional[str]:
    """A pressed key plus held modifiers as a hotkey string ("<ctrl>+<alt>+m"), or None for a
    key Utter can't use. The modifier values of NSEvent and Quartz are the same."""
    key = _KEY_NAMES.get(int(keycode))
    if key is None:
        return None
    parts = []
    for flag, name in ((FLAG_CONTROL, "<ctrl>"), (FLAG_OPTION, "<alt>"),
                       (FLAG_SHIFT, "<shift>"), (FLAG_COMMAND, "<cmd>")):
        if flags & flag:
            parts.append(name)
    parts.append(key if len(key) == 1 else f"<{key}>")
    return "+".join(parts)


def check_shortcut(which: str, combo: Optional[str], config: AppConfig) -> Optional[str]:
    """What is wrong with a new shortcut for "command_mode" / "dictation_mode" (None: fine)."""
    if combo is None:
        return "That key can't be used for a shortcut. Try a letter or a number."
    try:
        parsed = parse_key_combo(combo)
    except ValueError:
        return "That key can't be used for a shortcut. Try a letter or a number."
    if parsed.keycode == ESC_KEYCODE:
        return "Esc stops Utter while it listens, so it can't be a shortcut."
    if not parsed.modifiers & (FLAG_CONTROL | FLAG_OPTION | FLAG_COMMAND):
        return "Hold ⌃ Control, ⌥ Option or ⌘ Command too, so normal typing never starts Utter."
    if not parsed.modifiers & (FLAG_CONTROL | FLAG_OPTION):
        return (f"{format_hotkey(combo)} belongs to other apps (⌘ shortcuts like ⌘C copy). "
                "Add ⌃ Control or ⌥ Option.")
    other = "dictation_mode" if which == "command_mode" else "command_mode"
    other_combo = getattr(config.hotkeys, other)
    try:
        if parse_key_combo(other_combo) == parsed:
            return f"{format_hotkey(combo)} is already the shortcut for {SHORTCUT_NAMES[other]}."
    except ValueError:
        pass
    return None


# ---- Text settings ------------------------------------------------------------------------------

def phrases_text(phrases: Sequence[str]) -> str:
    return ", ".join(phrases)


def parse_phrases(text: str) -> List[str]:
    seen, result = set(), []
    for part in text.replace("\n", ",").split(","):
        phrase = " ".join(part.strip().split())
        if phrase and phrase.lower() not in seen:
            seen.add(phrase.lower())
            result.append(phrase)
    return result


def check_phrases(phrases: Sequence[str]) -> Optional[str]:
    for phrase in phrases:
        cleaned = phrase.lower()
        if cleaned in COMMON_WORDS or len(cleaned.split()) < 2:
            return (f"“{phrase}” is too short: use two or more words, like “stop dictation”, "
                    "so normal speech never ends it by accident.")
    return None


def check_note_title(template: str) -> Optional[str]:
    if not template.strip():
        return "The note title can't be empty. {date} and {time} fill in the date and time."
    try:
        generate_note_title(template)
    except (KeyError, IndexError, ValueError):
        return "Use only {date} and {time} in the title (in curly brackets)."
    return None


def parse_aliases(text: str) -> Tuple[Dict[str, str], Optional[str]]:
    """Lines "browser = Safari" -> {"browser": "Safari"}, or the first problem found."""
    aliases: Dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            return aliases, f"Line {number}: write it as “what you say = App Name”, like “browser = Safari”."
        spoken, app = (" ".join(p.strip().split()) for p in line.split("=", 1))
        if not spoken or not app:
            return aliases, f"Line {number}: write it as “what you say = App Name”, like “browser = Safari”."
        aliases[spoken.lower()] = app
    return aliases, None


def aliases_text(aliases: Dict[str, str]) -> str:
    return "\n".join(f"{k} = {v}" for k, v in aliases.items())


def word_list_terms(path: Optional[Path]) -> List[str]:
    if path is None or not path.is_file():
        return []
    try:
        return parse_vocabulary(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as e:
        logger.warning("Could not read the word list: %s", e)
        return []


def word_list_file_text(old_text: Optional[str], terms_text: str) -> str:
    """The word-list file with new terms: the explanation at the top is kept (or added)."""
    header: List[str] = []
    for line in (old_text if old_text is not None else VOCABULARY_FILE_TEMPLATE).splitlines():
        if line.strip() and not line.strip().startswith("#"):
            break
        header.append(line)
    while header and not header[-1].strip():
        header.pop()
    terms = parse_vocabulary(terms_text)
    return "\n".join(header + terms) + "\n"


# ---- Saving ---------------------------------------------------------------------------------------

@dataclass
class SettingsModel:
    """The settings as they are now, and the one way to change them."""
    config: AppConfig
    settings_file: Path
    default_config: Optional[Path] = None
    on_saved: Callable[[AppConfig, AppConfig], None] = lambda old, new: None
    saver: Callable[..., AppConfig] = field(default=save_settings)

    def save(self, changes: Dict[str, Dict[str, object]],
             tables: Optional[Dict[str, Dict[str, str]]] = None) -> Optional[str]:
        """Save and apply; returns a message for the window if it could not be saved."""
        try:
            new = self.saver(self.settings_file, changes, tables, default_config=self.default_config)
        except SettingsSaveError as e:
            logger.error("Settings not saved: %s", e)
            return str(e)
        old, self.config = self.config, new
        logger.info("Settings saved: %s", ", ".join(
            [f"{s}.{k}" for s, values in changes.items() for k in values] + list((tables or {}).keys())))
        try:
            self.on_saved(old, new)
        except Exception as e:
            logger.error("Could not apply the new settings: %s", e, exc_info=True)
        return None

    def set_toggle(self, toggle: Toggle, on: bool) -> Optional[str]:
        return self.save({toggle.section: {toggle.key: bool(on)}})

    def set_choice(self, choice: Choice, index: int) -> Optional[str]:
        options = choice_options(choice, self.config)
        if not 0 <= index < len(options):
            return None
        return self.save({choice.section: {choice.key: options[index][1]}})

    def set_shortcut(self, which: str, combo: Optional[str]) -> Optional[str]:
        problem = check_shortcut(which, combo, self.config)
        return problem or self.save({"hotkeys": {which: combo}})

    def set_phrases(self, section: str, text: str) -> Optional[str]:
        phrases = parse_phrases(text)
        if not phrases:
            return "Keep at least one stop phrase, like “stop dictation”."
        return check_phrases(phrases) or self.save({section: {"stop_phrases": phrases}})

    def set_note_title(self, template: str) -> Optional[str]:
        return check_note_title(template) or self.save({"notes": {"title_template": template.strip()}})

    def set_notes_folder(self, folder: str) -> Optional[str]:
        return self.save({"notes": {"folder": " ".join(folder.strip().split())}})

    def set_model(self, model_size: str) -> Optional[str]:
        return self.save({"whisper": {"model_size": model_size}})

    def set_aliases(self, text: str) -> Optional[str]:
        aliases, problem = parse_aliases(text)
        return problem or self.save({}, {"apps.aliases": aliases})

    def save_word_list(self, terms_text: str) -> Optional[str]:
        path = self.config.vocabulary.file
        if path is None:
            return "Turn on “Use my word list” first."
        try:
            old = path.read_text(encoding="utf-8") if path.is_file() else None
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(word_list_file_text(old, terms_text), encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            return f"Could not save the word list: {e}"
        logger.info("Word list saved from Settings.")
        return None


def installed_models(models_dir: Path, current: str) -> List[Tuple[str, str]]:
    """The speech models that are on this Mac (and the current one, even if missing)."""
    found = []
    for size, title in MODELS:
        if size == current or any((models_dir / name).is_dir()
                                  for name in (f"whisper-{size}-mlx", size)):
            found.append((size, title))
    if current not in [s for s, _ in found]:
        found.append((current, current))
    return found
