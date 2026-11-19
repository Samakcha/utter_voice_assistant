"""
The welcome window's look: always dark, purple, with handwritten touches (the Caveat font,
SIL Open Font Licence, assets/fonts). Only drawing lives here; interface/welcome_window.py
places the pieces and interface/setup_flow.py decides what each step does.

Everything is drawn by small NSView / NSButton subclasses, in the window's own coordinates
(y grows upwards, like the rest of AppKit).
"""

import logging
import math
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import AppKit
import objc

logger = logging.getLogger(__name__)

FONT_PATH = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "Caveat.ttf"
HAND_FAMILY = "Caveat"
_WEIGHT_AXIS = int.from_bytes(b"wght", "big")


def rgb(r: int, g: int, b: int, a: float = 1.0):
    return AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(r / 255, g / 255, b / 255, a)


BACKGROUND = rgb(21, 20, 27)
TEXT = rgb(242, 241, 247)
TEXT_DIM = rgb(170, 168, 184)
TEXT_FAINT = rgb(118, 116, 132)
CARD = rgb(255, 255, 255, 0.035)
CARD_EDGE = rgb(255, 255, 255, 0.085)
KEYCAP = rgb(47, 46, 56)
PURPLE = rgb(124, 92, 255)
PURPLE_LIGHT = rgb(146, 118, 255)
PURPLE_DEEP = rgb(98, 66, 232)
LAVENDER = rgb(168, 146, 255)
GREEN = rgb(74, 222, 128)
ORANGE = rgb(255, 176, 92)
RED = rgb(255, 118, 118)


# ---- Fonts --------------------------------------------------------------------------------

_registered: Optional[bool] = None


def register_fonts() -> bool:
    """Make Caveat usable in this process (once). False if the file can't be loaded."""
    global _registered
    if _registered is None:
        _registered = False
        try:
            import CoreText
            url = AppKit.NSURL.fileURLWithPath_(str(FONT_PATH))
            ok, _error = CoreText.CTFontManagerRegisterFontsForURL(url, CoreText.kCTFontManagerScopeProcess, None)
            # "already registered" is fine too
            _registered = bool(ok) or AppKit.NSFont.fontWithName_size_(HAND_FAMILY, 12) is not None
        except Exception as e:
            logger.debug("Could not load the Caveat font: %s", e)
    return _registered


def hand_font(size: float, weight: int = 700):
    """Caveat at this weight (400 to 700), or the system's rounded font if it is missing."""
    font = AppKit.NSFont.fontWithName_size_(HAND_FAMILY, size) if register_fonts() else None
    if font is None:
        return AppKit.NSFont.systemFontOfSize_weight_(size * 0.8, AppKit.NSFontWeightSemibold)
    descriptor = font.fontDescriptor().fontDescriptorByAddingAttributes_(
        {AppKit.NSFontVariationAttribute: {_WEIGHT_AXIS: weight}})
    return AppKit.NSFont.fontWithDescriptor_size_(descriptor, size) or font


def ui_font(size: float, weight=None):
    return AppKit.NSFont.systemFontOfSize_weight_(size, AppKit.NSFontWeightRegular if weight is None else weight)


def attributed(text: str, font, color, align=None):
    attrs = {AppKit.NSFontAttributeName: font, AppKit.NSForegroundColorAttributeName: color}
    if align is not None:
        style = AppKit.NSMutableParagraphStyle.alloc().init()
        style.setAlignment_(align)
        attrs[AppKit.NSParagraphStyleAttributeName] = style
    return AppKit.NSAttributedString.alloc().initWithString_attributes_(text, attrs)


def text_label(text: str, frame, font, color=TEXT, align=AppKit.NSTextAlignmentLeft, selectable: bool = False):
    field = AppKit.NSTextField.wrappingLabelWithString_(text)
    field.setFrame_(frame)
    field.setFont_(font)
    field.setTextColor_(color)
    field.setAlignment_(align)
    field.setSelectable_(selectable)
    field.setDrawsBackground_(False)
    return field


