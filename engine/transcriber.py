"""Whisper speech-to-text transcription engine using Apple MLX framework."""

from abc import ABC, abstractmethod
import logging
from pathlib import Path
import queue
import threading
import time
from typing import Callable, Optional, Tuple, Union
import wave
import numpy as np

from .config import WhisperConfig
from .models import TranscriptionResult

logger = logging.getLogger(__name__)

# Known MLX community model repository mapping
MLX_MODEL_REPO_MAP = {
    "tiny": "mlx-community/whisper-tiny-mlx",
    "tiny.en": "mlx-community/whisper-tiny.en-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "base.en": "mlx-community/whisper-base.en-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "small.en": "mlx-community/whisper-small.en-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "medium.en": "mlx-community/whisper-medium.en-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
}


def resolve_model_path(model_size: str, models_dir: Path) -> str:
    """
    Resolve local model directory path or Hugging Face repo ID.
    Prioritizes local cached directory in models_dir if present.
    """
    repo_id = MLX_MODEL_REPO_MAP.get(model_size.lower(), model_size)

    # Check local models_dir
    local_name = model_size.lower().replace(".", "_")
    candidates = [
        models_dir / f"whisper-{model_size}-mlx",
        models_dir / f"whisper-{local_name}-mlx",
        models_dir / model_size,
        models_dir / repo_id.split("/")[-1],
    ]

    for candidate in candidates:
        if candidate.is_dir() and (
            (candidate / "weights.npz").is_file()
            or (candidate / "weights.safetensors").is_file()
        ):
            logger.info("Found local model weights at: %s", candidate)
            return str(candidate)

    # Fall back to repo ID
    return repo_id


