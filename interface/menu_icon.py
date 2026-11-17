"""
Utter's menu bar icon: drawn in code, icon only (no text), adapts to light and dark menu bars.

  ready      a still five-bar waveform (the Utter mark)
  command    a pulsing orb while Utter listens for a command
  dictation  five bars that follow the live microphone level (also used for notes)
  loading /  the still waveform, dimmed
  stopped

There is no "transcribing" icon: when you stop speaking the icon goes straight back to ready.

The shapes are plain geometry (testable without AppKit); render_icon() turns them into a
template NSImage, the kind macOS tints white or black to match the menu bar.
"""

import math
import time
from typing import Callable, List, Optional, Sequence, Tuple

READY = "ready"
COMMAND = "command"
DICTATION = "dictation"
LOADING = "loading"
STOPPED = "stopped"
STATES = (READY, COMMAND, DICTATION, LOADING, STOPPED)
ANIMATED = (COMMAND, DICTATION)

# Canvas in points. Every state uses the same size, so icons to the left never shift.
ICON_W = 18.0
ICON_H = 16.0
BAR_COUNT = 5
BAR_W = 2.2
BAR_GAP = 1.6
MIN_BAR = 0.16  # silent dictation: a row of short dashes, so it still reads as "listening"
DIM_ALPHA = 0.35
FRAMES_PER_SEC = 15.0

# The Utter mark: a symmetric waveform, tallest in the middle
READY_BARS: Tuple[float, ...] = (0.34, 0.68, 1.0, 0.68, 0.34)
# How strongly each dictation bar reacts to the voice (centre bars move most)
_VOICE_PROFILE = (0.55, 0.85, 1.0, 0.8, 0.5)
_WOBBLE_HZ = (2.3, 3.1, 2.7, 3.5, 2.1)
_WOBBLE_PHASE = (0.0, 1.7, 0.6, 2.9, 4.1)
ORB_PULSE_SEC = 1.3

Rect = Tuple[float, float, float, float]  # x, y, width, height (points, origin bottom-left)


# ---- Shapes -------------------------------------------------------------------------------

def bar_rects(heights: Sequence[float]) -> List[Rect]:
    """Rounded bars centred vertically; heights are 0..1 of the canvas height."""
    total = len(heights) * BAR_W + (len(heights) - 1) * BAR_GAP
    x0 = (ICON_W - total) / 2.0
    rects = []
    for i, h in enumerate(heights):
        h = max(BAR_W, min(1.0, max(0.0, h)) * ICON_H)  # never thinner than a dot
        rects.append((x0 + i * (BAR_W + BAR_GAP), (ICON_H - h) / 2.0, BAR_W, h))
    return rects


def orb_rect(scale: float) -> Rect:
    """A circle centred on the canvas; scale 1.0 fills the height."""
    d = max(2.0, min(1.0, max(0.0, scale)) * ICON_H * 0.85)
    return ((ICON_W - d) / 2.0, (ICON_H - d) / 2.0, d, d)


def dictation_heights(level: float, t: float) -> Tuple[float, ...]:
    """Bar heights for a voice level 0..1 at time t (seconds): quiet = dashes, loud = tall."""
    level = min(1.0, max(0.0, level))
    out = []
    for i in range(BAR_COUNT):
        wobble = 0.7 + 0.3 * math.sin(2 * math.pi * _WOBBLE_HZ[i] * t + _WOBBLE_PHASE[i])
        out.append(MIN_BAR + (1.0 - MIN_BAR) * level * _VOICE_PROFILE[i] * wobble)
    return tuple(out)


def orb_scale(level: float, t: float) -> float:
    """A slow breathing pulse, a little larger while you speak."""
    pulse = 0.5 + 0.5 * math.sin(2 * math.pi * t / ORB_PULSE_SEC)
    return min(1.0, 0.62 + 0.22 * pulse + 0.16 * min(1.0, max(0.0, level)))


def level_from_rms_dbfs(rms_dbfs: float) -> float:
    """Microphone loudness to 0..1: room noise (about -55 dBFS) is 0, normal speech near 1."""
    return min(1.0, max(0.0, (rms_dbfs + 55.0) / 30.0))


# ---- Frames -------------------------------------------------------------------------------

