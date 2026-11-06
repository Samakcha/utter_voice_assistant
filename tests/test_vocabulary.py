"""Unit tests for the personal word list: loading, Whisper prompt, echo removal, correction."""

import os
from pathlib import Path

import pytest

from engine.vocabulary import (
    MAX_CONTEXT_CHARS,
    MAX_PROMPT_TERMS_CHARS,
    PROMPT_HEADER,
    VOCABULARY_FILE_TEMPLATE,
    VocabularyStore,
    build_whisper_prompt,
    correct_text,
    load_known_words,
    parse_vocabulary,
    strip_prompt_echo,
)

# Sample names and terms (not anyone's real list)
TERMS = [
    "Rahul", "Saanvi", "Ishaan", "Arjun", "Kavya", "Vikram", "Deepak", "Sneha", "Karan",
    "Curtis", "Antigravity", "PRD", "GitHub", "macOS", "Supabase", "Postgres", "Figma",
    "Wispr", "Priya Sharma", "Neha", "Will", "Notebook",
]

needs_dictionary = pytest.mark.skipif(load_known_words() is None, reason="system dictionary not available")


# --------------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------------

def test_parse_vocabulary_skips_comments_blanks_and_duplicates():
    text = "# comment\n\n  Rahul  \nrahul\nPriya    Sharma\n#Hidden\nGitHub\n" + "x" * 41 + "\n"
    assert parse_vocabulary(text) == ["Rahul", "Priya Sharma", "GitHub"]


def test_template_has_no_active_terms():
    assert parse_vocabulary(VOCABULARY_FILE_TEMPLATE) == []


def test_store_without_path_is_empty_and_cannot_create_file():
    store = VocabularyStore(None)
    assert store.terms() == []
    with pytest.raises(ValueError):
        store.ensure_file()


def test_store_missing_file_is_empty(tmp_path: Path):
    assert VocabularyStore(tmp_path / "nope.txt").terms() == []


def test_store_rereads_file_when_it_changes(tmp_path: Path):
    path = tmp_path / "vocabulary.txt"
    path.write_text("Rahul\n", encoding="utf-8")
    store = VocabularyStore(path)
    assert store.terms() == ["Rahul"]

    path.write_text("Rahul\nSaanvi\n", encoding="utf-8")
    st = path.stat()
    os.utime(path, (st.st_atime, st.st_mtime + 5))
    assert store.terms() == ["Rahul", "Saanvi"]

    path.unlink()
    assert store.terms() == []


def test_ensure_file_creates_template_once(tmp_path: Path):
    path = tmp_path / "sub" / "vocabulary.txt"
    store = VocabularyStore(path)
    assert store.ensure_file() == path
    assert path.read_text(encoding="utf-8") == VOCABULARY_FILE_TEMPLATE

    path.write_text("Rahul\n", encoding="utf-8")
    store.ensure_file()
    assert path.read_text(encoding="utf-8") == "Rahul\n"  # never overwrites the user's list


# --------------------------------------------------------------------------------------
# Whisper prompt
# --------------------------------------------------------------------------------------

def test_prompt_is_none_when_there_is_nothing_to_say():
    assert build_whisper_prompt([]) is None
    assert build_whisper_prompt([], context="  ", base_prompt="") is None


def test_prompt_order_is_base_then_words_then_context():
    prompt = build_whisper_prompt(["Rahul", "PRD"], context="We met yesterday.", base_prompt="Hello.")
    assert prompt == f"Hello. {PROMPT_HEADER} Rahul, PRD. We met yesterday."


def test_prompt_without_words_keeps_previous_behavior():
    assert build_whisper_prompt([], context="Previous chunk.") == "Previous chunk."


def test_prompt_term_count_and_length_are_capped():
    terms = [f"Name{i:03d}" for i in range(200)]
    prompt = build_whisper_prompt(terms, max_terms=50)
    listed = prompt[len(PROMPT_HEADER) + 1:-1].split(", ")
    assert len(listed) <= 50
    assert len(prompt) <= MAX_PROMPT_TERMS_CHARS + 2

    assert build_whisper_prompt(terms, max_terms=3) == f"{PROMPT_HEADER} Name000, Name001, Name002."


def test_long_context_is_trimmed_to_whole_words():
    context = " ".join(f"word{i}" for i in range(300))
    prompt = build_whisper_prompt([], context=context)
    assert len(prompt) <= MAX_CONTEXT_CHARS
    assert prompt.endswith("word299")
    assert prompt.split()[0].startswith("word")
    assert prompt.split()[0] in context.split()


# --------------------------------------------------------------------------------------
# Echo removal
# --------------------------------------------------------------------------------------

