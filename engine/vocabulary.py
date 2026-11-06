"""Personal word list: names and terms the user wants spelled right.

Two things use the list, in dictation and notes only (never commands):
1. A short hint passed to Whisper as its prompt, so it expects those words.
2. A strict post-correction step that fixes close mishearings ("Rahool" -> "Rahul").

The correction is deliberately conservative. A word is only replaced when it is very
close to a listed term, starts with the same letter, and is not an ordinary English
word, so normal sentences are left exactly as Whisper wrote them.
"""

from dataclasses import dataclass
from functools import lru_cache
import logging
from pathlib import Path
import re
import threading
from typing import FrozenSet, List, Optional, Sequence, Tuple

from .stop_phrases import levenshtein_distance

logger = logging.getLogger(__name__)

VOCABULARY_FILE_TEMPLATE = """\
# Personal word list for Voice Assistant
#
# Put one name or term per line, spelled exactly how you want it written.
# The app hints these to Whisper and fixes close mishearings in dictation and notes.
# Lines starting with # are ignored. Changes apply to your next dictation; no restart needed.
#
# Examples (remove the # in front to use them):
# Rahul
# Antigravity
# PRD
"""

# Prompt budget. mlx-whisper keeps only the last 223 prompt tokens, so the word list
# (which comes first) must stay short enough to survive next to the previous-chunk context.
PROMPT_HEADER = "Vocabulary:"
MAX_PROMPT_TERMS = 50
MAX_PROMPT_TERMS_CHARS = 400   # ~100 tokens
MAX_CONTEXT_CHARS = 300        # ~70 tokens
MAX_TERM_CHARS = 40

# Fuzzy matching limits
MIN_FUZZY_TERM_LEN = 5         # shorter terms ("PRD", "Neha") are only fixed on an exact letter match
MIN_FUZZY_CANDIDATE_LEN = 4

SYSTEM_DICTIONARY = Path("/usr/share/dict/words")

# Everyday words the 1934 system dictionary doesn't know. They must never be "corrected".
EXTRA_COMMON_WORDS = frozenset("""
email emails emailed app apps okay ok online offline website websites webpage internet
laptop laptops smartphone smartphones login logout signup username password passwords
blog blogs podcast podcasts download downloads downloaded upload uploads uploaded
wifi bluetooth google googled youtube whatsapp instagram facebook twitter linkedin zoom
slack chatbot chatgpt emoji emojis selfie selfies inbox spam texting texted dm dms
gonna wanna gotta yeah yep nope hey hi bye thanks cool awesome alright
meetup startup startups smartwatch screenshot screenshots backend frontend
tv pc mac iphone ipad android windows macbook airpods netflix spotify amazon
tech teammate teammates feedback workflow workflows dashboard dashboards
""".split())

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z]+)*")
_POSSESSIVE_RE = re.compile(r"^(.*?)(['’]s)$", re.IGNORECASE)
_GAP_RE = re.compile(r"^[ \t-]*$")
_KEY_RE = re.compile(r"[^a-z0-9]")


# --------------------------------------------------------------------------------------
# Loading the list
# --------------------------------------------------------------------------------------

def parse_vocabulary(text: str) -> List[str]:
    """Parse word-list text: one term per line, # comments, de-duplicated (case-insensitive)."""
    terms: List[str] = []
    seen = set()
    for line in text.splitlines():
        term = " ".join(line.strip().split())
        if not term or term.startswith("#"):
            continue
        if len(term) > MAX_TERM_CHARS:
            logger.warning("Skipping word-list entry longer than %d characters.", MAX_TERM_CHARS)
            continue
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        terms.append(term)
    return terms


