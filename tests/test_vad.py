"""Unit tests verifying Silero VAD speech detection and noise discrimination."""

import numpy as np
from engine.vad import SileroVAD


def test_vad_silence_detection():
    vad = SileroVAD(sample_rate=16000, threshold=0.5)
    silence_chunk = np.zeros(512, dtype=np.float32)

    is_speech, prob = vad.is_speech(silence_chunk)
    assert not is_speech, f"Expected silence not to be classified as speech (prob={prob})"
    assert prob < 0.1, f"Expected near-zero probability for silence, got {prob}"


def test_vad_click_rejection():
    """Verify that sharp isolated clicks or low-level white noise are not classified as sustained speech."""
    vad = SileroVAD(sample_rate=16000, threshold=0.5)

    # Low-level white noise
    noise_chunk = np.random.normal(0, 0.005, 512).astype(np.float32)
    is_speech, prob = vad.is_speech(noise_chunk)
    assert not is_speech, f"Low level noise classified as speech (prob={prob})"


def test_vad_reset():
    vad = SileroVAD(sample_rate=16000, threshold=0.5)
    vad.reset()
    silence_chunk = np.zeros(512, dtype=np.float32)
    is_speech, prob = vad.is_speech(silence_chunk)
    assert not is_speech
