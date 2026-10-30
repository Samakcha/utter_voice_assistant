"""Unit tests for stop phrase detection, fuzzy tolerance, and punctuation cleanup."""

import logging
import pytest
from engine.stop_phrases import (
    COMMON_WORDS,
    detect_stop_phrase,
    levenshtein_distance,
    validate_stop_phrases,
)

DEFAULT_STOP_PHRASES = ["stop dictation", "end dictation"]


def test_levenshtein_distance():
    assert levenshtein_distance("", "") == 0
    assert levenshtein_distance("stop", "stop") == 0
    assert levenshtein_distance("end", "send") == 1
    assert levenshtein_distance("dictation", "dictating") == 2
    assert levenshtein_distance("kitten", "sitting") == 3


def test_validate_stop_phrases(caplog):
    with caplog.at_level(logging.WARNING):
        validate_stop_phrases(["okay", "stop dictation", "hi", "end"])
    # Warnings expected for 'okay', 'hi', 'end'
    assert any("okay" in r.message for r in caplog.records)
    assert any("hi" in r.message for r in caplog.records)
    assert any("end" in r.message for r in caplog.records)
    # No warning for 'stop dictation'
    assert not any("stop dictation" in r.message for r in caplog.records)


# 25+ POSITIVE PHRASES
# Expected behavior: matches a stop phrase and cleanly strips it and dangling connectors.
POSITIVE_CASES = [
    # Exact phrase at end
    ("Please send the report stop dictation", "Please send the report."),
    ("We are meeting at noon end dictation", "We are meeting at noon."),
    # Trailing Whisper punctuation & capitalization
    ("Please send the report. Stop dictation.", "Please send the report."),
    ("Call me back tomorrow. End dictation.", "Call me back tomorrow."),
    ("Can you finish the draft? Stop dictation?", "Can you finish the draft?"),
    ("Great job! End dictation!", "Great job!"),
    # Dangling commas / dashes before stop phrase
    ("I will arrive tomorrow, stop dictation.", "I will arrive tomorrow."),
    ("See you at five, end dictation.", "See you at five."),
    ("The package arrived - stop dictation.", "The package arrived."),
    ("Heading to the store; end dictation.", "Heading to the store."),
    # Dangling conjunctions / connectors
    ("Call me tomorrow, and stop dictation.", "Call me tomorrow."),
    ("We finished the slides and end dictation.", "We finished the slides."),
    ("Let's review this tomorrow so stop dictation.", "Let's review this tomorrow."),
    ("Send the invoice then end dictation.", "Send the invoice."),
    ("Take the train or stop dictation.", "Take the train."),
    # Small fuzzy variants within 0.2 tolerance
    # "stop dictation" has 14 chars. 14 * 0.2 = 2.8 -> max distance 2.
    ("Here is my note stop dictations", "Here is my note."),  # +1 char 's'
    ("Review the document step dictation", "Review the document."),  # 'e' for 'o' (dist 1)
    ("Send the report end dictations", "Send the report."),  # +1 char 's'
    # Solo stop phrases (utterance is ONLY the stop phrase)
    ("stop dictation", ""),
    ("Stop dictation.", ""),
    ("end dictation", ""),
    ("End dictation!", ""),
    # Multiple sentences preceding stop phrase
    ("Hello world. The weather is great today. Stop dictation.", "Hello world. The weather is great today."),
    ("First item is done. Second item is in progress, end dictation.", "First item is done. Second item is in progress."),
    ("Are you ready? Let's begin. Stop dictation.", "Are you ready? Let's begin."),
    ("This looks excellent! Thank you so much. End dictation.", "This looks excellent! Thank you so much."),
]


@pytest.mark.parametrize("input_text,expected_cleaned", POSITIVE_CASES)
def test_positive_stop_phrases(input_text, expected_cleaned):
    match = detect_stop_phrase(
        text=input_text,
        stop_phrases=DEFAULT_STOP_PHRASES,
        fuzzy_tolerance=0.2,
    )
    assert match is not None, f"Expected match for positive phrase: {input_text!r}"
    assert match.cleaned_text == expected_cleaned, (
        f"Input: {input_text!r}\n"
        f"Got cleaned: {match.cleaned_text!r}\n"
        f"Expected:    {expected_cleaned!r}"
    )


# 25+ NEGATIVE PHRASES
# Expected behavior: must NOT match stop phrase
NEGATIVE_CASES = [
    # Explicitly required negatives by user
    "send note",
    "the end note",
    "I will send dictation",
    "tell him to stop dictation is ready",
    "stop",
    "dictation",
    # Mid-sentence occurrences (must never match)
    "Tell him stop dictation is a great feature",
    "Stop dictation should not be triggered mid sentence",
    "I told him to end dictation before leaving",
    "Explain why stop dictation works this way",
    "The phrase end dictation was mentioned earlier in our talk",
    # Normal conversation / dictation sentences
    "We need to stop the train now",
    "Please do not stop working",
    "Let's write a note about the meeting",
    "I took dictation for two hours today",
    "Can you send the dictation file over email?",
    "We reached the end of the road",
    "The end of the chapter was thrilling",
    "I will dictation tomorrow",
    "Stop and listen to the music",
    "Dictation mode is enabled",
    # Words separated by other words
    "stop the dictation",
    "stop this dictation",
    "end of dictation",
    "please stop and do dictation",
    # Empty and whitespace
    "",
    "   ",
    "...",
    "???",
    # Completely different sentences
    "Hello how are you today?",
    "Open the notes app please",
    "Buy some milk and bread on the way home",
]


@pytest.mark.parametrize("input_text", NEGATIVE_CASES)
def test_negative_stop_phrases(input_text):
    match = detect_stop_phrase(
        text=input_text,
        stop_phrases=DEFAULT_STOP_PHRASES,
        fuzzy_tolerance=0.2,
    )
    assert match is None, (
        f"False positive! Phrase '{input_text}' matched stop phrase '{match.matched_phrase}' "
        f"with raw match '{match.raw_match}' and cleaned text '{match.cleaned_text}'"
    )
