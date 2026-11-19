"""Unit tests verifying Silero VAD speech detection and noise discrimination."""

import numpy as np
import pytest
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


# ---- numpy voice detector (v0.7.1: no PyTorch in the app) ---------------------------------

def _sample_audio():
    import wave
    from pathlib import Path
    with wave.open(str(Path(__file__).parent / "data" / "speech.wav")) as w:
        assert w.getframerate() == 16000 and w.getsampwidth() == 2
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        if w.getnchannels() > 1:
            audio = audio.reshape(-1, w.getnchannels()).mean(axis=1)
    return audio


def test_speech_is_found_in_a_real_recording():
    audio = _sample_audio()
    vad = SileroVAD(sample_rate=16000, threshold=0.5)
    probs = [vad.is_speech(audio[i:i + 512])[1] for i in range(0, len(audio) - 511, 512)]
    assert max(probs) > 0.9 and min(probs) < 0.1


def test_same_answers_as_the_pytorch_silero_model():
    torch = pytest.importorskip("torch")
    silero_vad = pytest.importorskip("silero_vad")
    old = silero_vad.load_silero_vad()
    new = SileroVAD(sample_rate=16000, threshold=0.5)
    audio = _sample_audio()
    for gain in (1.0, 4.0):
        old.reset_states()
        new.reset()
        x = np.clip(audio * gain, -1, 1).astype(np.float32)
        for i in range(0, len(x) - 511, 512):
            chunk = x[i:i + 512]
            with torch.no_grad():
                expected = float(old(torch.from_numpy(chunk.copy()), 16000).item())
            assert abs(new.is_speech(chunk)[1] - expected) < 1e-4


def test_reset_forgets_earlier_audio():
    audio = _sample_audio()[:512 * 40]
    vad = SileroVAD(sample_rate=16000, threshold=0.5)
    first = [vad.is_speech(audio[i:i + 512])[1] for i in range(0, len(audio), 512)]
    vad.reset()
    again = [vad.is_speech(audio[i:i + 512])[1] for i in range(0, len(audio), 512)]
    assert first == again


def test_wrong_chunk_size_or_sample_rate_is_refused():
    vad = SileroVAD(sample_rate=16000, threshold=0.5)
    with pytest.raises(ValueError):
        vad.is_speech(np.zeros(256, dtype=np.float32))
    with pytest.raises(ValueError):
        SileroVAD(sample_rate=8000)


def test_utter_never_loads_pytorch():
    import subprocess
    import sys
    code = ("import sys, main, engine, engine.session, interface.app; "
            "from engine.vad import SileroVAD; SileroVAD(); "
            "print('torch' in sys.modules, 'silero_vad' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.split()[-2:] == ["False", "False"]


def test_the_weights_ship_with_the_app_and_pytorch_does_not():
    from engine.vad import WEIGHTS_PATH, load_safetensors
    from scripts import build_app
    assert WEIGHTS_PATH.parent.name == "assets" and "assets" in build_app.APP_CODE
    assert (WEIGHTS_PATH.parent / "silero_vad_LICENSE.txt").is_file()
    weights = load_safetensors(WEIGHTS_PATH)
    assert weights["stft_conv.weight"].shape == (258, 1, 256)
    assert weights["lstm_cell.weight_ih"].shape == (512, 128)
    for name in ("torch", "silero-vad", "sympy", "networkx"):
        assert name in build_app.DEV_ONLY_DISTRIBUTIONS
    tops = build_app.installed_top_level(build_app.DEV_ONLY_DISTRIBUTIONS)
    assert "torch" in tops and "numpy" not in tops and "mlx" not in tops