class VocabularyStore:
    """Reads the word-list file and re-reads it whenever it changes on disk."""

    def __init__(self, path: Optional[Path]):
        self.path = path
        self._lock = threading.Lock()
        self._terms: List[str] = []
        self._mtime: Optional[float] = None

    def terms(self) -> List[str]:
        """Current terms. Cheap to call per session: only re-reads the file when it changed."""
        if self.path is None:
            return []
        with self._lock:
            try:
                mtime = self.path.stat().st_mtime
            except FileNotFoundError:
                self._terms, self._mtime = [], None
                return []
            except OSError as e:
                logger.warning("Could not read the word list: %s", e)
                return list(self._terms)
            if mtime != self._mtime:
                try:
                    self._terms = parse_vocabulary(self.path.read_text(encoding="utf-8"))
                    self._mtime = mtime
                    logger.info("Loaded personal word list: %d terms.", len(self._terms))
                except (OSError, UnicodeDecodeError) as e:
                    logger.warning("Could not read the word list: %s", e)
            return list(self._terms)

    def ensure_file(self) -> Path:
        """Create the word-list file with instructions if it doesn't exist yet."""
        if self.path is None:
            raise ValueError("The personal word list is turned off in config.toml.")
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(VOCABULARY_FILE_TEMPLATE, encoding="utf-8")
        return self.path


# --------------------------------------------------------------------------------------
# Whisper prompt
# --------------------------------------------------------------------------------------

def _trim_context(context: str) -> str:
    context = " ".join(context.split())
    if len(context) <= MAX_CONTEXT_CHARS:
        return context
    tail = context[-MAX_CONTEXT_CHARS:]
    # Drop the partial first word
    space = tail.find(" ")
    return tail[space + 1:] if space != -1 else tail


def build_whisper_prompt(
    terms: Sequence[str],
    context: Optional[str] = None,
    base_prompt: str = "",
    max_terms: int = MAX_PROMPT_TERMS,
) -> Optional[str]:
    """
    Build Whisper's initial prompt: [config prompt] [Vocabulary: a, b, c.] [previous chunk].
    Returns None when there is nothing to say, so Whisper runs unbiased.
    """
    parts: List[str] = []
    if base_prompt and base_prompt.strip():
        parts.append(base_prompt.strip())

    chosen: List[str] = []
    used = len(PROMPT_HEADER)
    for term in list(terms)[:max(0, max_terms)]:
        cost = len(term) + 2
        if used + cost > MAX_PROMPT_TERMS_CHARS:
            break
        chosen.append(term)
        used += cost
    if chosen:
        parts.append(f"{PROMPT_HEADER} {', '.join(chosen)}.")

    if context and context.strip():
        parts.append(_trim_context(context))

    return " ".join(parts) if parts else None


def strip_prompt_echo(text: str, terms: Sequence[str]) -> str:
    """
    Remove the word-list hint if Whisper repeats it back (it can on near-silent audio).
    Only removes text that is clearly the hint: the "Vocabulary:" header followed by listed
    terms, or a whole transcript that is nothing but three or more listed terms.
    """
    if not text or not terms:
        return text
    term_keys = {t.lower() for t in terms}

    def _all_terms(items: List[str]) -> bool:
        return bool(items) and all(i.lower() in term_keys for i in items)

    def _replace_header(m: "re.Match[str]") -> str:
        items = [i.strip() for i in m.group(1).split(",") if i.strip()]
        return "" if _all_terms(items) else m.group(0)

    cleaned = re.sub(r"(?i)\bvocabulary:\s*([^.!?\n]*)[.!?]?", _replace_header, text)

    body = cleaned.strip().rstrip(".!?").strip()
    items = [i.strip() for i in body.split(",") if i.strip()]
    if len(items) >= 3 and _all_terms(items):
        cleaned = ""

    return " ".join(cleaned.split())


# --------------------------------------------------------------------------------------
# Post-correction
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Correction:
    original: str
    replacement: str


@dataclass(frozen=True)
class CorrectionResult:
    text: str
    corrections: Tuple[Correction, ...] = ()


