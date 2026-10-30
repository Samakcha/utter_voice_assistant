"""Audio signal processing utilities: level measurement and peak normalization."""

import logging
from typing import Tuple
import numpy as np

logger = logging.getLogger(__name__)


def compute_audio_levels(audio: np.ndarray) -> Tuple[float, float]:
    """
    Calculate peak and RMS levels of audio in dBFS (Decibels relative to Full Scale).
    Assumes float32 samples normalized to [-1.0, 1.0], where 1.0 = 0 dBFS.

    Args:
        audio: 1D numpy array of audio samples.

    Returns:
        Tuple of (peak_dbfs, rms_dbfs).
        If audio is empty or near dead silent (< 1e-7), returns (-100.0, -100.0).
    """
    if audio.size == 0:
        return -100.0, -100.0

    if audio.ndim > 1:
        audio = audio.squeeze()

    peak = float(np.max(np.abs(audio)))
    peak_dbfs = 20.0 * np.log10(peak) if peak > 1e-7 else -100.0

    # Root Mean Square
    rms = float(np.sqrt(np.mean(np.square(audio))))
    rms_dbfs = 20.0 * np.log10(rms) if rms > 1e-7 else -100.0

    return peak_dbfs, rms_dbfs


def normalize_audio(
    audio: np.ndarray,
    target_peak_dbfs: float = -3.0,
    max_gain_db: float = 20.0,
) -> Tuple[np.ndarray, float]:
    """
    Apply gain so the audio's peak reaches `target_peak_dbfs`, capped at `max_gain_db`.
    Guarantees no clipping (output clamped to [-1.0, 1.0]).
    Does not modify the input array in place; returns a new array and the applied gain in dB.

    Args:
        audio: 1D float32 numpy array in range [-1.0, 1.0].
        target_peak_dbfs: Desired peak level in dBFS (e.g. -3.0 dBFS).
        max_gain_db: Maximum boost in dB to avoid blowing up background noise on quiet clips.

    Returns:
        (normalized_audio, applied_gain_db)
    """
    if audio.size == 0:
        return audio.copy(), 0.0

    if audio.ndim > 1:
        audio = audio.squeeze()

    current_peak = float(np.max(np.abs(audio)))
    if current_peak < 1e-7:
        # Near dead silence; applying gain would only amplify numerical noise
        return audio.copy(), 0.0

    # Ensure target peak does not exceed 0 dBFS (headroom requirement to prevent clipping)
    safe_target_dbfs = min(target_peak_dbfs, 0.0)
    target_peak_linear = 10.0 ** (safe_target_dbfs / 20.0)

    # Compute required linear gain
    desired_gain_linear = target_peak_linear / current_peak
    desired_gain_db = 20.0 * np.log10(desired_gain_linear)

    # Cap gain boost at max_gain_db
    if desired_gain_db > max_gain_db:
        actual_gain_db = max_gain_db
        actual_gain_linear = 10.0 ** (max_gain_db / 20.0)
    else:
        actual_gain_db = desired_gain_db
        actual_gain_linear = desired_gain_linear

    # Apply gain to a fresh copy
    normalized = audio.astype(np.float32) * np.float32(actual_gain_linear)

    # Hard guard against clipping
    normalized = np.clip(normalized, -1.0, 1.0)

    return normalized, actual_gain_db
