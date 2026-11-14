"""
Dictation cleanup: remove filler words, accidental repeats and clear self-corrections.

Rules only (offline, no language model), applied to the final dictation or note text.
Every rule is deliberately narrow: when a rule is not sure, the text is left as spoken.

  Fillers       "Um, I think uh we should go"         -> "I think we should go"
  Repeats       "the the report", "can you can you"   -> "the report", "can you"
  Corrections   "Monday, no wait, Tuesday"            -> "Tuesday"
                "at 6, I mean 7"                       -> "at 7"
                "the red one, I mean the blue one"     -> "the blue one"
  Scratch that  "Call him. Scratch that. Email him."   -> "Email him."

Never touched: "very very good", "no no no", "bye bye", "I know that that is true",
"I mean it", "I'm sorry, Tuesday doesn't work", "like", "you know".
"""

from dataclasses import dataclass, field
import re
from typing import List, Optional, Sequence, Tuple

# ---- Tokens ----------------------------------------------------------------------------
# Text is split at spaces; each token keeps its own punctuation ("Monday," -> lead "",
# core "Monday", trail ","), so removing a word never leaves stray commas or spaces.

_CORE_RE = re.compile(r"^(\W*?)([\w$£€%][\w'’$£€%:.\-]*?[\w%]|[\w$£€%])(\W*)$")
_TERMINAL = (".", "!", "?")


@dataclass
class _Token:
    lead: str
    core: str
    trail: str
    capitalize: bool = False  # became the first word of a sentence after a removal

    @property
    def word(self) -> str:
        return self.core.lower().replace("’", "'")

    @property
    def ends_sentence(self) -> bool:
        return any(p in self.trail for p in _TERMINAL)

    def render(self) -> str:
        core = _cap_first(self.core) if self.capitalize else self.core
        return f"{self.lead}{core}{self.trail}"


def _cap_first(word: str) -> str:
    """Capitalize a sentence's new first word, but never "iPhone" or "eBay"."""
    if word[:1].isalpha() and word[:1].islower() and not any(c.isupper() for c in word[1:]):
        return word[:1].upper() + word[1:]
    return word


def _tokenize(text: str) -> List[_Token]:
    tokens = []
    for raw in text.split():
        m = _CORE_RE.match(raw)
        if m:
            tokens.append(_Token(m.group(1), m.group(2), m.group(3)))
        else:  # punctuation only ("-", "...")
            tokens.append(_Token("", "", raw))
    return tokens


def _render(tokens: Sequence[_Token]) -> str:
    return " ".join(t.render() for t in tokens if t.core or t.trail.strip()).strip()


def _sentence_start(tokens: Sequence[_Token], i: int) -> bool:
    return i == 0 or tokens[i - 1].ends_sentence


def _stronger_trail(a: str, b: str) -> str:
    """The trail that marks the bigger break: sentence end > comma-like > nothing."""
    def rank(t: str) -> int:
        if any(p in t for p in _TERMINAL):
            return 2
        return 1 if t.strip() else 0
    return a if rank(a) >= rank(b) else b


# ---- Result ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CleanupChange:
    kind: str  # "filler", "repeat", "correction", "scratch"
    removed: str
    replacement: str = ""


@dataclass(frozen=True)
class CleanupResult:
    text: str
    changes: Tuple[CleanupChange, ...] = field(default_factory=tuple)

    @property
    def changed(self) -> bool:
        return bool(self.changes)


# ---- 1. Filler words ------------------------------------------------------------------
# Sounds only, never real words: "like", "so", "well", "you know" and "hum" stay.
_FILLER_RE = re.compile(r"^(?:u+m+|u+h+m*|e+r+m*|e+r+|a+h+|h+m+|m{2,}|hm+)$")


def _is_filler(tok: _Token) -> bool:
    if not tok.core or (len(tok.core) > 1 and tok.core.isupper()):
        return False  # "ERR 404", "HMM" written in capitals is a code or a name, not a sound
    return bool(_FILLER_RE.match(tok.word))


