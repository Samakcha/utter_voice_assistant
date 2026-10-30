"""Stop phrase detection and cleanup for Dictation Mode."""

from dataclasses import dataclass
import logging
import re
from typing import List, Optional

logger = logging.getLogger(__name__)

COMMON_WORDS = {
    "okay",
    "ok",
    "yes",
    "yeah",
    "yep",
    "no",
    "nope",
    "done",
    "stop",
    "end",
    "note",
    "notes",
    "dictation",
    "thanks",
    "thank you",
    "hello",
    "hi",
    "bye",
    "please",
}


@dataclass(frozen=True)
class StopPhraseMatch:
    """Represents a matched stop phrase at the end of dictation."""
    matched_phrase: str
    cleaned_text: str
    raw_match: str


def levenshtein_distance(s1: str, s2: str) -> int:
    """Compute the Levenshtein edit distance between two strings."""
    if s1 == s2:
        return 0
    if not s1:
        return len(s2)
    if not s2:
        return len(s1)

    dp = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        new_dp = [i + 1] * (len(s2) + 1)
        for j, c2 in enumerate(s2):
            cost = 0 if c1 == c2 else 1
            new_dp[j + 1] = min(dp[j + 1] + 1, new_dp[j] + 1, dp[j] + cost)
        dp = new_dp
    return dp[-1]


def validate_stop_phrases(stop_phrases: List[str]) -> None:
    """
    Validate configured stop phrases at startup.
    Warns prominently in logs if a phrase is very short or is a common word.
    """
    for phrase in stop_phrases:
        cleaned = phrase.strip().lower()
        words = cleaned.split()
        if not cleaned:
            continue
        if cleaned in COMMON_WORDS:
            logger.warning(
                "Configured stop phrase '%s' is a common conversational word; "
                "risk of accidental dictation termination.",
                phrase,
            )
        elif len(cleaned) < 5 or len(words) < 2:
            logger.warning(
                "Configured stop phrase '%s' is very short (< 5 chars or < 2 words); "
                "recommend using a distinct multi-word phrase such as 'stop dictation'.",
                phrase,
            )


def detect_stop_phrase(
    text: str,
    stop_phrases: List[str],
    fuzzy_tolerance: float = 0.2,
) -> Optional[StopPhraseMatch]:
    """
    Check if text ends with any of the configured stop phrases within fuzzy tolerance.
    
    A stop phrase counts ONLY when it appears at the end of the text.
    Mid-sentence phrases are never matched.
    
    Returns:
        StopPhraseMatch with cleaned text (phrase and dangling connectors removed),
        or None if no stop phrase matched.
    """
    if not text or not text.strip():
        return None

    # Tokenize words while preserving character spans: list of (word_lower, start_idx, end_idx)
    tokens = []
    for match in re.finditer(r"\b[\w']+\b", text):
        tokens.append((match.group().lower(), match.start(), match.end()))

    if not tokens:
        return None

    # Determine any trailing sentence punctuation at the very end of the utterance
    trailing_punct_match = re.search(r"([.!?]+)\s*$", text)
    terminal_punct = trailing_punct_match.group(1)[0] if trailing_punct_match else "."

    for target_phrase in stop_phrases:
        target_norm = " ".join(target_phrase.strip().lower().split())
        target_words = target_norm.split()
        k = len(target_words)
        if k == 0 or len(tokens) < k:
            continue

        # Inspect candidate tokens at the end of the transcript
        candidate_tokens = tokens[-k:]
        candidate_words = [t[0] for t in candidate_tokens]
        candidate_str = " ".join(candidate_words)

        # Word-level integrity check: short words (<= 3 chars, e.g. 'end') must match exactly
        # so that "I will send dictation" does not match "end dictation" via 1-char insertion.
        word_mismatch = False
        for cand_w, target_w in zip(candidate_words, target_words):
            if len(target_w) <= 3 and cand_w != target_w:
                word_mismatch = True
                break
        if word_mismatch:
            continue

        # Compute Levenshtein distance on full phrase
        dist = levenshtein_distance(candidate_str, target_norm)
        target_len = len(target_norm)
        ratio = dist / float(target_len) if target_len > 0 else 1.0

        if ratio <= fuzzy_tolerance:
            # Matched at the end!
            start_char = candidate_tokens[0][1]
            raw_match = text[start_char:].strip()

            # Clean prefix text before the match
            prefix = text[:start_char].rstrip()

            # 1. Remove dangling conjunctions/connectors (e.g. "and", "so", "then", "or")
            prefix = re.sub(r"(?i)\b(?:and|then|so|or|also)\s*$", "", prefix).rstrip()

            # 2. Remove dangling punctuation before the stop phrase (e.g. comma, semicolon, dash)
            had_dangling_comma = bool(re.search(r"[,;:\-–—]+\s*$", prefix))
            prefix = re.sub(r"[,;:\-–—]+\s*$", "", prefix).rstrip()

            # 3. Clean sentence ending
            if prefix and not prefix.endswith((".", "!", "?")):
                prefix = prefix + terminal_punct

            return StopPhraseMatch(
                matched_phrase=target_phrase,
                cleaned_text=prefix,
                raw_match=raw_match,
            )

    return None
