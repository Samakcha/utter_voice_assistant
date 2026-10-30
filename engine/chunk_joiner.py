"""Intelligent joining of Progressive Whisper transcription chunks for Dictation Mode."""

import re
from typing import List

CONTINUATIONS = {
    "and",
    "but",
    "or",
    "nor",
    "so",
    "for",
    "yet",
    "because",
    "although",
    "since",
    "unless",
    "while",
    "where",
    "which",
    "who",
    "whom",
    "whose",
    "that",
    "then",
    "also",
    "too",
    "as",
    "if",
}


def _should_lowercase_first_word(word: str) -> bool:
    """Return True if word can be lowercased (not 'I', not acronyms)."""
    if not word:
        return False
    # Preserve pronoun 'I' and contractions ('I'm', 'I'll', etc.)
    if word == "I" or word.startswith(("I'", "I’")):
        return False
    # Preserve all-caps acronyms (e.g. 'NASA', 'API', 'JSON')
    if len(word) > 1 and word.isupper():
        return False
    # Preserve mixed-case words (e.g. 'macOS', 'iPhone', 'eBay')
    if any(c.isupper() for c in word[1:]):
        return False
    return True


def join_transcription_chunks(chunks: List[str]) -> str:
    """
    Intelligently join a sequence of chunk transcripts.
    
    Whisper often capitalizes and places periods at the end of isolated audio chunks.
    When a mid-sentence pause occurs across chunks (e.g. ["store.", "And then"]),
    this function converts the join into a seamless sentence:
    "store, and then".
    
    Rules:
    1. Empty chunks are skipped.
    2. If Chunk B starts with a lowercase word or continuation conjunction (and, but, so, etc.):
       - If Chunk A ended with a period, replace period with a comma (or space).
       - Lowercase Chunk B's initial word (unless proper noun 'I' or acronym).
    3. If Chunk A ended without terminal punctuation (mid-clause cutoff):
       - Lowercase Chunk B's initial word (unless proper noun 'I' or acronym).
    4. If Chunk A ended with a comma:
       - Lowercase Chunk B's initial word.
    5. If Chunk A ended with legitimate sentence punctuation (. ! ?) and Chunk B is a new sentence:
       - Preserve the sentence boundary as-is.
    """
    cleaned_chunks = [c.strip() for c in chunks if c and c.strip()]
    if not cleaned_chunks:
        return ""
    if len(cleaned_chunks) == 1:
        return cleaned_chunks[0]

    result = cleaned_chunks[0]

    for next_chunk in cleaned_chunks[1:]:
        if not next_chunk:
            continue

        words = next_chunk.split()
        if not words:
            continue

        first_word = words[0]
        first_word_clean = re.sub(r"[^\w']", "", first_word).lower()
        first_word_is_continuation = first_word_clean in CONTINUATIONS
        first_word_was_lowercase = words[0][0].islower()

        # Check ending of accumulated result
        ends_with_period = result.endswith(".") and not result.endswith("...")
        ends_with_comma = result.endswith(",")
        ends_with_terminal = result.endswith((".", "!", "?")) and not result.endswith("...")
        ends_with_no_punct = not re.search(r"[.,!?;:\-–—]$", result)

        should_join_as_clause = False

        if ends_with_period and (first_word_is_continuation or first_word_was_lowercase):
            # Mid-sentence pause where Whisper added a false period: "store. and then"
            result = result[:-1] + ","
            should_join_as_clause = True
        elif ends_with_no_punct:
            # Chunk cutoff in mid-clause: "We went to the" + "Store"
            should_join_as_clause = True
        elif ends_with_comma:
            # Chunk ended with comma: "Although it rained," + "We went out"
            should_join_as_clause = True

        if should_join_as_clause and _should_lowercase_first_word(first_word):
            # Lowercase first word of continuation
            words[0] = words[0][0].lower() + words[0][1:]
            next_chunk = " ".join(words)

        result = f"{result} {next_chunk}"

    return result
