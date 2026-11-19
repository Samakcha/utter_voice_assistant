"""
Voice activity detection (VAD): is there speech in this 32 ms of audio?

This is the Silero VAD network (MIT licence, assets/silero_vad_LICENSE.txt), run with numpy.
Utter used to run it with PyTorch, which made up about 700 MB of the app. The network is
small: a fixed spectrum step, four 1-D convolutions, one LSTM cell and a final layer. Its
weights are the 1.2 MB file Silero publishes (assets/silero_vad_16k.safetensors). On every
accuracy recording it gives the same speech / no-speech decisions as the PyTorch model
(tests/test_vad.py and PRD 10.2).
"""

import json
import logging
from pathlib import Path
import struct
import threading
from typing import Dict, Tuple

import numpy as np

logger = logging.getLogger(__name__)

WEIGHTS_PATH = Path(__file__).resolve().parent.parent / "assets" / "silero_vad_16k.safetensors"

CHUNK_SAMPLES = 512  # 32 ms at 16 kHz
CONTEXT_SAMPLES = 64  # the end of the previous chunk, put in front of each new one
_REFLECT_PAD = 64
_CUTOFF = 129  # 256-point spectrum: 129 frequency bins

_DTYPES = {"F32": np.float32, "F16": np.float16, "F64": np.float64}


def load_safetensors(path: Path) -> Dict[str, np.ndarray]:
    """Read a .safetensors file (an 8-byte header size, a JSON header, then raw arrays)."""
    data = Path(path).read_bytes()
    (header_size,) = struct.unpack("<Q", data[:8])
    header = json.loads(data[8:8 + header_size])
    body = data[8 + header_size:]
    arrays = {}
    for name, info in header.items():
        if name == "__metadata__":
            continue
        start, end = info["data_offsets"]
        dtype = np.dtype(_DTYPES[info["dtype"]]).newbyteorder("<")
        arrays[name] = np.frombuffer(body[start:end], dtype=dtype).reshape(info["shape"]).astype(np.float32)
    return arrays


def _conv1d(x: np.ndarray, weight: np.ndarray, bias, stride: int = 1, padding: int = 0) -> np.ndarray:
    """1-D convolution like torch.nn.Conv1d. x: (in_channels, time); weight: (out, in, kernel)."""
    if padding:
        x = np.pad(x, ((0, 0), (padding, padding)))
    out_channels, in_channels, kernel = weight.shape
    steps = (x.shape[1] - kernel) // stride + 1
    windows = np.lib.stride_tricks.sliding_window_view(x, kernel, axis=1)[:, ::stride][:, :steps]
    # windows: (in, steps, kernel) -> (steps, in * kernel)
    columns = windows.transpose(1, 0, 2).reshape(steps, in_channels * kernel)
    out = weight.reshape(out_channels, in_channels * kernel) @ columns.T
    if bias is not None:
        out += bias[:, None]
    return out


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


class SileroVAD:
    """Speech probability for each 512-sample chunk; remembers the previous chunks."""

    def __init__(self, sample_rate: int = 16000, threshold: float = 0.5, weights_path: Path = WEIGHTS_PATH):
        if sample_rate != 16000:
            raise ValueError(f"The voice detector needs 16000 Hz audio, got {sample_rate}")
        self.sample_rate = sample_rate
        self.threshold = threshold
        self._lock = threading.Lock()
        logger.info("Loading Silero VAD model...")
        w = load_safetensors(weights_path)
        self._stft = w["stft_conv.weight"]
        self._convs = [
            (w["conv1.weight"], w["conv1.bias"], 1),
            (w["conv2.weight"], w["conv2.bias"], 2),
            (w["conv3.weight"], w["conv3.bias"], 2),
            (w["conv4.weight"], w["conv4.bias"], 1),
        ]
        self._w_ih, self._w_hh = w["lstm_cell.weight_ih"], w["lstm_cell.weight_hh"]
        self._b_lstm = w["lstm_cell.bias_ih"] + w["lstm_cell.bias_hh"]
        self._final_w = w["final_conv.weight"][:, :, 0]
        self._final_b = w["final_conv.bias"]
        self.reset()
        logger.info("Silero VAD model initialized successfully.")

    def reset(self) -> None:
        """Forget earlier audio (at the start of every session)."""
        with self._lock:
            self._h = np.zeros(128, dtype=np.float32)
            self._c = np.zeros(128, dtype=np.float32)
            self._context = np.zeros(CONTEXT_SAMPLES, dtype=np.float32)

    def _probability(self, chunk: np.ndarray) -> float:
        x = np.concatenate([self._context, chunk])
        self._context = x[-CONTEXT_SAMPLES:].copy()
        x = np.pad(x, (0, _REFLECT_PAD), mode="reflect")[None, :]
        spectrum = _conv1d(x, self._stft, None, stride=128)
        x = np.sqrt(spectrum[:_CUTOFF] ** 2 + spectrum[_CUTOFF:] ** 2)
        for weight, bias, stride in self._convs:
            x = np.maximum(_conv1d(x, weight, bias, stride=stride, padding=1), 0.0)
        features = x[:, 0]
        gates = self._w_ih @ features + self._w_hh @ self._h + self._b_lstm
        i, f, g, o = np.split(gates, 4)  # PyTorch's LSTM gate order
        self._c = _sigmoid(f) * self._c + _sigmoid(i) * np.tanh(g)
        self._h = _sigmoid(o) * np.tanh(self._c)
        out = self._final_w @ np.maximum(self._h, 0.0) + self._final_b
        return float(_sigmoid(out)[0])

    def is_speech(self, chunk: np.ndarray) -> Tuple[bool, float]:
        """
        Args:
            chunk: 512 float32 samples in [-1.0, 1.0] at 16 kHz.

        Returns:
            (is_speech, speech_probability).
        """
        if chunk.ndim > 1:
            chunk = chunk.squeeze()
        if len(chunk) != CHUNK_SAMPLES:
            raise ValueError(f"Expected {CHUNK_SAMPLES} audio samples for VAD, got {len(chunk)}")
        chunk = np.asarray(chunk, dtype=np.float32)
        with self._lock:
            prob = self._probability(chunk)
        return (prob >= self.threshold, prob)