def load_audio_array(audio: Union[str, Path, np.ndarray], target_sr: int = 16000) -> Tuple[np.ndarray, float]:
    """
    Load audio from a file path or numpy array into 1D float32 normalized [-1.0, 1.0].
    Uses Python standard library `wave` to completely eliminate external ffmpeg dependency.
    """
    if isinstance(audio, np.ndarray):
        arr = audio.astype(np.float32)
        if arr.ndim > 1:
            arr = arr[:, 0]
        duration = len(arr) / float(target_sr)
        return arr, duration

    path = Path(audio)
    if not path.is_file():
        raise FileNotFoundError(f"Audio file not found: {path}")

    # Standard WAV reader (no ffmpeg needed)
    with wave.open(str(path), "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        n_frames = wf.getnframes()
        raw_bytes = wf.readframes(n_frames)

    duration = n_frames / float(framerate) if framerate > 0 else 0.0

    if sampwidth == 2:  # 16-bit PCM
        audio_int16 = np.frombuffer(raw_bytes, dtype=np.int16)
        if n_channels > 1:
            audio_int16 = audio_int16.reshape(-1, n_channels)[:, 0]
        arr = audio_int16.astype(np.float32) / 32768.0
    elif sampwidth == 4:  # 32-bit int
        audio_int32 = np.frombuffer(raw_bytes, dtype=np.int32)
        if n_channels > 1:
            audio_int32 = audio_int32.reshape(-1, n_channels)[:, 0]
        arr = audio_int32.astype(np.float32) / 2147483648.0
    else:
        # Fallback 8-bit unsigned
        audio_uint8 = np.frombuffer(raw_bytes, dtype=np.uint8)
        if n_channels > 1:
            audio_uint8 = audio_uint8.reshape(-1, n_channels)[:, 0]
        arr = (audio_uint8.astype(np.float32) - 128.0) / 128.0

    return arr, duration


class BaseTranscriber(ABC):
    """Abstract base class for swappable speech-to-text transcribers."""

    @abstractmethod
    def transcribe(
        self,
        audio: Union[str, Path, np.ndarray],
        prompt: Optional[str] = None,
    ) -> TranscriptionResult:
        """Transcribe audio into text."""
        pass


class MLXWhisperTranscriber(BaseTranscriber):
    """
    Whisper transcriber running locally on Apple Silicon Metal GPU via MLX.
    Runs on a dedicated long-lived worker thread to honor MLX thread-affinity,
    with startup warmup to avoid first-command latency.
    """

    def __init__(
        self,
        config: WhisperConfig,
        on_ready: Optional[Callable[[], None]] = None,
    ):
        self.config = config
        self.on_ready = on_ready
        self.model_path = resolve_model_path(config.model_size, config.models_dir)

        self._queue: queue.Queue = queue.Queue()
        self._ready_event = threading.Event()
        self._shutdown_event = threading.Event()

        # Dedicated worker thread
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="transcription-worker",
            daemon=True,
        )
        self._worker_thread.start()

    @property
    def is_ready(self) -> bool:
        """True if the model has completed loading and warmup."""
        return self._ready_event.is_set()

    def wait_until_ready(self, timeout: Optional[float] = 30.0) -> bool:
        """Block until model loading and warmup finish."""
        return self._ready_event.wait(timeout=timeout)

    def transcribe(
        self,
        audio: Union[str, Path, np.ndarray],
        prompt: Optional[str] = None,
        callback: Optional[Callable[[TranscriptionResult], None]] = None,
    ) -> TranscriptionResult:
        """
        Synchronously dispatch transcription to the dedicated worker thread and wait for result.
        """
        response_queue: queue.Queue = queue.Queue()
        self._queue.put((audio, prompt, callback, response_queue))
        result, exc = response_queue.get()
        if exc is not None:
            raise exc
        return result

    def transcribe_async(
        self,
        audio: Union[str, Path, np.ndarray],
        callback: Callable[[TranscriptionResult], None],
        on_error: Optional[Callable[[Exception], None]] = None,
        prompt: Optional[str] = None,
    ) -> None:
        """Asynchronously enqueue transcription on the dedicated worker thread."""
        def _wrapper(res: TranscriptionResult):
            callback(res)

        self._queue.put((audio, prompt, _wrapper, None, on_error))

    def _worker_loop(self) -> None:
        """Long-lived dedicated worker thread running all MLX calls."""
        import mlx_whisper

        logger.info(
            "Loading Whisper model '%s' on dedicated worker thread (path=%s)...",
            self.config.model_size,
            self.model_path,
        )

        try:
            # Warm up model on 0.5s of silence to compile Metal shaders
            warmup_audio = np.zeros(8000, dtype=np.float32)
            t0 = time.perf_counter()
            _ = mlx_whisper.transcribe(
                warmup_audio,
                path_or_hf_repo=self.model_path,
                language=self.config.language,
                initial_prompt=self.config.initial_prompt or None,
            )
            t1 = time.perf_counter()
            logger.info("Whisper model '%s' warmed up successfully in %.2fs.", self.config.model_size, t1 - t0)
        except Exception as e:
            logger.error("Failed to load/warmup Whisper model: %s", e, exc_info=True)
        finally:
            self._ready_event.set()
            if self.on_ready is not None:
                try:
                    self.on_ready()
                except Exception as e:
                    logger.error("Error in on_ready callback: %s", e, exc_info=True)

        while not self._shutdown_event.is_set():
            try:
                task = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if task is None:  # Sentinel to stop
                break

            if len(task) == 4:
                audio, prompt, callback, response_queue = task
                on_error = None
            else:
                audio, prompt, callback, response_queue, on_error = task

            result = None
            error = None

            try:
                result = self._execute_transcription(audio, prompt)
                if callback is not None:
                    callback(result)
            except Exception as e:
                logger.error("Transcription execution error: %s", e, exc_info=True)
                error = e
                if on_error is not None:
                    on_error(e)
            finally:
                if response_queue is not None:
                    response_queue.put((result, error))
                self._queue.task_done()

    def _execute_transcription(
        self,
        audio: Union[str, Path, np.ndarray],
        prompt: Optional[str],
    ) -> TranscriptionResult:
        """Perform the actual mlx_whisper transcribe call on this worker thread."""
        import mlx_whisper

        audio_array, duration_sec = load_audio_array(audio)

        active_prompt = prompt if prompt is not None else self.config.initial_prompt
        # Empty string should be passed as None so Whisper doesn't bias
        initial_prompt_val = active_prompt if active_prompt else None

        t0 = time.perf_counter()
        raw_result = mlx_whisper.transcribe(
            audio_array,
            path_or_hf_repo=self.model_path,
            language=self.config.language,
            initial_prompt=initial_prompt_val,
        )
        t1 = time.perf_counter()

        text = raw_result.get("text", "").strip()
        detected_language = raw_result.get("language", self.config.language)
        processing_time_sec = t1 - t0

        return TranscriptionResult(
            text=text,
            language=detected_language,
            audio_duration_sec=duration_sec,
            processing_time_sec=processing_time_sec,
        )

    def close(self) -> None:
        """Shut down the worker thread."""
        self._shutdown_event.set()
        self._queue.put(None)
        self._worker_thread.join(timeout=3.0)
