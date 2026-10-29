"""Microphone audio capture module using sounddevice."""

import logging
import threading
from typing import Callable, Optional
import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)


class AudioCaptureError(Exception):
    """Raised when audio capture fails to start or stream encounters an error."""
    pass


class AudioCapture:
    """
    Manages low-latency audio capture from the default microphone.
    Guarantees the microphone is ONLY open while an active recording session is running.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        chunk_size: int = 512,
        channels: int = 1,
    ):
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self.channels = channels
        self._stream: Optional[sd.InputStream] = None
        self._callback: Optional[Callable[[np.ndarray], None]] = None
        self._lock = threading.Lock()

    @property
    def is_active(self) -> bool:
        """True if the microphone stream is currently open and active."""
        with self._lock:
            return self._stream is not None and self._stream.active

    def start(self, on_chunk: Callable[[np.ndarray], None]) -> None:
        """
        Open the microphone and begin streaming audio chunks.

        Args:
            on_chunk: Callback function that receives a 1D float32 numpy array of length `chunk_size`.
        """
        with self._lock:
            if self._stream is not None:
                self._close_stream()

            self._callback = on_chunk

            def _sd_callback(indata: np.ndarray, frames: int, time_info, status) -> None:
                if status:
                    logger.warning("Audio capture status: %s", status)
                if self._callback is not None:
                    # Flatten to 1D mono float32 array
                    chunk = indata[:, 0].copy() if indata.ndim > 1 else indata.copy()
                    try:
                        self._callback(chunk)
                    except Exception as e:
                        logger.error("Error in audio chunk callback: %s", e, exc_info=True)

            try:
                self._stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    blocksize=self.chunk_size,
                    channels=self.channels,
                    dtype=np.float32,
                    callback=_sd_callback,
                )
                self._stream.start()
                logger.info("Microphone stream started (%d Hz, blocksize=%d).", self.sample_rate, self.chunk_size)
            except Exception as e:
                self._stream = None
                self._callback = None
                raise AudioCaptureError(f"Failed to open microphone stream: {e}") from e

    def stop(self) -> None:
        """Stop and close the microphone stream immediately, releasing hardware."""
        with self._lock:
            self._close_stream()

    def _close_stream(self) -> None:
        """Internal helper to close and clean up stream."""
        if self._stream is not None:
            try:
                if self._stream.active:
                    self._stream.stop()
                self._stream.close()
                logger.info("Microphone stream closed.")
            except Exception as e:
                logger.error("Error closing microphone stream: %s", e, exc_info=True)
            finally:
                self._stream = None
                self._callback = None
