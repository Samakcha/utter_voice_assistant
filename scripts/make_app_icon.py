#!/usr/bin/env python3
"""
Draw Utter's app icon (assets/utter-app-icon.png, 1024 x 1024) in code.

The same five-bar waveform as the menu bar icon (interface/menu_icon.py), white on a deep
indigo rounded square sized to Apple's macOS icon grid. Re-run after changing the design:

  uv run python scripts/make_app_icon.py
"""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from interface.menu_icon import BAR_GAP, BAR_W, ICON_H, READY_BARS  # noqa: E402

OUTPUT = PROJECT_ROOT / "assets" / "utter-app-icon.png"
SIZE = 1024
TILE = 824  # Apple's macOS icon grid: an 824-point rounded square centred in 1024
CORNER = 185
BACKGROUND = (0.17, 0.15, 0.40)  # deep indigo
BAR_COLOR = (1.0, 1.0, 1.0)
WAVE_HEIGHT = 470  # tallest bar, in pixels


def draw_app_icon(path: Path = OUTPUT, size: int = SIZE) -> Path:
    import AppKit

    rep = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, size, size, 8, 4, True, False, AppKit.NSDeviceRGBColorSpace, 0, 0
    )
    k = size / SIZE
    context = AppKit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    AppKit.NSGraphicsContext.saveGraphicsState()
    try:
        AppKit.NSGraphicsContext.setCurrentContext_(context)
        inset = (SIZE - TILE) / 2 * k
        AppKit.NSColor.colorWithDeviceRed_green_blue_alpha_(*BACKGROUND, 1.0).setFill()
        AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            ((inset, inset), (TILE * k, TILE * k)), CORNER * k, CORNER * k
        ).fill()

        unit = WAVE_HEIGHT * k / ICON_H  # menu-bar points to pixels
        bar_w, gap = BAR_W * unit, BAR_GAP * unit
        total = len(READY_BARS) * bar_w + (len(READY_BARS) - 1) * gap
        x0 = (size - total) / 2
        AppKit.NSColor.colorWithDeviceRed_green_blue_alpha_(*BAR_COLOR, 1.0).setFill()
        for i, h in enumerate(READY_BARS):
            bar_h = h * WAVE_HEIGHT * k
            rect = ((x0 + i * (bar_w + gap), (size - bar_h) / 2), (bar_w, bar_h))
            AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, bar_w / 2, bar_w / 2).fill()
        context.flushGraphics()
    finally:
        AppKit.NSGraphicsContext.restoreGraphicsState()
    path.parent.mkdir(parents=True, exist_ok=True)
    png = rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, None)
    if not png.writeToFile_atomically_(str(path), True):
        raise OSError(f"could not write {path}")
    return path


if __name__ == "__main__":
    print(f"Wrote {draw_app_icon()}")