# ---- Strokes that look drawn by hand ----------------------------------------------------------

def _pen(width: float = 2.6):
    path = AppKit.NSBezierPath.bezierPath()
    path.setLineWidth_(width)
    path.setLineCapStyle_(AppKit.NSLineCapStyleRound)
    path.setLineJoinStyle_(AppKit.NSLineJoinStyleRound)
    return path


def _polyline(points: Sequence[Tuple[float, float]], width: float = 2.6):
    path = _pen(width)
    path.moveToPoint_(points[0])
    for point in points[1:]:
        path.lineToPoint_(point)
    return path


def wobbly_ellipse(cx: float, cy: float, rx: float, ry: float, tilt_deg: float = -4.0, width: float = 2.8):
    """An ellipse drawn in one go that overshoots its start, as a pen would."""
    tilt = math.radians(tilt_deg)
    points = []
    start = math.radians(200)
    for i in range(0, 400):
        t = start + i * math.radians(1.0)
        wobble = 1 + 0.035 * math.sin(3 * t + 0.6) + 0.02 * math.sin(5 * t)
        drift = (t - start) / (2 * math.pi)
        x = rx * wobble * math.cos(t) + 4 * drift
        y = ry * wobble * math.sin(t) - 3 * drift
        points.append((cx + x * math.cos(tilt) - y * math.sin(tilt), cy + x * math.sin(tilt) + y * math.cos(tilt)))
    return _polyline(points, width)


def swash(x0: float, x1: float, y: float, lift: float = 3.0, width: float = 2.6):
    """A slightly curved underline."""
    points = []
    for i in range(41):
        f = i / 40
        points.append((x0 + (x1 - x0) * f, y + lift * math.sin(math.pi * f) - 1.5 * f))
    return _polyline(points, width)


def burst(x: float, y: float, size: float = 18.0, width: float = 2.6):
    """Three short strokes fanning out from (x, y), up and to the right."""
    paths = []
    for angle, length in ((80, 0.8), (45, 1.0), (12, 0.75)):
        a = math.radians(angle)
        start = (x + 6 * math.cos(a), y + 6 * math.sin(a))
        end = (x + (6 + size * length) * math.cos(a), y + (6 + size * length) * math.sin(a))
        paths.append(_polyline([start, end], width))
    return paths


def curved_arrow(start, control, end, width: float = 2.4, head: float = 9.0):
    path = _pen(width)
    path.moveToPoint_(start)
    path.curveToPoint_controlPoint1_controlPoint2_(end, control, control)
    angle = math.atan2(end[1] - control[1], end[0] - control[0])
    for side in (-1, 1):
        a = angle + math.pi + side * math.radians(28)
        path.moveToPoint_(end)
        path.lineToPoint_((end[0] + head * math.cos(a), end[1] + head * math.sin(a)))
    return path


# ---- Views ------------------------------------------------------------------------------

class Backdrop(AppKit.NSView):
    """The dark window background with purple hills glowing along the bottom."""

    def drawRect_(self, rect):
        bounds = self.bounds()
        w = bounds.size.width
        BACKGROUND.setFill()
        AppKit.NSRectFill(bounds)
        # three soft hills: (left foot, peak x, peak height, right foot), fill strength
        for left, peak_x, peak_h, right, alpha in ((-0.08, 0.17, 44, 0.52, 0.20),
                                                   (0.26, 0.58, 80, 0.98, 0.30),
                                                   (0.70, 0.90, 36, 1.14, 0.18)):
            x0, px, x1 = left * w, peak_x * w, right * w
            path = AppKit.NSBezierPath.bezierPath()
            path.moveToPoint_((x0, 0))
            path.curveToPoint_controlPoint1_controlPoint2_((px, peak_h), (x0 + (px - x0) * 0.55, 0),
                                                           (px - (px - x0) * 0.45, peak_h))
            path.curveToPoint_controlPoint1_controlPoint2_((x1, 0), (px + (x1 - px) * 0.45, peak_h),
                                                           (x1 - (x1 - px) * 0.55, 0))
            path.closePath()
            gradient = AppKit.NSGradient.alloc().initWithStartingColor_endingColor_(
                rgb(118, 88, 255, alpha), rgb(70, 40, 170, alpha * 0.25))
            gradient.drawInBezierPath_angle_(path, -90)
            rgb(170, 146, 255, alpha * 1.1).setStroke()
            edge = AppKit.NSBezierPath.bezierPath()
            edge.moveToPoint_((x0, 0))
            edge.curveToPoint_controlPoint1_controlPoint2_((px, peak_h), (x0 + (px - x0) * 0.55, 0),
                                                           (px - (px - x0) * 0.45, peak_h))
            edge.curveToPoint_controlPoint1_controlPoint2_((x1, 0), (px + (x1 - px) * 0.45, peak_h),
                                                           (x1 - (x1 - px) * 0.55, 0))
            edge.setLineWidth_(1.2)
            edge.stroke()

    def isOpaque(self):
        return True


