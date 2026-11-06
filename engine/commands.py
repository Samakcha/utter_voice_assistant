"""
Rule layer for voice commands.
Performs normalized whole-utterance pattern matching without loose substring search.
Pure engine component with NO UI code.
"""

from dataclasses import dataclass
import logging
import re
from typing import Optional
import unicodedata

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommandMatch:
    """Result of a successful rule layer command match."""
    action_id: str
    normalized_text: str
    matched_variant: str
    extracted_name: Optional[str] = None


def normalize_command_text(text: str) -> str:
    """
    Normalize transcript for command matching:
    - Lowercase
    - Replace punctuation with space
    - Collapse multiple whitespaces into a single space
    """
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


# Regex components for the "open Notes" command
# Optional politeness wrappers (salutations, modal phrases, directives)
_POLITENESS_PREFIX = (
    r"(?:"
    r"(?:hey|hi|hello)\s+|"
    r"(?:can|could|would|will)\s+you\s+(?:please\s+)?|"
    r"(?:please\s+)?go\s+ahead\s+and\s+|"
    r"(?:please|just|kindly)\s+"
    r")*"
)

# Note on verbs:
# 'show' and 'start' are deliberately excluded for now:
# - 'show notes' is a common phrase in conversation (e.g. podcast show notes).
# - 'start' could collide with dictation commands (e.g. 'start dictation') or note-taking ('start notes').
# We permit: open, open up, launch, bring up.
_VERB = r"(?:open(?:\s+up)?|launch|bring\s+up)"

# Determiner and target: accepts singular ('note', 'node') and plural ('notes', 'nodes').
# 'node' / 'nodes' are included to handle known Whisper acoustic mishearings.
# 'the' and 'my' are accepted determiners; 'a' and 'an' are excluded so 'open a note' is rejected.
_TARGET = r"(?:the\s+|my\s+)?(?:notes?|nodes?)(?:\s+(?:app|application))?"

# Trailing politeness wrapper
_POLITENESS_SUFFIX = r"(?:\s+(?:please|for\s+me|thanks|thank\s+you))?"

_OPEN_NOTES_PATTERN = re.compile(
    rf"^{_POLITENESS_PREFIX}{_VERB}\s+{_TARGET}{_POLITENESS_SUFFIX}$",
    re.IGNORECASE,
)

# Regex components for the "take note" command
# Verbs: start/begin taking, take, start/begin creating, create, start/begin making, make, (a) new
_TAKE_NOTE_VERB = (
    r"(?:"
    r"(?:(?:start|begin)\s+taking|take|"
    r"(?:start|begin)\s+creating|create|"
    r"(?:start|begin)\s+making|make)\s+(?:(?:a\s+)?new\s+|a\s+|some\s+)?|"
    r"(?:a\s+)?new\s+"
    r")"
)
_TAKE_NOTE_TARGET = r"(?:notes?|nodes?)"

_TAKE_NOTE_PATTERN = re.compile(
    rf"^{_POLITENESS_PREFIX}{_TAKE_NOTE_VERB}{_TAKE_NOTE_TARGET}{_POLITENESS_SUFFIX}$",
    re.IGNORECASE,
)

# Regex components for the "open WhatsApp" command
_OPEN_WA_VERB = r"(?:open(?:\s+up)?|launch|bring\s+up)"
_OPEN_WA_TARGET = r"(?:(?:the|my)\s+)?whatsapp(?:\s+(?:app|application))?"

_OPEN_WHATSAPP_PATTERN = re.compile(
    rf"^{_POLITENESS_PREFIX}{_OPEN_WA_VERB}\s+{_OPEN_WA_TARGET}{_POLITENESS_SUFFIX}$",
    re.IGNORECASE,
)

# Regex components for the "whatsapp search" command
_WA_SEARCH_PREFIX = (
    r"(?:"
    r"search\s+(?:whatsapp\s+)?for\s+|"
    r"search\s+whatsapp\s+for\s+|"
    r"search\s+whatsapp\s+|"
    r"search\s+for\s+|"
    r"search\s+|"
    r"find\s+"
    r")"
)
_WA_SEARCH_SUFFIX = r"(?:\s+(?:on|in)\s+whatsapp)?"

