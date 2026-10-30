"""Logic and formatting for dictation text insertion."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class FormattedInsertion:
    """Formatted dictation text ready for insertion."""
    text: str
    word_count: int
    should_insert: bool


@dataclass
class ClipboardSnapshot:
    """Snapshot of clipboard items and changeCount for safe save/restore."""
    change_count: int
    items: List[Dict[str, Any]] = field(default_factory=list)


def format_dictation_insertion(
    text: str,
    trailing_space: bool = False,
) -> FormattedInsertion:
    """
    Format dictation text for clipboard insertion.
    Trims outer whitespace, computes word count, and optionally appends a trailing space.
    """
    trimmed = text.strip()
    if not trimmed:
        return FormattedInsertion(text="", word_count=0, should_insert=False)

    word_count = len(trimmed.split())
    final_text = trimmed + (" " if trailing_space else "")
    return FormattedInsertion(
        text=final_text,
        word_count=word_count,
        should_insert=True,
    )
