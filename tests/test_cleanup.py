"""Tests for dictation cleanup (engine/cleanup.py): fillers, repeats, self-corrections."""

import pytest

from engine.cleanup import clean_dictation


def clean(text, **kwargs):
    return clean_dictation(text, **kwargs).text


# ---- Fillers ----------------------------------------------------------------------------

@pytest.mark.parametrize("heard, expected", [
    ("Um, I think we should leave at 5.", "I think we should leave at 5."),
    ("So um the meeting is moved to Friday.", "So the meeting is moved to Friday."),
    ("Err the price is $20.", "The price is $20."),
    ("We need, uh, three chairs.", "We need three chairs."),
    ("I went, uhh, to the shop.", "I went to the shop."),
    ("I think um.", "I think."),
    ("Wait. Um. Okay.", "Wait. Okay."),
    ("Hmm, let me think about it.", "Let me think about it."),
    ("Ah, I see what you mean.", "I see what you mean."),
    ("Mm, sounds good.", "Sounds good."),
    ("It is, erm, complicated.", "It is complicated."),
    ("Uh, iPhone is great.", "iPhone is great."),  # never "IPhone"
    ("I need to buy, um, eggs. Uh, and milk.", "I need to buy eggs. And milk."),
])
def test_fillers_are_removed(heard, expected):
    assert clean(heard) == expected


@pytest.mark.parametrize("heard", [
    "I like it, you know.",  # "like" and "you know" are real words
    "Hum a tune for me.",
    "The error code is ERR 404.",
    "Send it to Umar.",
    "He said uh-oh.",
    "Ahmed will join us.",
    "Errands first, then lunch.",
])
def test_real_words_are_never_treated_as_fillers(heard):
    assert clean(heard) == heard


def test_only_fillers_leaves_nothing():
    assert clean("Um.") == ""
    assert clean("Uh, um, hmm.") == ""


# ---- Repeats ----------------------------------------------------------------------------

@pytest.mark.parametrize("heard, expected", [
    ("The the report is ready.", "The report is ready."),
    ("The The Report is ready.", "The Report is ready."),
    ("I I will call you later.", "I will call you later."),
    ("Can you can you send me the file?", "Can you send me the file?"),
    ("Can you, can you send me the file?", "Can you send me the file?"),
    ("I, I will call you after lunch.", "I will call you after lunch."),
    ("We went to the the store and and bought milk.", "We went to the store and bought milk."),
    ("It is is it is fine.", "It is is it is fine."),  # "is is" can be grammar: left alone
    ("I think I think we are done.", "I think we are done."),
    ("the the the end", "the end"),
])
def test_accidental_repeats_are_removed(heard, expected):
    assert clean(heard) == expected


@pytest.mark.parametrize("heard", [
    "It was very very good.",
    "Very, very good.",
    "No, no, no.",
    "No no no.",
    "Bye bye.",
    "I know that that is true.",
    "She had had enough.",
    "Go. Go.",
    "Really really nice.",
    "The code is 5 5 7 2.",
    "Room 22 22.",
    "Ha ha, that is funny.",
    "Hello hello, can you hear me?",
    "What it is is a problem.",
])
def test_deliberate_doubles_are_kept(heard):
    assert clean(heard) == heard


# ---- Self-corrections -------------------------------------------------------------------

@pytest.mark.parametrize("heard, expected", [
    ("Send it on Monday. No wait. Tuesday.", "Send it on Tuesday."),
    ("Send it on Monday, no wait, Tuesday.", "Send it on Tuesday."),
    ("Let's meet at 6. I mean 7.", "Let's meet at 7."),
    ("Let's meet at six, I mean seven.", "Let's meet at seven."),
    ("Meet at 6 tonight, I mean 7.", "Meet at 7 tonight."),
    ("We need three, no, four chairs.", "We need four chairs."),
    ("Call me today, no wait, tomorrow.", "Call me tomorrow."),
    ("It costs $20, sorry I mean $25.", "It costs $25."),
    ("The trip is in March, wait no, April.", "The trip is in April."),
    ("I'll bring the red one, I mean the blue one.", "I'll bring the blue one."),
    ("Let's go to the park, I mean to the beach.", "Let's go to the beach."),
    ("Send it at 5pm, no wait, 6pm.", "Send it at 6pm."),
])
def test_clear_self_corrections_are_applied(heard, expected):
    assert clean(heard) == expected


@pytest.mark.parametrize("heard", [
    "I mean it, this is important.",
    "It's okay. I mean, it's not bad.",
    "Do you know what I mean?",
    "There was no wait at the desk.",
    "I'm sorry, Tuesday doesn't work.",
    "I have 2 kids. I mean 2 is enough.",
    "I love the beach. I mean the beach is my favourite place to be.",
    "To him I mean the world.",
    "No, Tuesday is better.",
    "Actually, Monday works.",
    "There are no wait times on Monday.",
    "We need three. No four chairs fit.",  # "No" after a full stop is not "three, no, four"
    "I mean, the plan is fine.",
])
def test_things_that_only_look_like_corrections_are_kept(heard):
    assert clean(heard) == heard


# ---- Scratch that -----------------------------------------------------------------------

@pytest.mark.parametrize("heard, expected", [
    ("Call him tomorrow. Scratch that. Email him tomorrow.", "Email him tomorrow."),
    ("I'll be late. Call him tomorrow, scratch that. Email him.", "I'll be late. Email him."),
    ("Buy milk. Strike that. Buy eggs.", "Buy eggs."),
    ("Call him tomorrow. Scratch that.", ""),
])
def test_scratch_that_removes_the_sentence_before_it(heard, expected):
    assert clean(heard) == expected


@pytest.mark.parametrize("heard", [
    "I need to scratch that itch.",
    "Scratch that off the list.",
    "Scratch that.",  # nothing to take back
    "Don't scratch, that will hurt.",
])
def test_scratch_that_in_a_normal_sentence_is_kept(heard):
    assert clean(heard) == heard


# ---- Switches and results ---------------------------------------------------------------

def test_each_rule_can_be_switched_off():
    text = "Um, the the meeting is on Monday, no wait, Tuesday."
    assert clean(text) == "The meeting is on Tuesday."
    assert clean(text, fillers=False) == "Um, the meeting is on Tuesday."
    assert clean(text, repeats=False) == "The the meeting is on Tuesday."
    assert clean(text, corrections=False) == "The meeting is on Monday, no wait, Tuesday."


def test_changes_are_reported_and_unchanged_text_is_returned_as_is():
    result = clean_dictation("Um, the the meeting is on Monday, no wait, Tuesday.")
    assert [c.kind for c in result.changes] == ["filler", "correction", "repeat"]
    assert result.changes[1].removed == "Monday" and result.changes[1].replacement == "Tuesday"

    same = "  Exactly   as spoken,  with odd spacing. "
    result = clean_dictation(same)
    assert result.text == same and not result.changed


@pytest.mark.parametrize("text", ["", "   ", None])
def test_empty_text(text):
    assert clean_dictation(text).text == (text or "")


@pytest.mark.parametrize("heard", [
    "We shipped v2.1 to 3,000 users at 9:30 a.m. on Jan 5th.",
    "Email me at hello@example.com, thanks!",
    "The PRD for macOS is ready - check GitHub.",
    "Rahul's notes: eggs, milk, bread.",
    "50% of the team said \"yes\".",
    "It's 6 o'clock... time to go!",
])
def test_ordinary_dictation_is_untouched(heard):
    assert clean(heard) == heard
