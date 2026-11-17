"""
The moving sound wave shown in the pop-up while Utter listens (command, dictation, notes).

A few bold bars in a small pill, centred on a line, like an audio waveform. Each frame takes
the live microphone level, makes quiet speech count more, adds a strong natural variation, and
pushes it in from the right, so the wave scrolls left while you speak. Every bar also flickers
a little each frame, and even in silence the bars twitch slightly, so the wave always looks
alive. The bars get shorter towards both ends. Plain geometry here (testable);
interface/overlay.py draws it.
"""

from collections import deque
import math
import random
from typing import List, Optional, Sequence, Tuple

# A compact pill (in the style of Wispr Flow's), about 96 x 26 points
BAR_COUNT = 14
BAR_W = 3.0  # bold bars
BAR_GAP = 2.0
PAD_X = 14.0
HEIGHT = 26.0  # the pop-up while listening, in points
MAX_BAR = 18.0
MIN_BAR = 3.0  # silence: round dots, as wide as the bars
WIDTH = PAD_X * 2 + BAR_COUNT * BAR_W + (BAR_COUNT - 1) * BAR_GAP
FRAMES_PER_SEC = 24.0

# Movement: how lively the wave is
LEVEL_CURVE = 0.5  # < 1: quiet speech still makes big bars
DECAY = 0.55  # how much of the last frame's loudness carries over (lower: snappier)
VARIATION = 0.6  # how different neighbouring bars are (0: all equal, 1: fully random)
FLICKER = 0.35  # every bar wobbles by up to this much each frame while you speak
IDLE = 0.3  # in silence, bars still twitch up to this height (0..1)

Rect = Tuple[float, float, float, float]  # x, y, width, height (points, origin bottom-left)


def taper(i: int, n: int = BAR_COUNT) -> float:
    """Full height in the middle, about a third at both ends."""
    return 0.35 + 0.65 * math.sin(math.pi * (i + 0.5) / n)


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
        level = min(1.0, max(0.0, float(level))) ** LEVEL_CURVE
        self.smoothed = max(level, self.smoothed * DECAY)  # jumps up at once, drops fast
        self.history.append(self.smoothed * ((1.0 - VARIATION) + VARIATION * self._rng.random()))
        return self.heights()

    def heights(self) -> List[float]:
        out = []
        for i, h in enumerate(self.history):
            flicker = (1.0 - FLICKER) + FLICKER * self._rng.random()
            idle = IDLE * self._rng.random()
            out.append(min(1.0, max(h * flicker, idle)) * taper(i))
        return out


def bar_rects(heights: Sequence[float], width: float = WIDTH, height: float = HEIGHT) -> List[Rect]:
    total = len(heights) * BAR_W + (len(heights) - 1) * BAR_GAP
    x0 = (width - total) / 2.0
    rects = []
    for i, h in enumerate(heights):
        bar = max(MIN_BAR, min(1.0, max(0.0, h)) * MAX_BAR)
        rects.append((x0 + i * (BAR_W + BAR_GAP), (height - bar) / 2.0, BAR_W, bar))
    return rects
