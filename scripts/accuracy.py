#!/usr/bin/env python3
"""
Accuracy score for the voice assistant, measured on your own voice.

Two steps:

  1. record  - shows one sentence at a time; you read it aloud. The audio and the exact
               sentence are saved together, so no transcripts have to be typed by hand.
               About 90 sentences (~20 minutes). Quit any time; it resumes where you left off.

  2. score   - runs every recording through the same steps the app uses (Whisper model,
               volume normalization, personal word list for dictation, the command rules
               for commands) and reports how many words came out right, per category,
               with and without the word list, plus the worst mistakes.

Usage:
  uv run python scripts/accuracy.py record
  uv run python scripts/accuracy.py score

Everything (your recordings, the sentence list with your word-list terms, the reports)
is stored in ~/Library/Application Support/VoiceAssistant/accuracy/, never in the
project folder. Delete that folder to remove it all. Nothing is ever uploaded.
"""

import argparse
import csv
from dataclasses import dataclass, field
from datetime import datetime
import os
from pathlib import Path
import sys
import threading
from typing import Callable, Dict, List, Optional, Sequence
import wave

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import re

import numpy as np

from engine.commands import match_command
from engine.config import DEFAULT_DATA_DIR


# ---- Scoring words: spelling-only differences are not errors -----------------------------
# "I'm" / "I am", "every day" / "everyday", "two" / "2" and "3 pm" / "3 p.m." mean the same
# thing, so both sides are rewritten into one form before words are compared. Real
# mishearings ("notes" / "nodes", "eggs" / "x") still count.

# Unambiguous contraction endings, expanded
_CONTRACTION_SPECIAL = {"won't": "will not", "can't": "can not", "shan't": "shall not", "ain't": "is not"}
_CONTRACTION_SUFFIXES = [("n't", " not"), ("'m", " am"), ("'ll", " will"), ("'ve", " have"), ("'re", " are")]

# "'s" (is / has / possessive) and "'d" (would / had) can't be expanded safely, so the
# long forms are contracted instead, only after these words.
_S_WORDS = {"it", "he", "she", "that", "there", "here", "what", "who", "where", "how", "when", "why"}
_D_WORDS = {"i", "you", "he", "she", "we", "they", "it", "that", "there", "who"}

# One spelling for words written either joined or apart
_SAME_WORD = {
    "everyday": "every day", "anymore": "any more", "alright": "all right",
    "cannot": "can not", "ok": "okay",
}

_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
_TENS_WORDS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
               "seventy": 70, "eighty": 80, "ninety": 90}


def _expand_contraction(word: str) -> str:
    if word in _CONTRACTION_SPECIAL:
        return _CONTRACTION_SPECIAL[word]
    for suffix, expansion in _CONTRACTION_SUFFIXES:
        if word.endswith(suffix) and len(word) > len(suffix):
            return word[: -len(suffix)] + expansion
    return word


def _join_numbers(words: List[str]) -> List[str]:
    out: List[str] = []
    i = 0
    while i < len(words):
        w = words[i]
        if w in _TENS_WORDS:
            value = _TENS_WORDS[w]
            nxt = words[i + 1] if i + 1 < len(words) else ""
            if nxt in _NUMBER_WORDS and 1 <= _NUMBER_WORDS[nxt] <= 9:
                value += _NUMBER_WORDS[nxt]
                i += 1
            out.append(str(value))
        elif w in _NUMBER_WORDS:
            out.append(str(_NUMBER_WORDS[w]))
        else:
            out.append(w)
        i += 1
    return out


def score_words(text: str) -> List[str]:
    """
    The words of a sentence in one standard form, for scoring: lowercase, no punctuation,
    contractions expanded, joined/split spellings unified, number words as digits.
    """
    text = (text or "").lower().replace("’", "'").replace("‘", "'")
    text = re.sub(r"\b([ap])\.?\s?m\b\.?", r"\1m", text)  # "p.m.", "p m" -> "pm"
    text = re.sub(r"[-–—/]", " ", text)  # "two-factor" = "two factor"
    raw = [w.strip("'") for w in re.findall(r"[\w']+", text)]
    words: List[str] = []
    for w in raw:
        if not w:
            continue
        for part in _expand_contraction(w).split():
            words.extend(_SAME_WORD.get(part, part).split())
    # Contract "it is" -> "it's", "I would" -> "I'd" (the ambiguous contractions)
    merged: List[str] = []
    for w in words:
        prev = merged[-1] if merged else ""
        if w in ("is", "has") and prev in _S_WORDS:
            merged[-1] = prev + "'s"
        elif w in ("would", "had") and prev in _D_WORDS:
            merged[-1] = prev + "'d"
        else:
            merged.append(w)
    return _join_numbers(merged)

