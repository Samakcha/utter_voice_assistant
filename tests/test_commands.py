"""
Table-driven unit tests for the rule matching layer (engine/commands.py).
Evaluates positive patterns, edge cases, Whisper punctuation/casing variations,
and negative phrases that must strictly never match.
"""

import pytest
from engine.commands import CommandMatch, match_command, normalize_command_text


POSITIVE_PHRASES = [
    # Core command variations
    "open notes",
    "open the notes app",
    "open my notes app",
    "open notes application",
    "open the notes application",
    "open up notes",
    "open up the notes app",
    # Verbs: launch, bring up
    "launch notes",
    "launch the notes app",
    "launch my notes",
    "launch notes application",
    "bring up notes",
    "bring up the notes app",
    "bring up my notes",
    "bring up the notes application",
    # Modal & politeness prefixes
    "can you open notes",
    "can you please open notes",
    "could you open notes",
    "could you please launch the notes app",
    "would you open notes",
    "will you open notes",
    "please open notes",
    "please open the notes app",
    "just open notes",
    "kindly open notes",
    "hey open notes",
    "hi can you open notes",
    "hello please launch notes",
    "go ahead and open notes",
    "please go ahead and bring up notes",
    # Singular 'note' and 'node' variations
    "open note",
    "open the note app",
    "open node",
    "launch the note",
    "can you open the note app",
    "Open the Note App.",
    # Known acoustic mishearings ("nodes" for "notes")
    "open nodes",
    "can you open nodes",
    "launch nodes",
    "open the nodes app",
    "bring up nodes",
    # Trailing politeness suffixes
    "open notes please",
    "open notes for me",
    "open notes thanks",
    "open notes thank you",
    "can you open notes please",
    # Whisper-style casing, capitalization and trailing punctuation
    "Open Notes.",
    "Can you open notes?",
    "Open the nodes app.",
    "Could you please bring up notes?",
    "Open notes!",
    "Launch Notes...",
]

NEGATIVE_PHRASES = [
    # Explicitly requested negative phrases
    "open notes and send it to Rahul",
    "why did you open notes",
    "do you open notes",
    "notes open",
    "open notes folder",
    "open note folder",
    "open the note taking app",
    "I wrote a note yesterday",
    "take a note",
    "open a note",
    "delete note",
    "stop notes",
    "thank you",
    "okay",
    "",
    "   ",
    # PRD negative examples
    "I took some notes yesterday",
    "I opened notes yesterday",
    "notes are in the folder",
    "don't open notes",
    "do not open notes",
    "open the notes folder",
    # Excluded verbs ('show' and 'start') per User Change 4
    "show notes",
    "show the notes app",
    "start notes",
    "start taking notes",
    # Other actions, operations or conversations
    "take notes",
    "delete notes",
    "notes",
    "the notes",
    "notes app",
    "my notes",
    "what are my notes",
    "read my notes",
    "make a note",
    "can you take notes",
    "close notes",
    "quit notes",
    "open safari",
    "launch terminal",
    "hello how are you",
    "send message to Rahul on WhatsApp",
    "please don't open notes",
    "I really like taking notes in Apple Notes",
    "is notes open",
    "notes is open",
]


@pytest.mark.parametrize("phrase", POSITIVE_PHRASES)
def test_match_command_positive_cases(phrase: str):
    """Verify that every valid phrasing maps cleanly to the 'open_notes' action."""
    match = match_command(phrase)
    assert match is not None, f"Failed to match valid positive phrase: '{phrase}'"
    assert isinstance(match, CommandMatch)
    assert match.action_id == "open_notes"
    assert match.matched_variant == "open_notes"
    assert len(match.normalized_text) > 0


@pytest.mark.parametrize("phrase", NEGATIVE_PHRASES)
def test_match_command_negative_cases(phrase: str):
    """Verify that conversational text, questions, or non-matching verbs return None."""
    match = match_command(phrase)
    assert match is None, f"Incorrectly matched negative phrase: '{phrase}' (got {match})"


def test_normalize_command_text():
    assert normalize_command_text("  Open,  Notes!  ") == "open notes"
    assert normalize_command_text("Can you open notes???") == "can you open notes"
    assert normalize_command_text("") == ""
    assert normalize_command_text(None) == ""