def test_strip_echo_removes_header_and_listed_terms():
    assert strip_prompt_echo("Vocabulary: Rahul, PRD, GitHub. Hello there.", TERMS) == "Hello there."
    assert strip_prompt_echo("vocabulary: rahul.", TERMS) == ""


def test_strip_echo_removes_bare_list_of_three_or_more_terms():
    assert strip_prompt_echo("Rahul, Saanvi, GitHub.", TERMS) == ""


def test_strip_echo_keeps_real_speech():
    assert strip_prompt_echo("Rahul, Saanvi.", TERMS) == "Rahul, Saanvi."
    assert strip_prompt_echo("The vocabulary: big words.", TERMS) == "The vocabulary: big words."
    assert strip_prompt_echo("Call Rahul about GitHub.", TERMS) == "Call Rahul about GitHub."
    assert strip_prompt_echo("Hello.", []) == "Hello."


# --------------------------------------------------------------------------------------
# Correction
# --------------------------------------------------------------------------------------

@needs_dictionary
@pytest.mark.parametrize(
    "heard, expected",
    [
        ("Message Rahool now.", "Message Rahul now."),
        ("Ask Saanvee and Arjoon.", "Ask Saanvi and Arjun."),
        ("Vikrum and Deepuk met Snaha.", "Vikram and Deepak met Sneha."),
        ("Rahool's laptop is here.", "Rahul's laptop is here."),
        ("Talk to Kavia today.", "Talk to Kavya today."),
        ("Ask Priya Sharmaa.", "Ask Priya Sharma."),
        ("Open the Figmaa file.", "Open the Figma file."),
    ],
)
def test_close_mishearings_are_fixed(heard, expected):
    assert correct_text(heard, TERMS).text == expected


@needs_dictionary
@pytest.mark.parametrize(
    "heard, expected",
    [
        ("Send the prd today.", "Send the PRD today."),
        ("Open anti gravity.", "Open Antigravity."),
        ("Check the anti-gravity build.", "Check the Antigravity build."),
        ("Push it to git hub.", "Push it to GitHub."),
        ("Update mac os tonight.", "Update macOS tonight."),
        ("Move to supa base and post gres.", "Move to Supabase and Postgres."),
        ("Call rahul.", "Call Rahul."),
    ],
)
def test_exact_spelling_case_and_spacing_are_fixed(heard, expected):
    assert correct_text(heard, TERMS).text == expected


@needs_dictionary
@pytest.mark.parametrize(
    "sentence",
    [
        "I will take the dog for a walk and then make some tea.",
        "Will you rave about it, or will you rule it out?",
        "Write it in a note book before the meeting.",
        "The rule is simple and the road is long.",
        "Please check your email and the website before lunch.",
        "Upload the certs to the server and restart it.",
        "I don't know if they're coming, but we'll see.",
        "My net worth is near zero and my neck hurts.",
        "Send 5 copies to room 12 by Friday.",
        "Okay, the app is online now, thanks.",
        "Karaoke night was fun, and the korn chips were great.",
        "Running late, stopped for coffee, then walked to work.",
        "Remember to buy milk, bread, eggs, and some apples.",
        "Visit https://github.com/someone/repo or mail rahul@example.com.",
        "The file is called num_certs.txt in the build folder.",
    ],
)
def test_ordinary_sentences_are_left_exactly_alone(sentence):
    result = correct_text(sentence, TERMS)
    assert result.text == sentence
    assert result.corrections == ()


@needs_dictionary
def test_already_correct_terms_are_unchanged():
    text = "Rahul, Saanvi and Priya Sharma use GitHub and macOS."
    assert correct_text(text, TERMS).text == text


@needs_dictionary
def test_short_terms_are_never_fuzzy_matched():
    # "Neha" is 4 letters: too short to fix by similarity, only by exact spelling
    assert correct_text("Ask Neta.", TERMS).text == "Ask Neta."
    assert correct_text("Ask neha.", TERMS).text == "Ask Neha."


@needs_dictionary
def test_corrections_are_reported():
    result = correct_text("Ask Rahool about the prd.", TERMS)
    assert [(c.original, c.replacement) for c in result.corrections] == [("Rahool", "Rahul"), ("prd", "PRD")]


def test_without_dictionary_only_exact_fixes_run():
    result = correct_text("Ask Rahool about the prd and anti gravity.", TERMS, use_system_dictionary=False)
    assert result.text == "Ask Rahool about the PRD and Antigravity."


def test_empty_inputs():
    assert correct_text("", TERMS).text == ""
    assert correct_text("Hello Rahool.", []).text == "Hello Rahool."
