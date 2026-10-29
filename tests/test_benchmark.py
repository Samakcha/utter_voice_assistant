"""Unit tests for benchmark metrics and WER computation."""

from scripts.benchmark_whisper import compute_wer, normalize_text


def test_normalize_text():
    raw = "  Hello, WORLD! Let's test...  "
    norm = normalize_text(raw)
    assert norm == "hello world lets test"


def test_compute_wer_exact_match():
    ref = "open notes"
    hyp = "Open notes."
    assert compute_wer(ref, hyp) == 0.0


def test_compute_wer_with_substitution():
    ref = "open notes"
    hyp = "open nodes"
    # 1 substitution out of 2 words = 0.5 (50%)
    assert compute_wer(ref, hyp) == 0.5


def test_compute_wer_with_insertion():
    ref = "open notes"
    hyp = "please open notes"
    # 1 insertion out of 2 words = 0.5
    assert compute_wer(ref, hyp) == 0.5


def test_compute_wer_with_deletion():
    ref = "open notes please"
    hyp = "open notes"
    # 1 deletion out of 3 words = 1/3 ~ 0.333
    assert abs(compute_wer(ref, hyp) - (1.0 / 3.0)) < 0.001