_WHATSAPP_SEARCH_PATTERN = re.compile(
    rf"^{_POLITENESS_PREFIX}{_WA_SEARCH_PREFIX}(?P<raw_name>.+?){_WA_SEARCH_SUFFIX}{_POLITENESS_SUFFIX}$",
    re.IGNORECASE,
)

RESERVED_START_WORDS = {
    "the", "a", "an", "my", "notes", "note", "whatsapp", "chat", "app", "for"
}
RESERVED_ALL_WORDS = {
    "the", "a", "an", "my", "notes", "note", "whatsapp", "chat", "app", "for"
}


def is_valid_name_char(c: str) -> bool:
    """Check if character is a letter in any Unicode script, apostrophe, or hyphen."""
    cat = unicodedata.category(c)
    return cat.startswith("L") or cat.startswith("M") or c in ("'", "’", "-")


def validate_extracted_name(raw_name: str) -> Optional[str]:
    """
    Validate and clean extracted name slot:
    - 1-4 words.
    - Letters in any script (including vowel marks), apostrophes and hyphens only.
    - Strips Whisper trailing punctuation.
    - Rejects digits, empty names.
    - Rejects names starting with reserved words (the, a, an, my, notes, note, whatsapp, chat, app, for).
    - Rejects names consisting only of reserved words or containing reserved preposition 'for'.
    - Allows single-letter middle initials like 'A' in 'John A Smith'.
    """
    if not raw_name:
        return None
    raw = raw_name.strip().rstrip(".,?!;:\"'—–-").strip(" \t\n\r\"'")
    if not raw:
        return None
    words = raw.split()
    if not (1 <= len(words) <= 4):
        return None
    if words[0].lower() in RESERVED_START_WORDS:
        return None
    if all(w.lower() in RESERVED_ALL_WORDS for w in words):
        return None
    if any(w.lower() == "for" for w in words):
        return None
    for w in words:
        w_core = w.strip("'’-_")
        if not w_core:
            return None
        if not all(is_valid_name_char(c) for c in w):
            return None
    return " ".join(words)


# Commands with nothing open-ended in them. Only these may run before the full command
# silence timeout (early finish), because there is nothing more the user could add.
# WhatsApp search is excluded: the name could still be continuing ("Rahul" ... "Sharma").
EARLY_FINISH_ACTIONS = frozenset({"open_notes", "take_note", "open_whatsapp"})


def allows_early_finish(match: Optional[CommandMatch]) -> bool:
    """True if this match is a complete fixed command that may run before the timeout."""
    return match is not None and match.action_id in EARLY_FINISH_ACTIONS and not match.extracted_name


def match_command(text: str) -> Optional[CommandMatch]:
    """
    Match a transcript against the rule layer.

    Args:
        text: Raw transcription text from Whisper.

    Returns:
        CommandMatch if recognized, or None if no command matched.
    """
    normalized = normalize_command_text(text)
    if not normalized:
        return None

    if _OPEN_NOTES_PATTERN.match(normalized):
        return CommandMatch(
            action_id="open_notes",
            normalized_text=normalized,
            matched_variant="open_notes",
        )

    if _TAKE_NOTE_PATTERN.match(normalized):
        return CommandMatch(
            action_id="take_note",
            normalized_text=normalized,
            matched_variant="take_note",
        )

    if _OPEN_WHATSAPP_PATTERN.match(normalized):
        return CommandMatch(
            action_id="open_whatsapp",
            normalized_text=normalized,
            matched_variant="open_whatsapp",
        )

    clean_text = text.strip().rstrip(".,?!;:\"'—–-").strip()
    search_m = _WHATSAPP_SEARCH_PATTERN.match(clean_text)
    if search_m:
        extracted = validate_extracted_name(search_m.group("raw_name"))
        if extracted:
            return CommandMatch(
                action_id="whatsapp_search",
                normalized_text=normalized,
                matched_variant="whatsapp_search",
                extracted_name=extracted,
            )

    return None