PROMPTS_FILE = Path(__file__).resolve().parent / "accuracy_prompts.csv"
DEFAULT_ACCURACY_DIR = DEFAULT_DATA_DIR.expanduser() / "accuracy"
MANIFEST_NAME = "manifest.csv"
MANIFEST_FIELDS = [
    "id", "category", "text", "expected_action", "expected_name",
    "file", "duration_sec", "peak_dbfs", "recorded_at",
]
SAMPLE_RATE = 16000

# Sentences built from the personal word list (names and terms), one per term
WORD_LIST_TEMPLATES = [
    "Please add {term} to the notes for tomorrow.",
    "I wrote {term} in the message earlier.",
    "We talked about {term} during the call.",
    "Remember to check {term} before the meeting.",
]
MAX_WORD_LIST_ITEMS = 12

# Recordings this short or quiet are probably a mistake (mic muted, Enter pressed twice)
MIN_DURATION_SEC = 0.6
MIN_PEAK_DBFS = -45.0

CATEGORY_LABELS = {
    "command": "Commands",
    "short": "Short dictation",
    "long": "Long dictation",
    "numbers": "Numbers",
    "tech": "Tech words",
    "word_list": "Your word list",
}


@dataclass(frozen=True)
class Item:
    id: str
    category: str
    text: str
    expected_action: str = ""
    expected_name: str = ""


# ---- Sentence list -------------------------------------------------------------------

def load_prompts(path: Path = PROMPTS_FILE) -> List[Item]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        return [
            Item(
                id=row["id"].strip(),
                category=row["category"].strip(),
                text=row["text"].strip(),
                expected_action=(row.get("expected_action") or "").strip(),
                expected_name=(row.get("expected_name") or "").strip(),
            )
            for row in csv.DictReader(f)
            if row.get("id", "").strip()
        ]


def word_list_items(terms: Sequence[str], limit: int = MAX_WORD_LIST_ITEMS) -> List[Item]:
    """One sentence per word-list term (the first `limit` terms), rotating the templates."""
    items = []
    for i, term in enumerate(list(terms)[:limit]):
        template = WORD_LIST_TEMPLATES[i % len(WORD_LIST_TEMPLATES)]
        items.append(Item(id=f"w{i + 1:02d}", category="word_list", text=template.format(term=term),
                          expected_name=term))
    return items


def all_items(terms: Sequence[str], prompts_path: Path = PROMPTS_FILE) -> List[Item]:
    return load_prompts(prompts_path) + word_list_items(terms)


# ---- Manifest (what has been recorded) -----------------------------------------------

def read_manifest(folder: Path) -> List[Dict[str, str]]:
    path = folder / MANIFEST_NAME
    if not path.is_file():
        return []
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_manifest(folder: Path, rows: List[Dict[str, str]]) -> None:
    """Rewrite the manifest atomically (a crash never leaves it half-written)."""
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / (MANIFEST_NAME + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in MANIFEST_FIELDS})
    os.replace(tmp, folder / MANIFEST_NAME)


def _item_key(category: str, text: str) -> str:
    return f"{category}|{text}"


def pending_items(items: Sequence[Item], manifest: Sequence[Dict[str, str]], folder: Path) -> List[Item]:
    """Items not recorded yet. Matched by sentence text, so editing the word list is safe."""
    done = {
        _item_key(row["category"], row["text"])
        for row in manifest
        if (folder / row.get("file", "")).is_file()
    }
    return [it for it in items if _item_key(it.category, it.text) not in done]