Frame = Tuple[str, Tuple[float, ...], float]  # ("bars" | "orb", sizes, alpha)


def frame_for(state: str, level: float = 0.0, t: float = 0.0) -> Frame:
    if state == COMMAND:
        return ("orb", (round(orb_scale(level, t), 3),), 1.0)
    if state == DICTATION:
        return ("bars", tuple(round(h, 3) for h in dictation_heights(level, t)), 1.0)
    alpha = DIM_ALPHA if state in (LOADING, STOPPED) else 1.0
    return ("bars", READY_BARS, alpha)


def render_icon(frame: Frame, scale: int = 2):
    """Draw a frame into a template NSImage (black shapes; macOS recolours them)."""
    import AppKit

    kind, sizes, alpha = frame
    width, height = int(ICON_W * scale), int(ICON_H * scale)
    rep = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, width, height, 8, 4, True, False, AppKit.NSDeviceRGBColorSpace, 0, 0
    )
    rep.setSize_((ICON_W, ICON_H))
    context = AppKit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    AppKit.NSGraphicsContext.saveGraphicsState()
    try:
        AppKit.NSGraphicsContext.setCurrentContext_(context)
        AppKit.NSColor.colorWithWhite_alpha_(0.0, alpha).setFill()
        if kind == "orb":
            x, y, w, h = orb_rect(sizes[0])
            AppKit.NSBezierPath.bezierPathWithOvalInRect_(((x, y), (w, h))).fill()
        else:
            for x, y, w, h in bar_rects(sizes):
                AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(((x, y), (w, h)), w / 2.0, w / 2.0).fill()
        context.flushGraphics()
    finally:
        AppKit.NSGraphicsContext.restoreGraphicsState()
    image = AppKit.NSImage.alloc().initWithSize_((ICON_W, ICON_H))
    image.addRepresentation_(rep)
    image.setTemplate_(True)
    image.setAccessibilityDescription_("Utter")
    return image


# ---- Controller ---------------------------------------------------------------------------

class MenuBarIcon:
    """
    Keeps the menu bar icon in step with the app's state. Animated states redraw about
    15 times a second from a main-thread timer that runs only while Utter is listening.
    Call set_state() on the main thread.
    """

    def __init__(
        self,
        show: Callable[[object], None],
        level: Callable[[], float] = lambda: 0.0,
        render: Callable[[Frame], object] = render_icon,
        clock: Callable[[], float] = time.monotonic,
        make_timer: Optional[Callable[[Callable, float], object]] = None,
    ):
        self._show = show
        self._level = level
        self._render = render
        self._clock = clock
        self._make_timer = make_timer
        self._timer = None
        self._state = LOADING
        self._started_at = clock()
        self._smoothed = 0.0
        self._last_frame: Optional[Frame] = None
        self._static_cache = {}

    @property
    def state(self) -> str:
        return self._state

    @property
    def animating(self) -> bool:
        return self._timer is not None

    def set_state(self, state: str) -> None:
        if state not in STATES:
            raise ValueError(f"unknown menu bar state: {state}")
        if state != self._state:
            self._state = state
            self._started_at = self._clock()
            self._smoothed = 0.0
        if state in ANIMATED:
            self._start_timer()
        else:
            self._stop_timer()
        self.tick()

    def tick(self, *_args) -> None:
        """Draw the current frame (called by the timer while listening)."""
        if self._state in ANIMATED:
            try:
                target = float(self._level())
            except Exception:
                target = 0.0
            # Rise at once with the voice, fall back gently between words
            self._smoothed = max(target, self._smoothed * 0.8)
            frame = frame_for(self._state, self._smoothed, self._clock() - self._started_at)
        else:
            frame = frame_for(self._state)
        if frame == self._last_frame:
            return
        self._last_frame = frame
        if self._state in ANIMATED:
            image = self._render(frame)
        else:
            image = self._static_cache.get(frame)
            if image is None:
                image = self._static_cache[frame] = self._render(frame)
        self._show(image)

    def stop(self) -> None:
        self._stop_timer()

    def _start_timer(self) -> None:
        if self._timer is None and self._make_timer is not None:
            self._timer = self._make_timer(self.tick, 1.0 / FRAMES_PER_SEC)
            self._timer.start()

    def _stop_timer(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
