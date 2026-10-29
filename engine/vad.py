"""Voice Activity Detection (VAD) module using Silero VAD."""

import logging
import threading
from typing import Tuple
import numpy as np
import silero_vad
import torch

logger = logging.getLogger(__name__)


class SileroVAD:
    """Wrapper around Silero VAD for frame-by-frame speech detection."""

    def __init__(self, sample_rate: int = 16000, threshold: float = 0.5):
        self.sample_rate = sample_rate
        self.threshold = threshold
        self._lock = threading.Lock()
        logger.info("Loading Silero VAD model...")
        self.model = silero_vad.load_silero_vad()
        self.reset()
        logger.info("Silero VAD model initialized successfully.")

    def reset(self) -> None:
        """Reset internal recurrent states of the VAD model."""
        with self._lock:
            if hasattr(self.model, "reset_states"):
                self.model.reset_states()

    def is_speech(self, chunk: np.ndarray) -> Tuple[bool, float]:
        """
        Evaluate a single chunk of audio samples.

        Args:
            chunk: 1D numpy array of float32 samples normalized to [-1.0, 1.0].
                   Expected length: 512 samples at 16000 Hz.

        Returns:
            Tuple of (is_speech_bool, speech_probability_float).
        """
        if chunk.ndim > 1:
            chunk = chunk.squeeze()

        if len(chunk) != 512:
            raise ValueError(f"Expected 512 audio samples for VAD, got {len(chunk)}")

        # Ensure float32 dtype
        if chunk.dtype != np.float32:
            chunk = chunk.astype(np.float32)

        tensor_chunk = torch.from_numpy(chunk)

        with self._lock, torch.no_grad():
            prob = float(self.model(tensor_chunk, self.sample_rate).item())

        return (prob >= self.threshold, prob)