def _remove_fillers(tokens: List[_Token], changes: List[CleanupChange]) -> List[_Token]:
    out: List[_Token] = []
    cap_next = False
    for i, tok in enumerate(tokens):
        if not _is_filler(tok):
            if cap_next:
                tok.capitalize = True
                cap_next = False
            out.append(tok)
            continue
        changes.append(CleanupChange("filler", tok.core))
        at_start = not out or out[-1].ends_sentence
        if at_start:
            # "Um, I think" -> "I think"; "Wait. Um. Okay." -> "Wait. Okay."
            cap_next = cap_next or tok.core[:1].isupper() or not out
            if tok.capitalize:
                cap_next = True
        elif out[-1].trail.strip() == "," and tok.trail.strip() == ",":
            # "We need, uh, three" -> "We need three": the commas were only there for the "uh"
            out[-1].trail = ""
        elif tok.trail.strip():
            # "I think um." -> "I think."
            out[-1].trail = _stronger_trail(out[-1].trail, tok.trail)
    return out


# ---- 2. Accidental repeats ------------------------------------------------------------
# Doubles people say on purpose, or that are correct grammar ("that that", "had had").
_KEEP_DOUBLE = {
    "very", "really", "so", "no", "yes", "yeah", "yep", "nope", "bye", "ha", "haha", "hey",
    "go", "now", "please", "well", "oh", "okay", "ok", "too", "more", "much", "many", "far",
    "long", "little", "again", "over", "round", "around", "on", "out", "up", "down", "blah",
    "bla", "tick", "ding", "la", "na", "knock", "hip", "chop", "tut", "hush", "quick", "wait",
    "run", "come", "there", "that", "had", "is", "do", "does", "did", "buh", "hello", "hi",
    "thank", "thanks", "sorry", "cha", "boo", "woo", "yay", "bang", "dum", "ho", "hee",
    "help", "stop", "never", "always", "faster", "harder", "louder", "slowly", "step",
}
_MAX_REPEAT_WORDS = 3


def _plain(tok: _Token) -> bool:
    """No punctuation after the word."""
    return not tok.trail.strip()


def _same_words(a: Sequence[_Token], b: Sequence[_Token]) -> bool:
    return all(x.core and x.word == y.word for x, y in zip(a, b)) and len(a) == len(b)


def _remove_repeats(tokens: List[_Token], changes: List[CleanupChange]) -> List[_Token]:
    out = list(tokens)
    i = 0
    while i < len(out):
        removed = False
        for n in range(_MAX_REPEAT_WORDS, 0, -1):
            first, second = out[i:i + n], out[i + n:i + 2 * n]
            if len(second) < n or not _same_words(first, second):
                continue
            if any(not _plain(t) for t in first[:-1]) or first[-1].ends_sentence:
                continue  # "Go. Go." was said on purpose; "can you, can you" is a stumble
            if any(t.word.isdigit() or any(c.isdigit() for c in t.word) for t in first):
                continue  # "5 5" may be a real number said in parts
            if n == 1 and first[0].word in _KEEP_DOUBLE:
                continue
            if n > 1 and all(t.word in _KEEP_DOUBLE for t in first):
                continue  # "no no, no no" / "bye now bye now": every word is a deliberate double
            # Keep the first copy's spelling and capital, and the second copy's punctuation
            for k in range(n):
                second[k].lead, second[k].core = first[k].lead, first[k].core
                second[k].capitalize = second[k].capitalize or first[k].capitalize
            changes.append(CleanupChange("repeat", " ".join(t.core for t in first)))
            del out[i:i + n]
            removed = True
            break
        if not removed:
            i += 1
    return out


# ---- 3. Self-corrections --------------------------------------------------------------
# Only clear correction phrases. Plain "no" counts only between two numbers or two days
# ("three, no, four"); "sorry" and "actually" on their own never count.
_STRONG_MARKERS = [
    ("no", "wait"), ("wait", "no"), ("no", "sorry"), ("sorry", "no"), ("sorry", "i", "mean"),
    ("no", "i", "mean"), ("i", "mean"), ("actually", "no"), ("or", "rather"),
]

