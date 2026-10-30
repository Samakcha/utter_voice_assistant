"""Unit tests for audio level measurement and peak normalization utilities."""

import numpy as np
import pytest

from engine.audio_utils import compute_audio_levels, normalize_audio


def test_compute_audio_levels_full_scale():
    # Square wave with amplitude 1.0 (0 dBFS peak, 0 dBFS RMS)
    audio = np.array([1.0, -1.0, 1.0, -1.0], dtype=np.float32)
    peak, rms = compute_audio_levels(audio)
    assert abs(peak - 0.0) < 0.01
    assert abs(rms - 0.0) < 0.01


def test_compute_audio_levels_sine_wave():
    # Full scale sine wave: peak is 0 dBFS, RMS is ~ -3.01 dBFS
    t = np.linspace(0, 1.0, 16000, endpoint=False)
    audio = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    peak, rms = compute_audio_levels(audio)
    assert abs(peak - 0.0) < 0.05
    assert abs(rms - (-3.01)) < 0.1


def test_compute_audio_levels_silence():
    audio = np.zeros(1600, dtype=np.float32)
    peak, rms = compute_audio_levels(audio)
    assert peak == -100.0
    assert rms == -100.0

    empty = np.array([], dtype=np.float32)
    peak_empty, rms_empty = compute_audio_levels(empty)
    assert peak_empty == -100.0
    assert rms_empty == -100.0


def test_compute_audio_levels_known_dbfs():
    # Signal with peak at -20 dBFS -> amplitude 0.1
    audio = np.array([0.1, -0.1, 0.05, -0.05], dtype=np.float32)
    peak, rms = compute_audio_levels(audio)
    assert abs(peak - (-20.0)) < 0.05


def test_normalize_audio_boosts_quiet_speech():
    # Signal with peak at -23 dBFS (~0.07079)
    # Target: -3.0 dBFS (~0.7079) -> required gain = +20 dB
    orig_peak = 10 ** (-23.0 / 20.0)
    audio = np.array([orig_peak, -orig_peak, orig_peak / 2], dtype=np.float32)

    norm_audio, applied_gain_db = normalize_audio(audio, target_peak_dbfs=-3.0, max_gain_db=25.0)

    new_peak, _ = compute_audio_levels(norm_audio)
    assert abs(new_peak - (-3.0)) < 0.05
    assert abs(applied_gain_db - 20.0) < 0.05


def test_normalize_audio_capped_at_max_gain():
    # Signal with peak at -50 dBFS (~0.00316)
    # Target: -3.0 dBFS -> would need +47 dB gain
    # But max_gain_db is 20 dB, so gain should be capped at 20 dB
    orig_peak = 10 ** (-50.0 / 20.0)
    audio = np.array([orig_peak, -orig_peak], dtype=np.float32)

    norm_audio, applied_gain_db = normalize_audio(audio, target_peak_dbfs=-3.0, max_gain_db=20.0)

    assert abs(applied_gain_db - 20.0) < 0.01
    new_peak, _ = compute_audio_levels(norm_audio)
    # Peak should now be -50 + 20 = -30 dBFS
    assert abs(new_peak - (-30.0)) < 0.05


def test_normalize_audio_attenuates_loud_audio():
    # Signal with peak at -1 dBFS (~0.891)
    # Target: -3.0 dBFS -> should attenuate by 2 dB
    orig_peak = 10 ** (-1.0 / 20.0)
    audio = np.array([orig_peak, -orig_peak], dtype=np.float32)

    norm_audio, applied_gain_db = normalize_audio(audio, target_peak_dbfs=-3.0, max_gain_db=20.0)

    new_peak, _ = compute_audio_levels(norm_audio)
    assert abs(new_peak - (-3.0)) < 0.05
    assert abs(applied_gain_db - (-2.0)) < 0.05


def test_normalize_audio_does_not_clip_or_mutate():
    audio = np.array([0.5, -0.8, 0.2], dtype=np.float32)
    audio_orig_copy = audio.copy()

    norm_audio, gain = normalize_audio(audio, target_peak_dbfs=0.0, max_gain_db=30.0)

    # Input array must not be mutated
    np.testing.assert_array_equal(audio, audio_orig_copy)

    # All values must strictly remain within [-1.0, 1.0]
    assert np.all(norm_audio >= -1.0)
    assert np.all(norm_audio <= 1.0)


def test_normalize_audio_silence():
    audio = np.zeros(500, dtype=np.float32)
    norm_audio, gain = normalize_audio(audio, target_peak_dbfs=-3.0, max_gain_db=20.0)
    assert gain == 0.0
    np.testing.assert_array_equal(norm_audio, audio)
