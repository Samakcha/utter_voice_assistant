"""Tests and before/after demonstrations for joining progressive Whisper chunks."""

import pytest
from engine.chunk_joiner import join_transcription_chunks


# Pairs of (raw_chunks, expected_joined_text, description)
CHUNK_JOIN_CASES = [
    (
        ["We went to the grocery store.", "And then we bought some milk."],
        "We went to the grocery store, and then we bought some milk.",
        "Mid-sentence pause with conjunction ('store.' + 'And then')",
    ),
    (
        ["We went to the", "Grocery store and bought milk."],
        "We went to the grocery store and bought milk.",
        "Mid-clause cutoff without punctuation ('to the' + 'Grocery')",
    ),
    (
        ["Although it was raining,", "We decided to walk."],
        "Although it was raining, we decided to walk.",
        "Comma clause continuation ('raining,' + 'We decided')",
    ),
    (
        ["The draft is complete.", "Please take a look when you have time."],
        "The draft is complete. Please take a look when you have time.",
        "Independent sentences separated by period",
    ),
    (
        ["Are we ready to deploy?", "Yes, the tests passed."],
        "Are we ready to deploy? Yes, the tests passed.",
        "Question followed by independent sentence",
    ),
    (
        ["If you need any help,", "I will be in the office."],
        "If you need any help, I will be in the office.",
        "Pronoun 'I' preserved after comma continuation",
    ),
    (
        ["Please connect to the", "API endpoint now."],
        "Please connect to the API endpoint now.",
        "All-caps acronym 'API' preserved after mid-clause cutoff",
    ),
    (
        ["I think this works.", "Because we verified all edge cases."],
        "I think this works, because we verified all edge cases.",
        "Causal conjunction 'Because' turns preceding period to comma",
    ),
    (
        ["Wait a second...", "Now we can continue."],
        "Wait a second... Now we can continue.",
        "Ellipsis preserved between clauses",
    ),
    (
        ["First clause,", "Second clause,", "Third clause."],
        "First clause, second clause, third clause.",
        "Multi-chunk continuous sentence across three chunks",
    ),
]


@pytest.mark.parametrize("raw_chunks,expected_joined,description", CHUNK_JOIN_CASES)
def test_chunk_joiner_cases(raw_chunks, expected_joined, description):
    joined = join_transcription_chunks(raw_chunks)
    assert joined == expected_joined, (
        f"Failed for case: {description}\n"
        f"Input chunks: {raw_chunks}\n"
        f"Got:          {joined!r}\n"
        f"Expected:     {expected_joined!r}"
    )


def test_chunk_joiner_empty_and_single():
    assert join_transcription_chunks([]) == ""
    assert join_transcription_chunks([""]) == ""
    assert join_transcription_chunks(["   "]) == ""
    assert join_transcription_chunks(["Single chunk text."]) == "Single chunk text."
    assert join_transcription_chunks(["First chunk.", "", "   ", "Second chunk."]) == "First chunk. Second chunk."