class Card(AppKit.NSView):
    """A rounded, faintly lit panel."""

    def initWithFrame_(self, frame):
        self = objc.super(Card, self).initWithFrame_(frame)
        if self is None:
            return None
        self.radius = 14.0
        self.fill = CARD
        self.edge = CARD_EDGE
        return self

    def drawRect_(self, rect):
        r = self.radius
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            AppKit.NSInsetRect(self.bounds(), 0.5, 0.5), r, r)
        self.fill.setFill()
        path.fill()
        self.edge.setStroke()
        path.setLineWidth_(1.0)
        path.stroke()


def divider(frame):
    view = AppKit.NSView.alloc().initWithFrame_(frame)
    view.setWantsLayer_(True)
    view.layer().setBackgroundColor_(rgb(255, 255, 255, 0.14).CGColor())
    return view


def card(frame, radius: float = 14.0, fill=None, edge=None):
    view = Card.alloc().initWithFrame_(frame)
    view.radius = radius
    if fill is not None:
        view.fill = fill
    if edge is not None:
        view.edge = edge
    return view


class Keycap(AppKit.NSView):
    def drawRect_(self, rect):
        bounds = self.bounds()
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bounds, 8, 8)
        KEYCAP.setFill()
        path.fill()
        text = attributed(self.text, ui_font(17, AppKit.NSFontWeightSemibold), TEXT)
        size = text.size()
        text.drawAtPoint_(((bounds.size.width - size.width) / 2, (bounds.size.height - size.height) / 2))


def keycap(text: str, frame):
    view = Keycap.alloc().initWithFrame_(frame)
    view.text = text
    return view


class Badge(AppKit.NSView):
    """The round mark in front of a permission: its number, or ✓ once it is on."""

    def drawRect_(self, rect):
        path = AppKit.NSBezierPath.bezierPathWithOvalInRect_(AppKit.NSInsetRect(self.bounds(), 0.5, 0.5))
        self.fill.setFill()
        path.fill()


def badge(frame):
    view = Badge.alloc().initWithFrame_(frame)
    view.fill = KEYCAP
    return view


