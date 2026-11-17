"""
The moving sound wave shown in the pop-up while Utter listens (command, dictation, notes).

A few thin bars in a small pill, centred on a line, like an audio waveform. Each frame takes the live
microphone level, adds a little natural variation, and pushes it in from the right, so the
wave scrolls left while you speak; silence leaves a row of tiny dots. The bars get shorter
towards both ends. Plain geometry here (testable); interface/overlay.py draws it.
"""

from collections import deque
import math
import random
from typing import List, Optional, Sequence, Tuple

# A compact pill (in the style of Wispr Flow's), about 94 x 26 points
BAR_COUNT = 16
BAR_W = 2.0
BAR_GAP = 2.0
PAD_X = 16.0
HEIGHT = 26.0  # the pop-up while listening, in points
MAX_BAR = 14.0
MIN_BAR = 2.0  # silence: tiny dots, so it still reads as "listening"
WIDTH = PAD_X * 2 + BAR_COUNT * BAR_W + (BAR_COUNT - 1) * BAR_GAP
FRAMES_PER_SEC = 16.0  # 16 bars at 16 frames a second: the last second of your voice

Rect = Tuple[float, float, float, float]  # x, y, width, height (points, origin bottom-left)


def taper(i: int, n: int = BAR_COUNT) -> float:
    """Full height in the middle, about a third at both ends."""
    return 0.32 + 0.68 * math.sin(math.pi * (i + 0.5) / n)


class WaveState:
    """The scrolling history of loudness behind the bars."""

    def __init__(self, rng: Optional[random.Random] = None):
        self._rng = rng or random.Random()
        self.history = deque([0.0] * BAR_COUNT, maxlen=BAR_COUNT)
        self.smoothed = 0.0

    def reset(self) -> None:
        self.history.extend([0.0] * BAR_COUNT)
        self.smoothed = 0.0

    def step(self, level: float) -> List[float]:
        """Add one frame at the given microphone level (0..1); returns the bar heights (0..1)."""
        level = min(1.0, max(0.0, float(level)))
        self.smoothed = max(level, self.smoothed * 0.8)  # rises at once, falls back between words
        self.history.append(self.smoothed * (0.3 + 0.7 * self._rng.random()))
        return self.heights()

    def heights(self) -> List[float]:
        return [h * taper(i) for i, h in enumerate(self.history)]


def bar_rects(heights: Sequence[float], width: float = WIDTH, height: float = HEIGHT) -> List[Rect]:
    total = len(heights) * BAR_W + (len(heights) - 1) * BAR_GAP
    x0 = (width - total) / 2.0
    rects = []
    for i, h in enumerate(heights):
        bar = max(MIN_BAR, min(1.0, max(0.0, h)) * MAX_BAR)
        rects.append((x0 + i * (BAR_W + BAR_GAP), (height - bar) / 2.0, BAR_W, bar))
    return rects