@lru_cache(maxsize=1)
def load_known_words() -> Optional[FrozenSet[str]]:
    """
    Ordinary English words that must never be rewritten. Uses the lowercase entries of the
    macOS system dictionary (capitalized entries are proper names, which we do want to fix).
    Returns None if the dictionary is missing; fuzzy correction is then turned off.
    """
    try:
        with open(SYSTEM_DICTIONARY, encoding="utf-8", errors="ignore") as f:
            words = {w.strip() for w in f if w.strip() and w.strip().islower()}
    except OSError:
        logger.info("System dictionary not found; word-list fixes will use exact matches only.")
        return None
    return frozenset(words | EXTRA_COMMON_WORDS)


def _is_known(word: str, known: FrozenSet[str]) -> bool:
    """True for ordinary words, including simple inflections the dictionary lacks."""
    w = word.lower()
    if w in known or w in EXTRA_COMMON_WORDS:
        return True
    stems = []
    if w.endswith("s"):
        stems.append(w[:-1])
    if w.endswith("es"):
        stems.append(w[:-2])
    if w.endswith("ies"):
        stems.append(w[:-3] + "y")
    if w.endswith("ed"):
        stems += [w[:-2], w[:-1]]
    if w.endswith("ing"):
        stems += [w[:-3], w[:-3] + "e"]
    if w.endswith(("ly", "er")):
        stems.append(w[:-2])
    # Doubled consonant: running -> run, stopped -> stop
    for stem in list(stems):
        if len(stem) >= 3 and stem[-1] == stem[-2]:
            stems.append(stem[:-1])
    return any(len(s) >= 2 and s in known for s in stems)


def _key(text: str) -> str:
    return _KEY_RE.sub("", text.lower())


@dataclass(frozen=True)
class _Term:
    text: str
    key: str
    n_words: int


@dataclass(frozen=True)
class _Token:
    base: str       # word without a possessive 's
    suffix: str     # "'s" or ""
    start: int
    end: int


def _tokenize(text: str) -> List[_Token]:
    tokens = []
    for m in _TOKEN_RE.finditer(text):
        word = m.group()
        pm = _POSSESSIVE_RE.match(word)
        if pm and pm.group(1):
            tokens.append(_Token(pm.group(1), pm.group(2), m.start(), m.end()))
        else:
            tokens.append(_Token(word, "", m.start(), m.end()))
    return tokens


def _fuzzy_limit(term_key: str) -> int:
    return 1 if len(term_key) <= 7 else 2


def _skeleton(key: str) -> str:
    """Consonants only, doubles collapsed: "rahool" and "rahul" both give "rhl"."""
    out = []
    for ch in key:
        if ch in "aeiouy":
            continue
        if out and out[-1] == ch:
            continue
        out.append(ch)
    return "".join(out)


def _fuzzy_match(cand_key: str, term_key: str) -> Optional[int]:
    """
    Edit distance if close enough, else None. A vowel-only slip ("Rahool", "Saanvee") gets
    one extra edit, but only when the first three letters were heard right and the lengths
    are within one, so "certs" never becomes "Curtis".
    """
    dist = levenshtein_distance(cand_key, term_key)
    limit = _fuzzy_limit(term_key)
    if dist <= limit:
        return dist
    if (
        dist <= limit + 1
        and len(cand_key) >= 5
        and abs(len(cand_key) - len(term_key)) <= 1
        and cand_key[:3] == term_key[:3]
        and _skeleton(cand_key) == _skeleton(term_key)
    ):
        return dist
    return None


_EMBED_CHARS = "./@_:\\"


def _is_embedded(text: str, tok: "_Token") -> bool:
    """True for words inside URLs, emails, file names or identifiers (ipfs.github.io, num_certs)."""
    before = text[tok.start - 1] if tok.start > 0 else ""
    before2 = text[tok.start - 2] if tok.start > 1 else ""
    after = text[tok.end] if tok.end < len(text) else ""
    after2 = text[tok.end + 1] if tok.end + 1 < len(text) else ""
    if before and before in _EMBED_CHARS and before2.isalnum():
        return True
    if before in "/@_\\" and before:
        return True
    if after and after in _EMBED_CHARS and after2.isalnum():
        return True
    if after in "/@_\\" and after:
        return True
    return False