def record_result(folder: Path, item: Item, audio: np.ndarray, now: Optional[datetime] = None) -> Dict[str, str]:
    """Save one recording as a 16-bit WAV and add (or replace) its manifest row."""
    from engine.audio_utils import compute_audio_levels

    now = now or datetime.now()
    rec_dir = folder / "recordings"
    rec_dir.mkdir(parents=True, exist_ok=True)
    rel = f"recordings/{item.id}-{now.strftime('%Y%m%d-%H%M%S')}.wav"
    save_wav(folder / rel, audio)
    peak, _ = compute_audio_levels(audio)
    row = {
        "id": item.id, "category": item.category, "text": item.text,
        "expected_action": item.expected_action, "expected_name": item.expected_name,
        "file": rel, "duration_sec": f"{len(audio) / SAMPLE_RATE:.2f}",
        "peak_dbfs": f"{peak:.1f}", "recorded_at": now.isoformat(timespec="seconds"),
    }
    rows = [r for r in read_manifest(folder) if _item_key(r["category"], r["text"]) != _item_key(item.category, item.text)]
    rows.append(row)
    write_manifest(folder, rows)
    return row


def save_wav(path: Path, audio: np.ndarray) -> None:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm.tobytes())


def recording_problem(audio: np.ndarray) -> Optional[str]:
    """A reason to suggest redoing this recording, or None if it looks fine."""
    from engine.audio_utils import compute_audio_levels

    duration = len(audio) / SAMPLE_RATE
    if duration < MIN_DURATION_SEC:
        return f"only {duration:.1f} s long"
    peak, _ = compute_audio_levels(audio)
    if peak < MIN_PEAK_DBFS:
        return f"very quiet (peak {peak:.0f} dBFS) - is the right microphone selected?"
    return None


# ---- Recording (interactive) ---------------------------------------------------------

class _Recorder:
    def __init__(self):
        from engine.audio_capture import AudioCapture
        self._capture = AudioCapture(sample_rate=SAMPLE_RATE, chunk_size=512, channels=1)
        self._chunks: List[np.ndarray] = []
        self._lock = threading.Lock()

    @property
    def device_name(self) -> str:
        return self._capture.get_input_device_name()

    def start(self) -> None:
        with self._lock:
            self._chunks = []
        self._capture.start(self._on_chunk)

    def _on_chunk(self, chunk: np.ndarray) -> None:
        with self._lock:
            self._chunks.append(chunk)

    def stop(self) -> np.ndarray:
        self._capture.stop()
        with self._lock:
            chunks, self._chunks = self._chunks, []
        return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip().lower()
    except EOFError:
        return "q"


def run_record(folder: Path, terms: Sequence[str], input_fn: Callable[[str], str] = _ask,
               recorder=None, prompts_path: Path = PROMPTS_FILE) -> int:
    items = all_items(terms, prompts_path)
    todo = pending_items(items, read_manifest(folder), folder)
    done_count = len(items) - len(todo)
    recorder = recorder or _Recorder()

    print("\nAccuracy recordings")
    print("=" * 60)
    print(f"Microphone: {recorder.device_name}")
    print(f"Saved to:   {folder}")
    print(f"Sentences:  {len(items)} in total, {done_count} already recorded, {len(todo)} to go")
    if not terms:
        print("            (your word list is empty, so there are no word-list sentences)")
    print("\nHow it works: press Enter, read the sentence out loud the way you normally")
    print("dictate, then press Enter again. Read it exactly as written; if you stumble,")
    print("redo it. Commands are said as commands. Quit any time with q: progress is saved.")
    print("=" * 60)

    if not todo:
        print("\nEverything is recorded. Next step: uv run python scripts/accuracy.py score")
        return 0

    try:
        return _record_loop(folder, items, todo, done_count, input_fn, recorder)
    finally:
        recorder.stop()  # the microphone is never left open (Ctrl+C, errors)


def _record_loop(folder, items, todo, done_count, input_fn, recorder) -> int:
    for n, item in enumerate(todo, start=done_count + 1):
        label = CATEGORY_LABELS.get(item.category, item.category)
        while True:
            print(f"\n[{n}/{len(items)}] {label} - say:")
            print(f"    {item.text}")
            choice = input_fn("  Enter = start recording, s = skip, q = quit > ")
            if choice == "q":
                print(f"\nStopped. {n - 1} of {len(items)} recorded. Run 'record' again to continue.")
                return 0
            if choice == "s":
                break
            recorder.start()
            input_fn("  ● Recording... press Enter when you have finished speaking ")
            audio = recorder.stop()
            problem = recording_problem(audio)
            if problem:
                again = input_fn(f"  That recording is {problem}. Enter = redo, k = keep anyway > ")
                if again != "k":
                    continue
            else:
                again = input_fn(f"  Got {len(audio) / SAMPLE_RATE:.1f} s. Enter = next, r = redo > ")
                if again == "r":
                    continue
            record_result(folder, item, audio)
            break

    remaining = pending_items(items, read_manifest(folder), folder)
    if remaining:
        print(f"\nDone for now; {len(remaining)} skipped. Run 'record' again to do them.")
    print("\nNext step: uv run python scripts/accuracy.py score")
    return 0


