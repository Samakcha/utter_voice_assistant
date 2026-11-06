"""Tests for the accuracy recorder and scorer (scripts/accuracy.py). No real mic or model."""

from pathlib import Path

import numpy as np
import pytest

from engine.commands import match_command
from scripts import accuracy as acc

SR = acc.SAMPLE_RATE


def speech(seconds=1.5, amplitude=0.2):
    t = np.arange(int(seconds * SR)) / SR
    return (amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


# ---- Sentence list --------------------------------------------------------------------

def test_prompt_file_is_well_formed():
    items = acc.load_prompts()
    ids = [i.id for i in items]
    assert len(ids) == len(set(ids)), "ids must be unique"
    assert 70 <= len(items) <= 100
    assert {i.category for i in items} <= set(acc.CATEGORY_LABELS)
    for i in items:
        assert i.text and i.text == i.text.strip()
        if i.category == "command":
            assert i.expected_action, i.id
        else:
            assert not i.expected_action, i.id


def test_command_sentences_agree_with_the_rules():
    """Said perfectly, every command sentence must do exactly what the list expects."""
    for item in acc.load_prompts():
        if item.category != "command":
            continue
        assert acc.command_correct(item.text, item.expected_action, item.expected_name), item
        m = match_command(item.text)
        assert (m.action_id if m else "none") == item.expected_action, item


def test_word_list_sentences_rotate_templates_and_are_capped():
    terms = [f"Name{i}" for i in range(20)]
    items = acc.word_list_items(terms)
    assert len(items) == acc.MAX_WORD_LIST_ITEMS
    assert items[0].text == acc.WORD_LIST_TEMPLATES[0].format(term="Name0")
    assert items[1].text == acc.WORD_LIST_TEMPLATES[1].format(term="Name1")
    assert all(i.category == "word_list" and i.expected_name in i.text for i in items)
    assert acc.word_list_items([]) == []


# ---- Saving and resuming --------------------------------------------------------------

def test_record_result_saves_wav_and_manifest_row(tmp_path):
    item = acc.Item("s01", "short", "Hello there.")
    row = acc.record_result(tmp_path, item, speech(1.0))
    wav = tmp_path / row["file"]
    assert wav.is_file() and wav.parent.name == "recordings"
    from engine.transcriber import load_audio_array
    audio, duration = load_audio_array(wav)
    assert duration == pytest.approx(1.0, abs=0.01)
    assert row["duration_sec"] == "1.00"
    assert acc.read_manifest(tmp_path)[0]["text"] == "Hello there."


def test_redo_replaces_the_row_instead_of_duplicating(tmp_path):
    item = acc.Item("s01", "short", "Hello there.")
    acc.record_result(tmp_path, item, speech(1.0))
    from datetime import datetime
    acc.record_result(tmp_path, item, speech(2.0), now=datetime(2030, 1, 1))
    rows = acc.read_manifest(tmp_path)
    assert len(rows) == 1 and rows[0]["duration_sec"] == "2.00"


def test_pending_items_resume_by_sentence_text_and_need_the_wav(tmp_path):
    items = [acc.Item("a", "short", "One."), acc.Item("b", "short", "Two."), acc.Item("c", "short", "Three.")]
    acc.record_result(tmp_path, items[0], speech())
    row_b = acc.record_result(tmp_path, items[1], speech())
    assert [i.id for i in acc.pending_items(items, acc.read_manifest(tmp_path), tmp_path)] == ["c"]

    (tmp_path / row_b["file"]).unlink()  # a deleted recording counts as not done
    assert [i.id for i in acc.pending_items(items, acc.read_manifest(tmp_path), tmp_path)] == ["b", "c"]


def test_recording_problem_flags_short_and_quiet_audio():
    assert "long" in acc.recording_problem(speech(0.3))
    assert "quiet" in acc.recording_problem(speech(2.0, amplitude=0.001))
    assert acc.recording_problem(speech(2.0)) is None


# ---- Interactive recording (fake keyboard and microphone) -----------------------------

class FakeRecorder:
    device_name = "Test Mic"

    def __init__(self, clips):
        self.clips = list(clips)
        self.open = False
        self.starts = 0

    def start(self):
        self.open = True
        self.starts += 1

    def stop(self):
        was_open, self.open = self.open, False
        if was_open and self.clips:
            return self.clips.pop(0)
        return np.zeros(0, dtype=np.float32)


def write_prompts(path: Path, rows):
    lines = ["id,category,text,expected_action,expected_name"] + [f"{i},short,{t},," for i, t in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_run_record_saves_skips_redoes_and_quits(tmp_path, capsys):
    prompts = write_prompts(tmp_path / "p.csv", [("a", "One."), ("b", "Two."), ("c", "Three."), ("d", "Four.")])
    folder = tmp_path / "acc"
    answers = iter([
        "", "", "",          # a: start, stop, keep
        "s",                 # b: skip
        "", "", "r",         # c: start, stop, redo
        "", "", "",          # c again: start, stop, keep
        "q",                 # d: quit
    ])
    rec = FakeRecorder([speech(1.0), speech(1.1), speech(1.2)])
    acc.run_record(folder, [], input_fn=lambda _p: next(answers), recorder=rec, prompts_path=prompts)

    rows = acc.read_manifest(folder)
    assert sorted(r["id"] for r in rows) == ["a", "c"]
    assert next(r for r in rows if r["id"] == "c")["duration_sec"] == "1.20"  # the redo was kept
    assert rec.open is False, "the microphone must be closed when recording stops"

    # Running again resumes with what is left
    answers2 = iter(["q"])
    acc.run_record(folder, [], input_fn=lambda _p: next(answers2), recorder=FakeRecorder([]), prompts_path=prompts)
    out = capsys.readouterr().out
    assert "2 already recorded, 2 to go" in out


def test_run_record_offers_redo_for_a_bad_take(tmp_path):
    prompts = write_prompts(tmp_path / "p.csv", [("a", "One.")])
    folder = tmp_path / "acc"
    answers = iter(["", "", "",     # too short -> Enter = redo
                    "", "", ""])    # good take -> keep
    rec = FakeRecorder([speech(0.2), speech(1.5)])
    acc.run_record(folder, [], input_fn=lambda _p: next(answers), recorder=rec, prompts_path=prompts)
    rows = acc.read_manifest(folder)
    assert len(rows) == 1 and rows[0]["duration_sec"] == "1.50"


def test_run_record_closes_the_mic_on_ctrl_c(tmp_path):
    prompts = write_prompts(tmp_path / "p.csv", [("a", "One.")])
    calls = {"n": 0}

    def _input(_prompt):
        calls["n"] += 1
        if calls["n"] == 2:  # while recording
            raise KeyboardInterrupt
        return ""

    rec = FakeRecorder([speech()])
    with pytest.raises(KeyboardInterrupt):
        acc.run_record(tmp_path / "acc", [], input_fn=_input, recorder=rec, prompts_path=prompts)
    assert rec.open is False


# ---- Scoring --------------------------------------------------------------------------

def test_word_errors_counts_edits_and_ignores_punctuation_and_case():
    assert acc.word_errors("Open notes.", "open Notes") == 0
    assert acc.word_errors("open notes", "open nodes") == 1
    assert acc.word_errors("we need milk and eggs", "we need milk eggs please") == 2
    assert acc.word_errors("", "") == 0


def test_command_correct_checks_action_name_and_do_nothing():
    assert acc.command_correct("Open notes.", "open_notes", "")
    assert not acc.command_correct("Open nodes, please now", "open_notes", "")
    assert acc.command_correct("Search WhatsApp for Rahul.", "whatsapp_search", "Rahul")
    assert not acc.command_correct("Search WhatsApp for Raul.", "whatsapp_search", "Rahul")
    assert acc.command_correct("Take a note about the groceries.", "none", "")
    assert not acc.command_correct("Take a note.", "none", "")


def build_folder(tmp_path, items):
    folder = tmp_path / "acc"
    for it in items:
        acc.record_result(folder, it, speech())
    return folder


def test_score_uses_word_list_for_dictation_but_never_for_commands(tmp_path):
    items = [
        acc.Item("c01", "command", "open notes", "open_notes"),
        acc.Item("s01", "short", "We need more milk."),
        acc.Item("w01", "word_list", "Please add Saanvi to the notes for tomorrow.", expected_name="Saanvi"),
    ]
    folder = build_folder(tmp_path, items)
    by_id = {r["id"]: r["file"] for r in acc.read_manifest(folder)}
    prompts_seen = []

    def fake_transcribe(path, prompt):
        prompts_seen.append((path.name.split("-")[0], prompt))
        rid = path.name.split("-")[0]
        if rid == "c01":
            return "Open notes."
        if rid == "s01":
            return "We need more milk."
        # Without the hint Whisper spells the name wrong; with it, nearly right ("Saanvee"),
        # and the strict post-correction fixes the vowel slip.
        return "Please add Saanvee to the notes for tomorrow." if prompt else "Please add Sanavi to the notes for tomorrow."

    pipeline = acc.AppPipeline(fake_transcribe, terms=["Saanvi"])
    results = {r.row["id"]: r for r in acc.score_recordings(folder, pipeline)}

    assert results["c01"].command_ok is True and results["c01"].heard_without_list is None
    assert [p for rid, p in prompts_seen if rid == "c01"] == [None]
    assert results["w01"].heard == "Please add Saanvi to the notes for tomorrow."
    assert results["w01"].term_ok is True and results["w01"].term_ok_without_list is False
    assert results["w01"].errors == 0 and results["w01"].errors_without_list == 1

    summary = acc.summarize(list(results.values()))
    assert summary["_dictation"]["right"] == 100.0
    assert summary["_dictation"]["right_without_list"] < 100.0
    assert summary["command"]["commands_ok"] == 1
    assert summary["word_list"]["terms_ok"] == 1 and summary["word_list"]["terms_ok_without_list"] == 0


def test_summary_is_word_weighted_and_report_lists_mistakes(tmp_path):
    items = [
        acc.Item("s01", "short", "one two three four five six seven eight nine ten"),
        acc.Item("s02", "short", "hello there"),
        acc.Item("c19", "command", "take a note about the groceries", "none"),
    ]
    folder = build_folder(tmp_path, items)
    heard = {
        "s01": "one two three four five six seven eight nine ten",
        "s02": "hello bear",
        "c19": "Take a note.",  # misheard: the app would have started a note
    }
    pipeline = acc.AppPipeline(lambda p, _prompt: heard[p.name.split("-")[0]], terms=[])
    results = acc.score_recordings(folder, pipeline)
    summary = acc.summarize(results)

    # 1 error in 12 words, not the average of 0% and 50% per sentence
    assert summary["_dictation"]["errors"] == 1 and summary["_dictation"]["words"] == 12
    assert summary["_dictation"]["right"] == pytest.approx(100 * 11 / 12)
    assert summary["command"]["commands_ok"] == 0

    report = acc.format_report(results, summary, model="test-model")
    assert "DICTATION: 91.7% of words right" in report
    assert "COMMANDS:  0 of 1" in report
    assert "should: do nothing" in report
    assert "heard: hello bear" in report
    assert "without your word list" not in report  # empty list: no comparison

    csv_path = tmp_path / "items.csv"
    acc.write_item_csv(csv_path, results)
    assert "hello bear" in csv_path.read_text(encoding="utf-8")


def test_report_for_no_recordings():
    assert "No recordings found" in acc.format_report([], {}, model="x")


def test_default_folder_is_in_the_user_data_folder_not_the_project():
    project = Path(acc.__file__).resolve().parent.parent
    assert "Library/Application Support/VoiceAssistant/accuracy" in str(acc.DEFAULT_ACCURACY_DIR)
    assert project not in acc.DEFAULT_ACCURACY_DIR.parents