_NUMBER_WORDS = {
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
    "nineteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
    "hundred", "thousand", "half",
}
_NUMBER_RE = re.compile(r"^[$£€]?\d+(?:[.,:]\d+)*(?:%|am|pm|st|nd|rd|th|k)?$")
_DAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
         "today", "tomorrow", "tonight", "yesterday", "weekend"}
_MONTHS = {"january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december"}
# Words a corrected phrase can start from: "the red one, I mean the blue one"
_ANCHORS = {"the", "a", "an", "at", "on", "in", "to", "for", "with", "from", "by", "my",
            "your", "our", "their", "his", "her"}
_MAX_LOOKBACK = 8  # how far back a corrected word may be
_MAX_ANCHOR_SPAN = 6  # longest phrase an anchored correction may replace
_MAX_ANCHOR_FRAGMENT = 4  # "the blue one." - a longer follow-on is a new thought, not a fix


def _kind(tok: _Token) -> Optional[str]:
    w = tok.word
    if w in _NUMBER_WORDS or _NUMBER_RE.match(w):
        return "number"
    if w in _DAYS:
        return "day"
    if w in _MONTHS and w != "may":
        return "month"
    return None


def _match_marker(tokens: Sequence[_Token], i: int) -> int:
    """Length of the correction phrase starting at tokens[i] (0: none)."""
    for marker in _STRONG_MARKERS:
        n = len(marker)
        span = tokens[i:i + n]
        if len(span) == n and all(t.word == m for t, m in zip(span, marker)):
            if all(_plain(t) for t in span[:-1]):  # "no wait", not "no. Wait"
                return n
    return 0


def _apply_correction(tokens: List[_Token], m: int, n: int, changes: List[CleanupChange]) -> Optional[List[_Token]]:
    """Try to apply the correction phrase tokens[m:m+n]. Returns new tokens or None."""
    before, after = tokens[:m], tokens[m + n:]
    if not before or not after or not after[0].core:
        return None
    # A real correction is set off by a pause: "Monday, no wait, Tuesday" or "6. I mean 7".
    # "there was no wait at the desk" and "I mean the world to her" are left alone.
    if not (before[-1].trail.strip() or tokens[m + n - 1].trail.strip()):
        return None
    first = after[0]
    kind = _kind(first)

    if kind:
        # Replace the nearest earlier word of the same kind: "at 6 tonight, I mean 7"
        lo = max(0, m - _MAX_LOOKBACK)
        target = next((j for j in range(m - 1, lo - 1, -1) if _kind(before[j]) == kind), None)
        if target is None:
            return None
        if target != m - 1 and not (first.trail.strip() or len(after) == 1):
            return None  # "I have 2 kids. I mean 2 is enough" is not a correction
        old = before[target]
        changes.append(CleanupChange("correction", old.core, first.core))
        new = _Token(old.lead, first.core, old.trail, old.capitalize)
        middle = before[target + 1:]
        if not middle:
            new.trail = first.trail
        else:
            middle = [_Token(t.lead, t.core, t.trail, t.capitalize) for t in middle]
            middle[-1].trail = first.trail
        return before[:target] + [new] + middle + after[1:]

    if first.word in _ANCHORS:
        # "bring the red one, I mean the blue one": restart from the last "the"
        lo = max(0, m - _MAX_ANCHOR_SPAN)
        target = next((j for j in range(m - 1, lo - 1, -1) if before[j].word == first.word), None)
        if target is None:
            return None
        if any(t.ends_sentence for t in before[target:m - 1]):
            return None  # never reach back across a full sentence
        fragment_end = next((k for k, t in enumerate(after) if t.trail.strip()), None)
        if fragment_end is None:
            fragment_end = len(after) - 1
        if fragment_end + 1 > _MAX_ANCHOR_FRAGMENT:
            return None  # "I mean the beach is my favourite" is a new thought
        changes.append(CleanupChange(
            "correction",
            " ".join(t.core for t in before[target:]),
            " ".join(t.core for t in after if t.core)[:60],
        ))
        start = _Token(before[target].lead, first.core, first.trail, before[target].capitalize)
        return before[:target] + [start] + after[1:]
    return None


