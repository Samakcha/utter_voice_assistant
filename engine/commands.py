"""
Rule layer for voice commands.
Performs normalized whole-utterance pattern matching without loose substring search.
Pure engine component with NO UI code.
"""

from dataclasses import dataclass
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommandMatch:
    """Result of a successful rule layer command match."""
    action_id: str
    normalized_text: str
    matched_variant: str


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

# Determiner and target (including known Whisper acoustic mishearing 'nodes' for 'notes')
_TARGET = r"(?:the\s+|my\s+)?(?:notes|nodes)(?:\s+(?:app|application))?"

# Trailing politeness wrapper
_POLITENESS_SUFFIX = r"(?:\s+(?:please|for\s+me|thanks|thank\s+you))?"

_OPEN_NOTES_PATTERN = re.compile(
    rf"^{_POLITENESS_PREFIX}{_VERB}\s+{_TARGET}{_POLITENESS_SUFFIX}$",
    re.IGNORECASE,
)


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

    return None