class PillButton(AppKit.NSButton):
    """A rounded button: purple ("primary", with an arrow) or quiet ("ghost")."""

    def drawRect_(self, rect):
        bounds = self.bounds()
        h = bounds.size.height
        enabled = self.isEnabled()
        pressed = self.isHighlighted()
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            AppKit.NSInsetRect(bounds, 0.5, 0.5), h / 2 - 0.5, h / 2 - 0.5)
        if self.style == "key":  # a shortcut: a key cap; purple while it waits for new keys
            listening = self.title() == "Press keys…"
            cap = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                AppKit.NSInsetRect(bounds, 0.5, 0.5), 9, 9)
            (rgb(124, 92, 255, 0.22) if listening else (rgb(60, 59, 70) if pressed else KEYCAP)).setFill()
            cap.fill()
            (PURPLE_LIGHT if listening else rgb(255, 255, 255, 0.10)).setStroke()
            cap.setLineWidth_(1.5 if listening else 1.0)
            cap.stroke()
            text = attributed(self.title(), ui_font(14 if listening else 16, AppKit.NSFontWeightSemibold),
                              LAVENDER if listening else TEXT)
            size = text.size()
            text.drawAtPoint_(((bounds.size.width - size.width) / 2, (h - size.height) / 2))
            return
        if self.style == "primary":
            alpha = 1.0 if enabled else 0.38
            top, bottom = (PURPLE_DEEP, PURPLE_DEEP) if pressed else (PURPLE_LIGHT, PURPLE)
            gradient = AppKit.NSGradient.alloc().initWithStartingColor_endingColor_(
                top.colorWithAlphaComponent_(alpha), bottom.colorWithAlphaComponent_(alpha))
            gradient.drawInBezierPath_angle_(path, -90)
            color = TEXT if enabled else TEXT.colorWithAlphaComponent_(0.55)
            title = self.title() + ("  →" if self.arrow else "")
            font = ui_font(15.5, AppKit.NSFontWeightSemibold)
        else:
            rgb(255, 255, 255, 0.12 if pressed else 0.06).setFill()
            path.fill()
            rgb(255, 255, 255, 0.16 if enabled else 0.07).setStroke()
            path.setLineWidth_(1.0)
            path.stroke()
            color = TEXT if enabled else TEXT_FAINT
            title = self.title()
            font = ui_font(13.5, AppKit.NSFontWeightMedium)
        text = attributed(title, font, color)
        size = text.size()
        text.drawAtPoint_(((bounds.size.width - size.width) / 2, (h - size.height) / 2))

    def isFlipped(self):
        return False


def pill_button(title: str, frame, target, style: str = "primary", arrow: bool = False):
    button = PillButton.alloc().initWithFrame_(frame)
    button.style = style
    button.arrow = arrow
    button.setBordered_(False)
    button.setTitle_(title)
    button.setTarget_(target)
    button.setAction_("fire:")
    return button


class HandTitle(AppKit.NSView):
    """
    A handwritten title, centred. `pieces` are (text, mark) with mark None, "circle" (a pen
    circle around it) or "underline". stringValue / setStringValue_ like a label.
    """

    def drawRect_(self, rect):
        bounds = self.bounds()
        font = hand_font(self.size, 700)
        pieces = [(text.strip(), mark) for text, mark in self.pieces if text.strip()]
        texts = [attributed(text, font, TEXT) for text, _ in pieces]
        widths = [t.size().width for t in texts]
        height = max([t.size().height for t in texts] or [0])
        gap = attributed(" ", font, TEXT).size().width  # a word space between pieces
        pad = self.size * 0.16  # and room for the pen circle on each side of a circled word
        pads = [pad if mark == "circle" else 0.0 for _, mark in pieces]
        total = sum(widths) + 2 * sum(pads) + gap * max(0, len(pieces) - 1)
        x = 2.0 if getattr(self, "align", "center") == "left" else (bounds.size.width - total) / 2
        y = (bounds.size.height - height) / 2
        for (text, mark), drawn, width, space in zip(pieces, texts, widths, pads):
            x += space
            drawn.drawAtPoint_((x, y))
            middle = y + height * 0.47
            if mark == "circle":
                cx = x + width / 2
                rx, ry = width / 2 + self.size * 0.2, height * 0.42
                PURPLE.setStroke()
                wobbly_ellipse(cx, middle, rx, ry).stroke()
                if self.burst:
                    for stroke in burst(cx + rx * 0.62, middle + ry * 0.78, self.size * 0.26):
                        stroke.stroke()
            elif mark == "swash":
                PURPLE.setStroke()
                swash(x + 2, x + width * 0.96, y + height * 0.08, lift=2.5).stroke()
            elif mark == "underline":
                PURPLE.setStroke()
                swash(x + width * 0.50, x + width * 0.93, y + height * 0.07).stroke()
                swash(x + width * 0.40, x + width * 0.58, y + height * 0.10, lift=1.2, width=2.2).stroke()
            x += width + space + gap

    def stringValue(self):
        return " ".join(text.strip() for text, _ in self.pieces if text.strip())

    def setStringValue_(self, text):
        text = str(text)
        before, found, after = text.partition("Utter")
        self.pieces = [(before, None), ("Utter", "circle"), (after, None)] if found else [(text, None)]
        self.setNeedsDisplay_(True)