def correct_text(
    text: str,
    terms: Sequence[str],
    known_words: Optional[FrozenSet[str]] = None,
    use_system_dictionary: bool = True,
) -> CorrectionResult:
    """
    Replace close mishearings of listed terms with the listed spelling.

    Exact fixes (same letters, different case/spacing: "prd" -> "PRD", "anti gravity" ->
    "Antigravity") need the heard word not to be an ordinary word. Fuzzy fixes ("Rahool" ->
    "Rahul") additionally need: a term of 5+ letters, the same first letter, at most 1 edit
    (2 for terms of 8+ letters; one more for vowel-only slips), and the system dictionary to
    be available. Words inside URLs, emails and identifiers are never touched.
    """
    if not text or not terms:
        return CorrectionResult(text=text)

    if known_words is None and use_system_dictionary:
        known_words = load_known_words()
    fuzzy_enabled = known_words is not None
    known: FrozenSet[str] = known_words if known_words is not None else EXTRA_COMMON_WORDS

    parsed: List[_Term] = []
    for t in terms:
        k = _key(t)
        if len(k) >= 2:
            parsed.append(_Term(t, k, max(1, len(_tokenize(t)))))
    if not parsed:
        return CorrectionResult(text=text)
    max_span = max(t.n_words for t in parsed) + 1

    tokens = _tokenize(text)
    out: List[str] = []
    corrections: List[Correction] = []
    cursor = 0
    i = 0
    while i < len(tokens):
        best: Optional[Tuple[int, int, _Term]] = None  # (distance, span_len, term)

        for span in range(min(max_span, len(tokens) - i), 0, -1):
            group = tokens[i:i + span]
            # Words in a span may only be separated by spaces or hyphens, and only the
            # last one may carry a possessive.
            if any(not _GAP_RE.match(text[a.end:b.start]) for a, b in zip(group, group[1:])):
                continue
            if any(tok.suffix for tok in group[:-1]):
                continue
            if any(("'" in tok.base or "’" in tok.base) for tok in group):
                continue  # contractions like "don't" are never names
            if _is_embedded(text, group[0]) or _is_embedded(text, group[-1]):
                continue
            cand_key = "".join(_key(tok.base) for tok in group)
            if not cand_key or any(ch.isdigit() for ch in cand_key):
                continue
            all_known = all(_is_known(tok.base, known) for tok in group)

            for term in parsed:
                if span > term.n_words + 1:
                    continue
                if cand_key == term.key:
                    if span == 1 and _is_known(group[0].base, known):
                        continue  # "will" stays "will" even if "Will" is listed
                    if span > 1 and term.n_words == 1 and term.key in known:
                        continue  # "note book" stays two words even if "Notebook" is listed
                    cand = (0, span, term)
                elif fuzzy_enabled and span >= term.n_words:
                    if len(term.key) < MIN_FUZZY_TERM_LEN or len(cand_key) < MIN_FUZZY_CANDIDATE_LEN:
                        continue
                    if cand_key[0] != term.key[0] or abs(len(cand_key) - len(term.key)) > 2:
                        continue
                    if all_known or (span > 1 and _is_known(cand_key, known)):
                        continue
                    dist = _fuzzy_match(cand_key, term.key)
                    if dist is None:
                        continue
                    cand = (dist, span, term)
                else:
                    continue
                if best is None or (cand[0], -cand[1], -len(cand[2].key)) < (best[0], -best[1], -len(best[2].key)):
                    best = cand

        if best is None:
            i += 1
            continue

        _, span, term = best
        group = tokens[i:i + span]
        start, end = group[0].start, group[-1].end
        original = text[start:end]
        replacement = term.text + group[-1].suffix
        if original != replacement:
            out.append(text[cursor:start])
            out.append(replacement)
            cursor = end
            corrections.append(Correction(original=original, replacement=replacement))
        i += span

    out.append(text[cursor:])
    return CorrectionResult(text="".join(out), corrections=tuple(corrections))
