"""Unit and integration tests for MLXWhisperTranscriber."""

from pathlib import Path
import threading
import numpy as np
import pytest

from engine.config import WhisperConfig
from engine.models import TranscriptionResult
from engine.transcriber import MLXWhisperTranscriber, resolve_local_model, resolve_model_path


@pytest.fixture(scope="module")
def transcriber():
    project_root = Path(__file__).resolve().parent.parent
    models_dir = project_root / "models"
    config = WhisperConfig(
        model_size="small",
        language="en",
        initial_prompt="",
        models_dir=models_dir,
    )
    tx = MLXWhisperTranscriber(config)
    assert tx.wait_until_ready(timeout=60.0), "Model failed to load/warmup in time"
    yield tx
    tx.close()


def test_resolve_model_path():
    project_root = Path(__file__).resolve().parent.parent
    models_dir = project_root / "models"
    resolved = resolve_model_path("small", models_dir)
    # Since we downloaded small into models/whisper-small-mlx, it should point to local path
    assert "whisper-small-mlx" in resolved


def _fake_model(models_dir: Path, name: str, weights: str = "weights.npz") -> Path:
    d = models_dir / name
    d.mkdir(parents=True)
    (d / weights).write_bytes(b"")
    return d


def test_resolve_local_model_uses_configured_model_when_present(tmp_path):
    _fake_model(tmp_path, "whisper-small-mlx")
    _fake_model(tmp_path, "whisper-large-v3-turbo-mlx", "weights.safetensors")
    path, active = resolve_local_model("large-v3-turbo", tmp_path)
    assert active == "large-v3-turbo"
    assert path.endswith("whisper-large-v3-turbo-mlx")


def test_resolve_local_model_falls_back_to_small_when_missing(tmp_path):
    _fake_model(tmp_path, "whisper-small-mlx")
    path, active = resolve_local_model("large-v3-turbo", tmp_path)
    assert active == "small"
    assert path.endswith("whisper-small-mlx")


def test_resolve_local_model_never_returns_a_repo_id(tmp_path):
    # Nothing on disk: must not hand back a Hugging Face repo id (which would trigger a download)
    path, active = resolve_local_model("large-v3-turbo", tmp_path)
    assert path is None
    assert active == "large-v3-turbo"


def test_transcriber_without_local_weights_fails_clearly(tmp_path):
    config = WhisperConfig(model_size="large-v3-turbo", language="en", initial_prompt="", models_dir=tmp_path)
    tx = MLXWhisperTranscriber(config)
    try:
        assert tx.wait_until_ready(timeout=10.0)
        assert tx.model_path is None
        with pytest.raises(RuntimeError, match="not downloaded"):
            tx.transcribe(np.zeros(16000, dtype=np.float32))
    finally:
        tx.close()


def test_real_config_model_resolves_locally():
    # Whatever config.toml names, the app must find weights on disk (or the small fallback)
    from engine.config import load_config
    cfg = load_config()
    path, _ = resolve_local_model(cfg.whisper.model_size, cfg.whisper.models_dir)
    assert path is not None and Path(path).is_dir()


def test_digital_silence_is_never_transcribed(transcriber):
    result = transcriber.transcribe(np.zeros(8000, dtype=np.float32))
    assert result.text == ""
    assert result.processing_time_sec == 0.0


def test_quiet_speech_level_is_not_treated_as_silence(transcriber):
    # A -40 dBFS tone is far quieter than real speech here but well above the silence guard
    t = np.arange(16000) / 16000.0
    tone = (10 ** (-40 / 20) * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    result = transcriber.transcribe(tone)
    assert result.processing_time_sec > 0.0


def test_transcribe_sample_wav(transcriber):
    sample_wav = Path(__file__).resolve().parent / "data" / "sample.wav"
    assert sample_wav.is_file(), "tests/data/sample.wav not found"

    result = transcriber.transcribe(sample_wav)

    assert isinstance(result, TranscriptionResult)
    assert isinstance(result.text, str)
    assert result.audio_duration_sec > 0
    assert result.processing_time_sec > 0
    assert result.real_time_factor > 0


def test_transcribe_async(transcriber):
    sample_wav = Path(__file__).resolve().parent / "data" / "sample.wav"
    done_event = threading.Event()
    received_result = None

    def on_complete(res):
        nonlocal received_result
        received_result = res
        done_event.set()

    transcriber.transcribe_async(sample_wav, callback=on_complete)
    assert done_event.wait(timeout=10.0), "Async transcription timed out"
    assert received_result is not None
    assert isinstance(received_result.text, str)


def test_transcribe_numpy_array(transcriber):
    # 1 second of silence / tone
    audio = np.zeros(16000, dtype=np.float32)
    result = transcriber.transcribe(audio)
    assert isinstance(result, TranscriptionResult)
    assert abs(result.audio_duration_sec - 1.0) < 0.01


def test_transcribe_with_and_without_normalization(transcriber):
    sample_wav = Path(__file__).resolve().parent / "data" / "sample.wav"
    assert sample_wav.is_file()

    # Transcribe with normalization
    res_norm = transcriber.transcribe(sample_wav, normalize=True)
    assert isinstance(res_norm, TranscriptionResult)

    # Transcribe without normalization
    res_raw = transcriber.transcribe(sample_wav, normalize=False)
    assert isinstance(res_raw, TranscriptionResult)
    assert res_raw.applied_gain_db == 0.0


def test_transcribe_does_not_modify_wav_file(transcriber):
    import hashlib
    sample_wav = Path(__file__).resolve().parent / "data" / "sample.wav"
    assert sample_wav.is_file()

    with open(sample_wav, "rb") as f:
        before_hash = hashlib.sha256(f.read()).hexdigest()

    # Transcribe with normalization enabled
    _ = transcriber.transcribe(sample_wav, normalize=True)

    with open(sample_wav, "rb") as f:
        after_hash = hashlib.sha256(f.read()).hexdigest()

    assert before_hash == after_hash, "Saved WAV file was altered during transcription!"
