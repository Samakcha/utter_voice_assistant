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
    # Other actions, operations or conversations
    "delete notes",
    "notes",
    "the notes",
    "notes app",
    "my notes",
    "what are my notes",
    "read my notes",
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
    # take_note negative phrases
    "I took notes yesterday",
    "take notes from the meeting",
    "take a note of that",
    "don't take notes",
    "do not take notes",
    "start taking notes on the lecture",
    "create a note about Rahul",
    "open notes and take a note",
    "take a note of what he said",
    "can you take notes from this call",
    "take notes for the class tomorrow",
    "I want to take a note",
    "he will take notes",
    "taking notes is fun",
    "note taking",
    "take note of this",
    "create note for mom",
    "new notes folder",
    "make a note about dinner",
    "start taking notes about biology",
    "begin taking notes on project",
    "please don't take notes",
    "why do you take notes",
    "take a note app",
]

TAKE_NOTE_POSITIVE_PHRASES = [
    "start taking notes",
    "start taking a note",
    "take notes",
    "take a note",
    "take a new note",
    "new note",
    "create a note",
    "create a new note",
    "make a note",
    "begin taking notes",
    "begin taking a note",
    "start creating a note",
    "start creating notes",
    "begin creating a note",
    "start making a note",
    "start making notes",
    "begin making a note",
    "make notes",
    "create notes",
    "take some notes",
    # Politeness wrappers
    "can you take a note",
    "can you please take a note",
    "could you take notes",
    "could you please take a note",
    "please take a note",
    "please take notes",
    "hey take a note",
    "take a note for me",
    "take notes for me",
    "take a note please",
    "can you take notes",
    # Whisper mishearings
    "take a node",
    "take nodes",
    "start taking nodes",
    "create a node",
    "new node",
    # Capitalization & punctuation
    "Start taking notes.",
    "Take a note.",
    "Can you take a note?",
    "Please take a note!",
]


@pytest.mark.parametrize("phrase", POSITIVE_PHRASES)
def test_match_command_open_notes_positive(phrase: str):
    """Verify that every valid phrasing maps cleanly to the 'open_notes' action."""
    match = match_command(phrase)
    assert match is not None, f"Failed to match valid positive phrase: '{phrase}'"
    assert isinstance(match, CommandMatch)
    assert match.action_id == "open_notes"
    assert match.matched_variant == "open_notes"
    assert len(match.normalized_text) > 0


@pytest.mark.parametrize("phrase", TAKE_NOTE_POSITIVE_PHRASES)
def test_match_command_take_note_positive(phrase: str):
    """Verify that every valid note-taking phrasing maps cleanly to 'take_note'."""
    match = match_command(phrase)
    assert match is not None, f"Failed to match valid take_note phrase: '{phrase}'"
    assert isinstance(match, CommandMatch)
    assert match.action_id == "take_note"
    assert match.matched_variant == "take_note"
    assert len(match.normalized_text) > 0


@pytest.mark.parametrize("phrase", NEGATIVE_PHRASES)
def test_match_command_negative_cases(phrase: str):
    """Verify that conversational text, questions, or non-matching verbs return None."""
    match = match_command(phrase)
    assert match is None, f"Incorrectly matched negative phrase: '{phrase}' (got {match})"


def test_eval_rules_csv_disjoint_and_accuracy():
    """Empirical overlap and accuracy test across the entire eval_rules.csv."""
    import csv
    from pathlib import Path

    csv_path = Path(__file__).resolve().parent / "data" / "eval_rules.csv"
    assert csv_path.is_file(), f"eval_rules.csv missing at {csv_path}"

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        assert header is not None

        for phrase, expected in reader:
            phrase = phrase.strip()
            expected = expected.strip().lower()
            if not phrase:
                continue

            match = match_command(phrase)
            predicted = match.action_id if match else "none"

            assert predicted == expected, (
                f"Mismatch for '{phrase}': expected '{expected}', got '{predicted}'"
            )


def test_normalize_command_text():
    assert normalize_command_text("  Open,  Notes!  ") == "open notes"
    assert normalize_command_text("Can you open notes???") == "can you open notes"
    assert normalize_command_text("") == ""
    assert normalize_command_text(None) == ""