def hand_title(pieces: Sequence[Tuple[str, Optional[str]]], frame, size: float, burst_marks: bool = False,
               align: str = "center"):
    view = HandTitle.alloc().initWithFrame_(frame)
    view.pieces = list(pieces)
    view.size = size
    view.burst = burst_marks
    view.align = align
    return view


class HandNote(AppKit.NSView):
    """
    A small handwritten note in purple, slightly tilted: `lines` are (text, indent).
    Extra pen strokes (underlines, ticks, an arrow) are given in the view's coordinates.
    """

    def drawRect_(self, rect):
        transform = AppKit.NSAffineTransform.transform()
        bounds = self.bounds()
        transform.translateXBy_yBy_(bounds.size.width / 2, bounds.size.height / 2)
        transform.rotateByDegrees_(self.tilt)
        transform.translateXBy_yBy_(-bounds.size.width / 2, -bounds.size.height / 2)
        transform.concat()
        font = hand_font(self.size, 600)
        y = bounds.size.height - self.size * 1.25
        for text, indent in self.lines:
            attributed(text, font, LAVENDER).drawAtPoint_((indent, y))
            y -= self.size * 0.98
        PURPLE.setStroke()
        for stroke in self.strokes:
            stroke.stroke()


def hand_note(lines: Sequence[Tuple[str, float]], frame, size: float = 26.0, tilt: float = 6.0,
              strokes: Optional[List] = None):
    view = HandNote.alloc().initWithFrame_(frame)
    view.lines = list(lines)
    view.size = size
    view.tilt = tilt
    view.strokes = list(strokes or [])
    return view


class Strokes(AppKit.NSView):
    """Free pen strokes (an arrow between two parts of the page)."""

    def drawRect_(self, rect):
        PURPLE.setStroke()
        for stroke in self.strokes:
            stroke.stroke()


def strokes(frame, paths: List):
    view = Strokes.alloc().initWithFrame_(frame)
    view.strokes = list(paths)
    return view


class StepDots(AppKit.NSView):
    """One dot per step; the current one is purple, done ones are lighter."""

    def drawRect_(self, rect):
        diameter, gap = 10.0, 7.0
        y = (self.bounds().size.height - diameter) / 2
        for i in range(self.total):
            color = PURPLE_LIGHT if i == self.current else (rgb(255, 255, 255, 0.32) if i < self.current
                                                             else rgb(255, 255, 255, 0.14))
            color.setFill()
            AppKit.NSBezierPath.bezierPathWithOvalInRect_(
                AppKit.NSMakeRect(i * (diameter + gap), y, diameter, diameter)).fill()

    def setStep_of_(self, current: int, total: int):
        self.current, self.total = current, total
        self.setHidden_(total <= 1)
        self.setNeedsDisplay_(True)


def step_dots(frame):
    view = StepDots.alloc().initWithFrame_(frame)
    view.current, view.total = 0, 0
    return view


class IconTile(AppKit.NSView):
    """Utter's icon, drawn: a purple rounded square with the sound bars, softly glowing."""

    def drawRect_(self, rect):
        bounds = self.bounds()
        inset = 8.0
        tile = AppKit.NSInsetRect(bounds, inset, inset)
        glow = AppKit.NSShadow.alloc().init()
        glow.setShadowColor_(rgb(124, 92, 255, 0.55))
        glow.setShadowBlurRadius_(18)
        glow.setShadowOffset_((0, -2))
        AppKit.NSGraphicsContext.saveGraphicsState()
        glow.set()
        radius = tile.size.width * 0.24
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(tile, radius, radius)
        AppKit.NSGradient.alloc().initWithStartingColor_endingColor_(
            rgb(78, 62, 196), rgb(44, 38, 112)).drawInBezierPath_angle_(path, -90)
        AppKit.NSGraphicsContext.restoreGraphicsState()
        rgb(255, 255, 255, 0.10).setStroke()
        path.setLineWidth_(1.0)
        path.stroke()
        # the five bars of the app icon (assets/utter-app-icon.png), as fractions of the tile
        size = tile.size.width
        bar_w = size * 0.075
        TEXT.setFill()
        for centre, height in ((0.23, 0.19), (0.37, 0.39), (0.5, 0.57), (0.63, 0.39), (0.77, 0.19)):
            x = tile.origin.x + size * centre - bar_w / 2
            h = size * height
            y = tile.origin.y + (size - h) / 2
            AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                AppKit.NSMakeRect(x, y, bar_w, h), bar_w / 2, bar_w / 2).fill()