def _bare_no_correction(tokens: List[_Token], i: int, changes: List[CleanupChange]) -> Optional[List[_Token]]:
    """"three, no, four" / "Monday, no, Tuesday": plain "no" between two of a kind."""
    if i == 0 or i + 1 >= len(tokens):
        return None
    prev, nxt = tokens[i - 1], tokens[i + 1]
    kind = _kind(prev)
    if kind is None or _kind(nxt) != kind or prev.ends_sentence or tokens[i].ends_sentence:
        return None
    changes.append(CleanupChange("correction", prev.core, nxt.core))
    new = _Token(prev.lead, nxt.core, nxt.trail, prev.capitalize)
    return tokens[:i - 1] + [new] + tokens[i + 2:]


def _apply_corrections(tokens: List[_Token], changes: List[CleanupChange]) -> List[_Token]:
    out = list(tokens)
    i = 0
    while i < len(out):
        n = _match_marker(out, i)
        result = None
        if n:
            result = _apply_correction(out, i, n, changes)
        elif out[i].word == "no":
            result = _bare_no_correction(out, i, changes)
        if result is not None:
            out = result
            i = 0  # rescan: positions moved
            continue
        i += 1
    return out


# ---- 4. "Scratch that" ----------------------------------------------------------------
_SCRATCH = {("scratch", "that"), ("strike", "that")}


def _apply_scratch(tokens: List[_Token], changes: List[CleanupChange]) -> List[_Token]:
    out = list(tokens)
    i = 1
    while i < len(out):
        pair = (out[i - 1].word, out[i].word)
        if pair not in _SCRATCH or not _plain(out[i - 1]):
            i += 1
            continue
        start, end = i - 1, i + 1  # the phrase itself
        # It must stand on its own: "... tomorrow. Scratch that." / "..., scratch that."
        # "scratch that itch" goes on with more words and is left alone.
        followed_by_break = out[i].trail.strip() != "" or end == len(out)
        if not followed_by_break or start == 0:
            i += 1
            continue
        # Remove the sentence it takes back: the current one if the phrase is mid-sentence,
        # otherwise the previous one.
        j = start - 1
        if out[j].ends_sentence:
            j -= 1
        while j >= 0 and not out[j].ends_sentence:
            j -= 1
        cut_from = j + 1
        removed = " ".join(t.core for t in out[cut_from:start] if t.core)
        changes.append(CleanupChange("scratch", removed))
        rest = out[end:]
        if rest:
            rest[0].capitalize = True
        out = out[:cut_from] + rest
        i = max(1, cut_from)
    return out


# ---- Public ---------------------------------------------------------------------------

def clean_dictation(
    text: str,
    fillers: bool = True,
    repeats: bool = True,
    corrections: bool = True,
) -> CleanupResult:
    """Tidy dictated text. Returns the new text and what was changed (for the log)."""
    if not text or not text.strip():
        return CleanupResult(text or "")
    tokens = _tokenize(text)
    changes: List[CleanupChange] = []
    if fillers:
        tokens = _remove_fillers(tokens, changes)
    if corrections:
        tokens = _apply_corrections(tokens, changes)
        tokens = _apply_scratch(tokens, changes)
    if repeats:
        tokens = _remove_repeats(tokens, changes)
    if not changes:
        return CleanupResult(text)
    cleaned = _render(tokens)
    if cleaned and text.strip()[:1].isupper():
        cleaned = _cap_first(cleaned)
    if cleaned and not re.search(r"[.!?…]$", cleaned) and re.search(r"[.!?]$", text.strip()):
        cleaned = cleaned.rstrip(",;:") + text.strip()[-1]
    return CleanupResult(cleaned, tuple(changes))