# ---- Scoring -------------------------------------------------------------------------

def word_errors(reference: str, hypothesis: str) -> int:
    """Word-level edit distance (substitutions + insertions + deletions) on score_words()."""
    ref = score_words(reference)
    hyp = score_words(hypothesis)
    prev = list(range(len(hyp) + 1))
    for i in range(1, len(ref) + 1):
        cur = [i] + [0] * len(hyp)
        for j in range(1, len(hyp) + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            cur[j] = min(prev[j - 1] + cost, prev[j] + 1, cur[j - 1] + 1)
        prev = cur
    return prev[len(hyp)]


@dataclass
class ScoredItem:
    row: Dict[str, str]
    heard: str
    errors: int
    words: int
    heard_without_list: Optional[str] = None
    errors_without_list: Optional[int] = None
    command_ok: Optional[bool] = None
    term_ok: Optional[bool] = None
    term_ok_without_list: Optional[bool] = None


def command_correct(heard: str, expected_action: str, expected_name: str) -> bool:
    """Would the app have done the right thing? 'none' means it should do nothing."""
    match = match_command(heard)
    if expected_action == "none":
        return match is None
    if match is None or match.action_id != expected_action:
        return False
    if expected_name:
        return (match.extracted_name or "").casefold() == expected_name.casefold()
    return True


def _term_present(text: str, term: str) -> bool:
    return term in text  # exact spelling and capitalization, as the user wants it written


class AppPipeline:
    """The same transcription steps the app applies, around any transcribe(audio, prompt) function."""

    def __init__(self, transcribe: Callable[[Path, Optional[str]], str], terms: Sequence[str],
                 base_prompt: str = "", max_terms: int = 50, auto_correct: bool = True):
        self.transcribe = transcribe
        self.terms = list(terms)
        self.base_prompt = base_prompt
        self.max_terms = max_terms
        self.auto_correct = auto_correct

    def command(self, path: Path) -> str:
        return self.transcribe(path, None)  # commands never use the word list

    def dictation(self, path: Path, use_word_list: bool = True) -> str:
        from engine.vocabulary import build_whisper_prompt, correct_text, strip_prompt_echo

        if not use_word_list or not self.terms:
            return self.transcribe(path, self.base_prompt or None)
        prompt = build_whisper_prompt(self.terms, base_prompt=self.base_prompt, max_terms=self.max_terms)
        text = strip_prompt_echo(self.transcribe(path, prompt), self.terms)
        if self.auto_correct:
            text = correct_text(text, self.terms).text
        return text


def score_recordings(folder: Path, pipeline: AppPipeline, compare: bool = True,
                     progress: Optional[Callable[[int, int], None]] = None) -> List[ScoredItem]:
    rows = [r for r in read_manifest(folder) if (folder / r.get("file", "")).is_file()]
    results = []
    for i, row in enumerate(rows, start=1):
        if progress:
            progress(i, len(rows))
        path = folder / row["file"]
        expected = row["text"]
        words = len(score_words(expected))
        if row["category"] == "command":
            heard = pipeline.command(path)
            results.append(ScoredItem(
                row=row, heard=heard, errors=word_errors(expected, heard), words=words,
                command_ok=command_correct(heard, row.get("expected_action", ""), row.get("expected_name", "")),
            ))
            continue
        heard = pipeline.dictation(path, use_word_list=True)
        item = ScoredItem(row=row, heard=heard, errors=word_errors(expected, heard), words=words)
        if compare and pipeline.terms:
            plain = pipeline.dictation(path, use_word_list=False)
            item.heard_without_list = plain
            item.errors_without_list = word_errors(expected, plain)
        term = row.get("expected_name", "")
        if row["category"] == "word_list" and term:
            item.term_ok = _term_present(heard, term)
            if item.heard_without_list is not None:
                item.term_ok_without_list = _term_present(item.heard_without_list, term)
        results.append(item)
    return results


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _pct_right(errors: int, words: int) -> float:
    return max(0.0, 100.0 * (1.0 - errors / words)) if words else 0.0


def summarize(results: Sequence[ScoredItem]) -> Dict[str, Dict[str, float]]:
    """Per category and overall: words, errors, % words right; plus command and term accuracy."""
    summary: Dict[str, Dict[str, float]] = {}
    groups: Dict[str, List[ScoredItem]] = {}
    for r in results:
        groups.setdefault(r.row["category"], []).append(r)
    order = [c for c in CATEGORY_LABELS if c in groups] + [c for c in groups if c not in CATEGORY_LABELS]

    def _stats(items: Sequence[ScoredItem]) -> Dict[str, float]:
        words = sum(i.words for i in items)
        errors = sum(i.errors for i in items)
        s = {"count": len(items), "words": words, "errors": errors,
             "wer": (errors / words * 100.0) if words else 0.0, "right": _pct_right(errors, words)}
        compared = [i for i in items if i.errors_without_list is not None]
        if compared:
            cw = sum(i.words for i in compared)
            ce = sum(i.errors_without_list for i in compared)
            s["right_without_list"] = _pct_right(ce, cw)
            s["right_with_list_same_items"] = _pct_right(sum(i.errors for i in compared), cw)
        cmds = [i for i in items if i.command_ok is not None]
        if cmds:
            s["commands_ok"] = sum(1 for i in cmds if i.command_ok)
            s["commands_total"] = len(cmds)
        terms = [i for i in items if i.term_ok is not None]
        if terms:
            s["terms_ok"] = sum(1 for i in terms if i.term_ok)
            s["terms_total"] = len(terms)
            plain = [i for i in terms if i.term_ok_without_list is not None]
            if plain:
                s["terms_ok_without_list"] = sum(1 for i in plain if i.term_ok_without_list)
        return s

    for cat in order:
        summary[cat] = _stats(groups[cat])
    dictation = [r for r in results if r.row["category"] != "command"]
    if dictation:
        summary["_dictation"] = _stats(dictation)
    if results:
        summary["_all"] = _stats(results)
    return summary


def format_report(results: Sequence[ScoredItem], summary: Dict[str, Dict[str, float]], model: str,
                  worst: int = 12) -> str:
    lines = []
    add = lines.append
    add(f"Accuracy report - {datetime.now().strftime('%Y-%m-%d %H:%M')} - model {model}")
    add("=" * 72)
    if not results:
        add("No recordings found. Run: uv run python scripts/accuracy.py record")
        return "\n".join(lines)

    dic = summary.get("_dictation")
    if dic:
        add(f"DICTATION: {dic['right']:.1f}% of words right  (word error rate {dic['wer']:.1f}%, "
            f"{_plural(int(dic['errors']), 'error')} in {int(dic['words'])} words, "
            f"{_plural(int(dic['count']), 'recording')})")
        if "right_without_list" in dic:
            add(f"           without your word list: {dic['right_without_list']:.1f}% "
                f"(with it: {dic['right_with_list_same_items']:.1f}% on the same recordings)")
    cmd = summary.get("command")
    if cmd and "commands_total" in cmd:
        add(f"COMMANDS:  {int(cmd['commands_ok'])} of {int(cmd['commands_total'])} would have done the right thing "
            f"({cmd['commands_ok'] / cmd['commands_total'] * 100:.0f}%)")
    wl = summary.get("word_list")
    if wl and "terms_total" in wl:
        line = f"WORD LIST: {int(wl['terms_ok'])} of {int(wl['terms_total'])} names/terms spelled exactly right"
        if "terms_ok_without_list" in wl:
            line += f" (without the list: {int(wl['terms_ok_without_list'])})"
        add(line)

    add("")
    add(f"{'Category':<18} {'Recordings':>10} {'Words':>7} {'Errors':>7} {'% right':>8}")
    add("-" * 54)
    for cat, s in summary.items():
        if cat.startswith("_"):
            continue
        add(f"{CATEGORY_LABELS.get(cat, cat):<18} {int(s['count']):>10} {int(s['words']):>7} "
            f"{int(s['errors']):>7} {s['right']:>7.1f}%")
    add("")
    add("Notes: spelling-only differences are not errors: punctuation, capitals, \"I'm\" vs")
    add("\"I am\", \"every day\" vs \"everyday\", \"two\" vs \"2\". Long sentences are scored as one")
    add("piece; the app splits dictation at pauses, which can differ slightly.")

    wrong_cmds = [r for r in results if r.command_ok is False]
    if wrong_cmds:
        add("")
        add("Commands that would have gone wrong:")
        for r in wrong_cmds:
            want = r.row.get("expected_action") or ""
            want = "do nothing" if want == "none" else want
            add(f"  [{r.row['id']}] said: {r.row['text']}")
            add(f"         heard: {r.heard}   (should: {want})")

    mistakes = sorted((r for r in results if r.errors > 0 and r.row["category"] != "command"),
                      key=lambda r: (r.errors / max(1, r.words), r.errors), reverse=True)[:worst]
    if mistakes:
        add("")
        add(f"Dictation with the most mistakes (top {len(mistakes)}):")
        for r in mistakes:
            add(f"  [{r.row['id']}] {r.errors} error(s)")
            add(f"     said:  {r.row['text']}")
            add(f"     heard: {r.heard}")
    return "\n".join(lines)


def write_item_csv(path: Path, results: Sequence[ScoredItem]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "category", "said", "heard", "errors", "words", "heard_without_list",
                    "errors_without_list", "command_ok", "term_ok"])
        for r in results:
            w.writerow([r.row["id"], r.row["category"], r.row["text"], r.heard, r.errors, r.words,
                        r.heard_without_list or "", "" if r.errors_without_list is None else r.errors_without_list,
                        "" if r.command_ok is None else r.command_ok, "" if r.term_ok is None else r.term_ok])