def icon_tile(frame):
    return IconTile.alloc().initWithFrame_(frame)


class Switch(AppKit.NSButton):
    """An on/off switch: purple when on. state() / setState_ like NSSwitch."""

    def drawRect_(self, rect):
        bounds = self.bounds()
        w, h = 40.0, 24.0
        x, y = (bounds.size.width - w) / 2, (bounds.size.height - h) / 2
        on = self.state() == AppKit.NSControlStateValueOn
        alpha = 1.0 if self.isEnabled() else 0.4
        track = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            AppKit.NSMakeRect(x, y, w, h), h / 2, h / 2)
        if on:
            AppKit.NSGradient.alloc().initWithStartingColor_endingColor_(
                PURPLE_LIGHT.colorWithAlphaComponent_(alpha), PURPLE.colorWithAlphaComponent_(alpha)
            ).drawInBezierPath_angle_(track, -90)
        else:
            rgb(255, 255, 255, 0.14 * alpha).setFill()
            track.fill()
        knob = h - 4
        kx = x + w - knob - 2 if on else x + 2
        rgb(255, 255, 255, alpha).setFill()
        AppKit.NSBezierPath.bezierPathWithOvalInRect_(AppKit.NSMakeRect(kx, y + 2, knob, knob)).fill()


def switch(frame, on: bool, target):
    view = Switch.alloc().initWithFrame_(frame)
    view.setButtonType_(AppKit.NSButtonTypePushOnPushOff)
    view.setBordered_(False)
    view.setTitle_("")
    view.setState_(AppKit.NSControlStateValueOn if on else AppKit.NSControlStateValueOff)
    view.setTarget_(target)
    view.setAction_("fire:")
    return view


class Selection(AppKit.NSView):
    """The purple pill behind the chosen page in the sidebar."""

    def drawRect_(self, rect):
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(self.bounds(), 10, 10)
        AppKit.NSGradient.alloc().initWithStartingColor_endingColor_(PURPLE_LIGHT, PURPLE).drawInBezierPath_angle_(
            path, 0)


class SidebarBackdrop(AppKit.NSView):
    """The sidebar: a shade lighter than the window, with a soft purple hill at the bottom."""

    def drawRect_(self, rect):
        bounds = self.bounds()
        w = bounds.size.width
        rgb(28, 27, 36).setFill()
        AppKit.NSRectFill(bounds)
        path = AppKit.NSBezierPath.bezierPath()
        path.moveToPoint_((-w * 0.2, 0))
        path.curveToPoint_controlPoint1_controlPoint2_((w * 0.45, 64), (w * 0.1, 0), (w * 0.2, 64))
        path.curveToPoint_controlPoint1_controlPoint2_((w * 1.2, 0), (w * 0.75, 64), (w * 0.95, 0))
        path.closePath()
        AppKit.NSGradient.alloc().initWithStartingColor_endingColor_(
            rgb(118, 88, 255, 0.28), rgb(70, 40, 170, 0.06)).drawInBezierPath_angle_(path, -90)
        rgb(255, 255, 255, 0.06).setFill()  # the edge, blended (NSRectFill would copy, not blend)
        AppKit.NSBezierPath.fillRect_(AppKit.NSMakeRect(w - 1, 0, 1, bounds.size.height))
