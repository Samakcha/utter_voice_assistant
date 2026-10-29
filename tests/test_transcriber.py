"""Unit and integration tests for MLXWhisperTranscriber."""

from pathlib import Path
import threading
import numpy as np
import pytest

from engine.config import WhisperConfig
from engine.models import TranscriptionResult
from engine.transcriber import MLXWhisperTranscriber, resolve_model_path


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