def run_score(folder: Path, compare: bool = True, model: Optional[str] = None) -> int:
    from engine.config import load_config
    from engine.transcriber import MLXWhisperTranscriber
    from engine.vocabulary import VocabularyStore
    import dataclasses

    config = load_config()
    whisper_cfg = config.whisper if not model else dataclasses.replace(config.whisper, model_size=model)
    manifest = [r for r in read_manifest(folder) if (folder / r.get("file", "")).is_file()]
    if not manifest:
        print(f"No recordings in {folder}. Run: uv run python scripts/accuracy.py record")
        return 1

    terms = VocabularyStore(config.vocabulary.file).terms() if config.vocabulary.file else []
    print(f"Loading {whisper_cfg.model_size} (local only)...")
    transcriber = MLXWhisperTranscriber(config=whisper_cfg, normalization_config=config.normalization)
    if not transcriber.wait_until_ready(timeout=120.0):
        print("The model did not load in time.")
        return 1
    try:
        pipeline = AppPipeline(
            transcribe=lambda path, prompt: transcriber.transcribe(path, prompt=prompt).text,
            terms=terms,
            base_prompt=config.whisper.initial_prompt,
            max_terms=config.vocabulary.max_prompt_terms,
            auto_correct=config.vocabulary.auto_correct,
        )

        def _progress(i, n):
            print(f"\r  Scoring {i}/{n}...", end="", flush=True)

        results = score_recordings(folder, pipeline, compare=compare, progress=_progress)
        print()
    finally:
        transcriber.close()

    summary = summarize(results)
    report = format_report(results, summary, model=transcriber.active_model_size or whisper_cfg.model_size)
    print("\n" + report)

    reports = folder / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (reports / f"report-{stamp}.txt").write_text(report + "\n", encoding="utf-8")
    write_item_csv(reports / f"items-{stamp}.csv", results)
    print(f"\nSaved: {reports / f'report-{stamp}.txt'}")
    print(f"       {reports / f'items-{stamp}.csv'} (every sentence, said vs heard)")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")  # never download a model from here
    parser = argparse.ArgumentParser(description="Measure the assistant's accuracy on your own voice.")
    parser.add_argument("--folder", type=Path, default=DEFAULT_ACCURACY_DIR,
                        help="Where recordings and reports are kept (default: your data folder).")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("record", help="Record the sentences (resumable).")
    p_score = sub.add_parser("score", help="Score all recordings.")
    p_score.add_argument("--no-compare", action="store_true", help="Skip the without-word-list comparison.")
    p_score.add_argument("--model", default=None, help="Override the model (must already be downloaded).")
    args = parser.parse_args(argv)

    if args.command == "record":
        from engine.config import load_config
        from engine.vocabulary import VocabularyStore
        config = load_config()
        terms = VocabularyStore(config.vocabulary.file).terms() if config.vocabulary.file else []
        try:
            return run_record(args.folder, terms)
        except KeyboardInterrupt:
            print("\nStopped. Progress is saved; run 'record' again to continue.")
            return 0
    return run_score(args.folder, compare=not args.no_compare, model=args.model)


if __name__ == "__main__":
    sys.exit(main())
