"""
The welcome window (first run, and "Setup…" in the menu). Draws interface/setup_flow.py's
steps with AppKit; all decisions are made there. Runs on the main thread only.

The window checks the permissions twice a second while it is open, so each ✓ appears by
itself as soon as the permission is switched on in System Settings.

With only the account step it is the "Sign in to Utter" window (interface/account_form.py);
calls to the sign-in server run on a background thread, never blocking the menu bar.
"""

import logging
from pathlib import Path
import threading
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Union

import AppKit
import objc

from . import account_form as af
from . import welcome_style as ws
from . import setup_flow
from .setup_flow import PERMISSION_ORDER, SetupFlow

logger = logging.getLogger(__name__)

WIDTH, HEIGHT = 760.0, 500.0
MARGIN = 28.0
NEXT_W = 190.0
POLL_SEC = 0.5
ICON_PATH = Path(__file__).resolve().parent.parent / "assets" / "utter-app-icon.png"

_COMMAND_KEYS = {"x": "cut:", "c": "copy:", "v": "paste:", "a": "selectAll:", "z": "undo:"}


class UtterSetupWindow(AppKit.NSWindow):
    """Utter has no Edit menu (menu bar app), so ⌘V / ⌘C / ⌘A are handled here; the Try
    step needs ⌘V, because that is how dictation types into the box."""

    def performKeyEquivalent_(self, event):
        flags = event.modifierFlags() & AppKit.NSEventModifierFlagDeviceIndependentFlagsMask
        if flags == AppKit.NSEventModifierFlagCommand:
            key = (event.charactersIgnoringModifiers() or "").lower()
            if key == "w":
                self.performClose_(None)
                return True
            action = _COMMAND_KEYS.get(key)
            if action and AppKit.NSApp.sendAction_to_from_(action, None, self):
                return True
        return objc.super(UtterSetupWindow, self).performKeyEquivalent_(event)


class _Action(AppKit.NSObject):
    """A button target that calls a Python function."""

    def initWithCallback_(self, callback):
        self = objc.super(_Action, self).init()
        if self is None:
            return None
        self.callback = callback
        return self

    def fire_(self, sender):
        try:
            self.callback()
        except Exception as e:
            logger.error("Setup window action failed: %s", e, exc_info=True)


class _Delegate(AppKit.NSObject):
    def initWithCallback_(self, callback):
        self = objc.super(_Delegate, self).init()
        if self is None:
            return None
        self.callback = callback
        return self

    def windowWillClose_(self, notification):
        self.callback()


# ---- Small view helpers -------------------------------------------------------------------

def _label(text: str, frame, size: float = 13.0, weight=None, color=None,
           align=AppKit.NSTextAlignmentLeft, selectable: bool = False):
    field = AppKit.NSTextField.wrappingLabelWithString_(text)
    field.setFrame_(frame)
    field.setFont_(AppKit.NSFont.systemFontOfSize_weight_(size, weight if weight is not None else AppKit.NSFontWeightRegular))
    field.setTextColor_(color or AppKit.NSColor.labelColor())
    field.setAlignment_(align)
    field.setSelectable_(selectable)
    return field


def _button(title: str, frame, target, primary: bool = False):
    button = AppKit.NSButton.alloc().initWithFrame_(frame)
    button.setTitle_(title)
    button.setBezelStyle_(AppKit.NSBezelStyleRounded)
    button.setTarget_(target)
    button.setAction_("fire:")
    if primary:
        button.setKeyEquivalent_("\r")
    return button


def _rect(x, y, w, h):
    return AppKit.NSMakeRect(x, y, w, h)


def _link_title(title: str):
    style = AppKit.NSMutableParagraphStyle.alloc().init()
    style.setAlignment_(AppKit.NSTextAlignmentCenter)
    return AppKit.NSAttributedString.alloc().initWithString_attributes_(title, {
        AppKit.NSForegroundColorAttributeName: ws.LAVENDER,
        AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_weight_(13.5, AppKit.NSFontWeightMedium),
        AppKit.NSParagraphStyleAttributeName: style,
    })


def _link(title: str, frame, target):
    button = AppKit.NSButton.alloc().initWithFrame_(frame)
    button.setBordered_(False)
    button.setTarget_(target)
    button.setAction_("fire:")
    button.setAttributedTitle_(_link_title(title))
    return button


def _shared_app():
    return AppKit.NSApplication.sharedApplication()


def _in_background(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="account", daemon=True).start()


def _on_main(work: Callable[[], None]) -> None:
    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(work)


class WelcomeWindow:
    """
    Callbacks (all optional):
      permissions()           -> {"microphone": bool, ...}  current answers from macOS
      asked_before(key)       -> bool   macOS was already asked once for this one
      turn_on(key)                      ask macOS / open System Settings for one permission
      hotkeys_ready()                   Accessibility and Input Monitoring just turned on
      restart()                         quit and reopen Utter (resume on the Try step)
      finish(start_at_login)            the last button
      closed()                          the window was closed (finished or not)
      signed_in(session)                the account step signed someone in
      sign_out()                        "Use a different account"
    account_form: the account step's logic (None: accounts are off, no account step).
    steps: which steps to show (default: all that apply); ["account"] = sign-in window.
    """

    def __init__(
        self,
        command_hotkey: str,
        dictation_hotkey: str,
        permissions: Callable[[], Dict[str, bool]],
        turn_on: Callable[[str], None],
        asked_before: Callable[[str], bool] = lambda key: False,
        hotkeys_ready: Callable[[], None] = lambda: None,
        restart: Optional[Callable[[], None]] = None,
        finish: Callable[[bool], None] = lambda start_at_login: None,
        closed: Callable[[], None] = lambda: None,
        login_available: bool = True,
        start_at_login: bool = setup_flow.START_AT_LOGIN_DEFAULT,
        start_step: Union[int, str] = 0,
        clock: Callable[[], float] = time.monotonic,
        account_form: Optional[af.AccountForm] = None,
        signed_in: Callable[[object], None] = lambda session: None,
        sign_out: Callable[[], None] = lambda: None,
        steps: Optional[Sequence[str]] = None,
        background: Callable[[Callable[[], None]], None] = _in_background,
        main: Callable[[Callable[[], None]], None] = _on_main,
    ):
        self.command_key = setup_flow.format_hotkey(command_hotkey)
        self.dictation_key = setup_flow.format_hotkey(dictation_hotkey)
        self._permissions = permissions
        self._turn_on = turn_on
        self._asked_before = asked_before
        self._hotkeys_ready = hotkeys_ready
        self._restart = restart
        self._finish = finish
        self._closed = closed
        self.login_available = login_available
        self._clock = clock
        self.account_form = account_form
        self._signed_in = signed_in
        self._sign_out = sign_out
        self._background = background
        self._main = main
        self.flow = SetupFlow(
            start_step=start_step, start_at_login=start_at_login,
            steps=steps if steps is not None else setup_flow.steps_for(accounts=account_form is not None),
            signed_in=bool(account_form and account_form.done))
        if self.flow.step_name == "try":
            self.flow.try_started_at = clock()
        self._finished = False
        self._timer = None
        self._targets: List = []  # buttons only keep weak references to their targets
        self.window = None
        self.pages: Dict[str, AppKit.NSView] = {}
        self.rows: Dict[str, Tuple] = {}  # key -> (mark, button, hint)
        self._refresh_permissions(initial=True)
        self._build()
        self._show_step()

    # ---- Building --------------------------------------------------------------------

    def _target(self, callback):
        target = _Action.alloc().initWithCallback_(callback)
        self._targets.append(target)
        return target

    def _build(self) -> None:
        style = (AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable
                 | AppKit.NSWindowStyleMaskFullSizeContentView)
        self.window = UtterSetupWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            _rect(0, 0, WIDTH, HEIGHT), style, AppKit.NSBackingStoreBuffered, False)
        self.window.setTitle_("Sign in to Utter" if self.flow.steps == setup_flow.ACCOUNT_ONLY
                              else "Welcome to Utter")
        self.window.setTitlebarAppearsTransparent_(True)
        self.window.setTitleVisibility_(AppKit.NSWindowTitleHidden)
        self.window.setReleasedWhenClosed_(False)
        # Always dark (the design), whatever the Mac's own setting
        self.window.setAppearance_(AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameDarkAqua))
        self.window.setBackgroundColor_(ws.BACKGROUND)
        self._delegate = _Delegate.alloc().initWithCallback_(self._on_close)
        self.window.setDelegate_(self._delegate)
        content = self.window.contentView()
        content.addSubview_(ws.Backdrop.alloc().initWithFrame_(_rect(0, 0, WIDTH, HEIGHT)))

        builders = [("welcome", self._build_welcome), ("permissions", self._build_permissions),
                    ("try", self._build_try), ("finish", self._build_finish)]
        if self.account_form is not None:
            builders.insert(1, ("account", self._build_account))
        for name, build in builders:
            page = AppKit.NSView.alloc().initWithFrame_(_rect(0, 0, WIDTH, HEIGHT))
            build(page)
            page.setHidden_(True)
            content.addSubview_(page)
            self.pages[name] = page

        self.step_dots = ws.step_dots(_rect(MARGIN + 12, 40, 120, 14))
        content.addSubview_(self.step_dots)
        self.step_label = ws.text_label("", _rect(MARGIN + 104, 36, 140, 20), ws.ui_font(14), ws.TEXT_DIM)
        content.addSubview_(self.step_label)
        self.next_button = ws.pill_button("", _rect(WIDTH - MARGIN - NEXT_W, 28, NEXT_W, 40),
                                          self._target(self.next), style="primary", arrow=True)
        self.next_button.setKeyEquivalent_("\r")
        content.addSubview_(self.next_button)
        self.back_button = ws.pill_button("Back", _rect(WIDTH - MARGIN - NEXT_W - 12 - 96, 30, 96, 36),
                                          self._target(self.back), style="ghost")
        content.addSubview_(self.back_button)

    # Page pieces ---------------------------------------------------------------------------

    def _heading(self, page, pieces, y: float, size: float = 50.0, burst: bool = False):
        view = ws.hand_title(pieces, _rect(0, y, WIDTH, size * 1.45), size, burst_marks=burst)
        page.addSubview_(view)
        return view

    def _subtitle(self, page, text: str, y: float, height: float = 44.0, size: float = 15.0):
        view = ws.text_label(text, _rect(90, y, WIDTH - 180, height), ws.ui_font(size), ws.TEXT_DIM,
                             align=AppKit.NSTextAlignmentCenter)
        page.addSubview_(view)
        return view

    def _build_welcome(self, page) -> None:
        page.addSubview_(ws.icon_tile(_rect((WIDTH - 88) / 2, 384, 88, 88)))
        self._heading(page, [("Welcome to", "underline"), ("Utter", "circle")], 296, size=68, burst=True)
        self._subtitle(page, "Talk to your Mac. Utter types what you say and opens apps for you.\n"
                             "Your voice never leaves this Mac.", 238, 46)
        card_x, card_w = (WIDTH - 470) / 2, 470
        for y, key, bold, rest in ((174, self.dictation_key, "Dictation:", " type by speaking, in any text box"),
                                   (118, self.command_key, "Command:", " “open Safari”, “take a note”…")):
            row = ws.card(_rect(card_x, y, card_w, 48), radius=16)
            row.addSubview_(ws.keycap(key, _rect(18, 8, 76, 32)))
            row.addSubview_(ws.divider(_rect(112, 12, 1, 24)))
            text = AppKit.NSMutableAttributedString.alloc().initWithAttributedString_(
                ws.attributed(bold, ws.ui_font(15.5, AppKit.NSFontWeightSemibold), ws.TEXT))
            text.appendAttributedString_(ws.attributed(rest, ws.ui_font(15.5), ws.TEXT))
            label = ws.text_label("", _rect(130, 14, card_w - 140, 21), ws.ui_font(15.5))
            label.setAttributedStringValue_(text)
            row.addSubview_(label)
            page.addSubview_(row)
        page.addSubview_(ws.text_label("Tap a hotkey and speak; Utter stops when you stop talking. Esc cancels.",
                                       _rect(MARGIN, 84, WIDTH - 2 * MARGIN, 20), ws.ui_font(13.5), ws.TEXT_DIM,
                                       align=AppKit.NSTextAlignmentCenter))
        # the handwritten side notes
        page.addSubview_(ws.hand_note([("speak", 0), ("type", 14), ("control", 10)], _rect(30, 176, 120, 104),
                                      size=27, tilt=8, strokes=[ws.swash(14, 66, 12, lift=2)]))
        page.addSubview_(ws.strokes(_rect(0, 0, WIDTH, HEIGHT),
                                    [ws.curved_arrow((96, 168), (104, 132), (card_x - 8, 142))]))
        page.addSubview_(ws.hand_note([("voice", 0), ("first", 6), ("on Mac", -6)], _rect(622, 152, 120, 104),
                                      size=27, tilt=8,
                                      strokes=[ws.swash(4, 76, 14, lift=2), ws.swash(14, 54, 7, lift=1.2, width=2.2)]
                                      + ws.burst(62, 84, 11, width=2.2)))

    def _build_permissions(self, page) -> None:
        self._heading(page, [("Give Utter", None), ("three", "circle"), ("permissions", None)], 372, size=46)
        self._subtitle(page, "Press each button and switch Utter on in System Settings.\n"
                             "The ✓ appears here by itself; macOS only asks once.", 318, 44, size=14)
        card_x, card_w = (WIDTH - 560) / 2, 560
        self.badges = {}
        for i, key in enumerate(PERMISSION_ORDER):
            y = 246 - i * 66
            row = self.flow.rows[key]
            panel = ws.card(_rect(card_x, y, card_w, 58), radius=16)
            circle = ws.badge(_rect(16, 14, 30, 30))
            panel.addSubview_(circle)
            mark = ws.text_label("", _rect(16, 18, 30, 22), ws.ui_font(15, AppKit.NSFontWeightBold), ws.TEXT,
                                 align=AppKit.NSTextAlignmentCenter)
            panel.addSubview_(mark)
            panel.addSubview_(ws.text_label(row.name, _rect(62, 30, 330, 20),
                                            ws.ui_font(15, AppKit.NSFontWeightSemibold)))
            panel.addSubview_(ws.text_label(row.why, _rect(62, 10, card_w - 62 - 160, 18), ws.ui_font(12.5),
                                            ws.TEXT_DIM))
            button = ws.pill_button("", _rect(card_w - 16 - 136, 12, 136, 34),
                                    self._target(lambda k=key: self.turn_on(k)), style="ghost")
            panel.addSubview_(button)
            page.addSubview_(panel)
            hint = ws.text_label(row.stuck_hint(), _rect(card_x + 8, 76, card_w - 16, 32), ws.ui_font(11.5),
                                 ws.ORANGE, selectable=True)
            hint.setHidden_(True)
            page.addSubview_(hint)
            self.rows[key] = (mark, button, hint)
            self.badges[key] = circle

    def _field_box(self, page, field, frame) -> None:
        """A text field drawn borderless inside a rounded dark box (shown and hidden with it)."""
        box = ws.card(frame, radius=12, fill=ws.rgb(255, 255, 255, 0.06), edge=ws.rgb(255, 255, 255, 0.14))
        field.setFrame_(_rect(14, (frame.size.height - 22) / 2, frame.size.width - 28, 22))
        field.setBezeled_(False)
        field.setBordered_(False)
        field.setDrawsBackground_(False)
        field.setFocusRingType_(AppKit.NSFocusRingTypeNone)
        field.setFont_(ws.ui_font(15))
        field.setTextColor_(ws.TEXT)
        box.addSubview_(field)
        page.addSubview_(box)
        self._boxes[field] = box

    def _build_account(self, page) -> None:
        self._boxes = getattr(self, "_boxes", {})
        self.account_heading = self._heading(page, [("", None)], 378, size=50)
        self.account_subtitle = self._subtitle(page, "", 330, 40, size=14)
        x, w = (WIDTH - 360) / 2, 360
        self.email_field = AppKit.NSTextField.alloc().initWithFrame_(_rect(0, 0, w, 22))
        self.email_field.setPlaceholderString_("Email")
        self.password_field = AppKit.NSSecureTextField.alloc().initWithFrame_(_rect(0, 0, w, 22))
        self.code_field = AppKit.NSTextField.alloc().initWithFrame_(_rect(0, 0, w, 22))
        self.code_field.setPlaceholderString_("6-digit code")
        self._field_box(page, self.email_field, _rect(x, 270, w, 44))
        self._field_box(page, self.password_field, _rect(x, 216, w, 44))
        self._field_box(page, self.code_field, _rect(x, 270, w, 44))
        self.account_check = ws.text_label("✓", _rect((WIDTH - 80) / 2, 236, 80, 64),
                                           ws.ui_font(48, AppKit.NSFontWeightBold), ws.GREEN,
                                           align=AppKit.NSTextAlignmentCenter)
        page.addSubview_(self.account_check)
        self.submit_button = ws.pill_button("", _rect(x, 160, w, 42),
                                            self._target(lambda: self.account(af.SUBMIT)), style="primary")
        page.addSubview_(self.submit_button)
        self.spinner = AppKit.NSProgressIndicator.alloc().initWithFrame_(_rect(x + w + 12, 173, 16, 16))
        self.spinner.setStyle_(AppKit.NSProgressIndicatorStyleSpinning)
        self.spinner.setDisplayedWhenStopped_(False)
        page.addSubview_(self.spinner)
        self.account_message = ws.text_label("", _rect(110, 112, WIDTH - 220, 38), ws.ui_font(13), ws.TEXT_DIM,
                                             align=AppKit.NSTextAlignmentCenter, selectable=True)
        page.addSubview_(self.account_message)
        self.account_links = [
            _link("", _rect(130, 86, 220, 22), self._target(lambda: self._account_link(0))),
            _link("", _rect(WIDTH - 350, 86, 220, 22), self._target(lambda: self._account_link(1))),
        ]
        for link in self.account_links:
            page.addSubview_(link)

    def _account_link(self, index: int) -> None:
        links = self.account_form.links()
        if index < len(links):
            self.account(links[index][1])

    def _build_try(self, page) -> None:
        self._boxes = getattr(self, "_boxes", {})
        self._heading(page, [("Try", None), ("it", "circle")], 374, size=56, burst=True)
        self._subtitle(page, f"Press {self.dictation_key} and say “Hello Utter, this is my first dictation.”\n"
                             "Then stop talking: Utter types it into the box below.", 318, 44, size=14.5)
        x, w = (WIDTH - 560) / 2, 560
        box = ws.card(_rect(x, 202, w, 96), radius=16, fill=ws.rgb(255, 255, 255, 0.05),
                      edge=ws.rgb(255, 255, 255, 0.14))
        self.try_field = AppKit.NSTextField.alloc().initWithFrame_(_rect(16, 12, w - 32, 72))
        self.try_field.setPlaceholderString_("Your words appear here")
        self.try_field.setFont_(ws.ui_font(17))
        self.try_field.setTextColor_(ws.TEXT)
        self.try_field.setBezeled_(False)
        self.try_field.setDrawsBackground_(False)
        self.try_field.setFocusRingType_(AppKit.NSFocusRingTypeNone)
        self.try_field.cell().setWraps_(True)
        box.addSubview_(self.try_field)
        page.addSubview_(box)
        self.try_status = ws.text_label("", _rect(x, 166, w, 22), ws.ui_font(14, AppKit.NSFontWeightMedium),
                                        ws.TEXT_DIM, align=AppKit.NSTextAlignmentCenter)
        page.addSubview_(self.try_status)
        self.try_help = ws.text_label(
            "Nothing happening? After switching on Input Monitoring, macOS sometimes needs Utter "
            "to be restarted.", _rect(x, 106, w - 160, 36), ws.ui_font(12.5), ws.TEXT_DIM)
        page.addSubview_(self.try_help)
        self.restart_button = ws.pill_button("Restart Utter", _rect(x + w - 140, 108, 140, 34),
                                             self._target(self.restart), style="ghost")
        page.addSubview_(self.restart_button)
        for view in (self.try_help, self.restart_button):
            view.setHidden_(True)
        page.addSubview_(ws.hand_note([("just", 0), ("talk!", 10)], _rect(640, 300, 100, 80), size=27, tilt=10,
                                      strokes=[ws.swash(8, 62, 8, lift=2)]))

    def _build_finish(self, page) -> None:
        page.addSubview_(ws.icon_tile(_rect((WIDTH - 80) / 2, 368, 80, 80)))
        self._heading(page, [("You're", None), ("all set", "circle")], 278, size=62, burst=True)
        self._subtitle(page, "Utter lives in the menu bar: look for the wave icon at the top of your screen.\n"
                             "You can open this window again in Settings → General.", 224, 46, size=14.5)
        self.login_checkbox = AppKit.NSButton.checkboxWithTitle_target_action_(
            "Start Utter when I log in", self._target(self._toggle_login), "fire:")
        self.login_checkbox.setFrame_(_rect((WIDTH - 240) / 2, 170, 240, 26))
        self.login_checkbox.setFont_(ws.ui_font(15))
        page.addSubview_(self.login_checkbox)
        note = ("Utter is added to System Settings › General › Login Items. You can change this "
                "any time in Utter's Settings." if self.login_available else
                "Only available in Utter.app (this copy runs from the project folder).")
        page.addSubview_(ws.text_label(note, _rect(150, 118, WIDTH - 300, 36), ws.ui_font(12.5), ws.TEXT_FAINT,
                                       align=AppKit.NSTextAlignmentCenter))
        page.addSubview_(ws.hand_note([("happy", 0), ("talking!", 8)], _rect(628, 300, 120, 80), size=27,
                                      tilt=-8, strokes=[ws.swash(10, 86, 8, lift=2)]))

    # ---- Showing ---------------------------------------------------------------------

    def show(self) -> None:
        self.window.center()
        self._bring_to_front()
        self._start_timer()
        # At startup macOS may still be launching Utter and ignore the first request: ask
        # once more a moment later (only if the window is still open)
        AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            0.6, False, lambda _t: self._bring_to_front() if self.is_visible else None)

    def _bring_to_front(self) -> None:
        """In front of every other app's windows, ready to type in. Utter is a menu bar app,
        so macOS does not bring it forward by itself when it starts; and the newer
        "polite" activate() lets the app in front keep its place. So: take the front
        firmly (ignoring other apps), and put the window on top even if macOS hesitates."""
        app = _shared_app()
        try:
            app.activateIgnoringOtherApps_(True)
        except AttributeError:
            app.activate()
        self.window.makeKeyAndOrderFront_(None)
        self.window.orderFrontRegardless()

    def _start_timer(self) -> None:
        if self._timer is None:
            self._timer = AppKit.NSTimer.timerWithTimeInterval_repeats_block_(POLL_SEC, True, self.tick)
            AppKit.NSRunLoop.mainRunLoop().addTimer_forMode_(self._timer, AppKit.NSRunLoopCommonModes)

    def _stop_timer(self) -> None:
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None

    def _show_step(self) -> None:
        name = self.flow.step_name
        for page_name, page in self.pages.items():
            page.setHidden_(page_name != name)
        total = len(self.flow.steps)
        self.step_label.setStringValue_(f"Step {self.flow.step + 1} of {total}" if total > 1 else "")
        self.step_dots.setStep_of_(self.flow.step, total)
        self.back_button.setHidden_(self.flow.step == 0)
        # Return presses the form's own button on the account step, else Continue
        on_account = name == "account" and self.account_form is not None
        self.next_button.setKeyEquivalent_("" if on_account and not self.account_form.done else "\r")
        if on_account:
            self._update_account(focus=True)
        if name == "try" and self.window is not None:
            self.window.makeFirstResponder_(self.try_field)
        if name == "finish":
            self.login_checkbox.setEnabled_(self.login_available)
            self.login_checkbox.setState_(
                AppKit.NSControlStateValueOn if (self.flow.start_at_login and self.login_available)
                else AppKit.NSControlStateValueOff)
        self._update_views()

    def _update_views(self) -> None:
        now = self._clock()
        highlight = self.flow.next_permission()
        hint_shown = False  # the hints share one place under the rows: the first stuck one
        for key, (mark, button, hint) in self.rows.items():
            row = self.flow.rows[key]
            circle = self.badges[key]
            if row.granted:
                mark.setStringValue_("✓")
                mark.setTextColor_(ws.BACKGROUND)
                circle.fill = ws.GREEN
            else:
                mark.setStringValue_(str(PERMISSION_ORDER.index(key) + 1))
                mark.setTextColor_(ws.TEXT if key == highlight else ws.TEXT_DIM)
                circle.fill = ws.PURPLE if key == highlight else ws.KEYCAP
            circle.setNeedsDisplay_(True)
            button.setTitle_(row.button_title(self._asked_before(key)))
            button.setEnabled_(not row.granted)
            button.setKeyEquivalent_("")
            stuck = row.stuck(now) and not hint_shown
            hint.setHidden_(not stuck)
            hint_shown = hint_shown or stuck
        self.try_status.setStringValue_(self.flow.try_status())
        self.try_status.setTextColor_(ws.GREEN if self.flow.try_passed else ws.TEXT_DIM)
        needs_help = self.flow.try_needs_help(now) and self._restart is not None
        self.try_help.setHidden_(not needs_help)
        self.restart_button.setHidden_(not needs_help)
        self.next_button.setTitle_(self.flow.continue_title())
        self.next_button.setEnabled_(self.flow.can_continue)

    def _update_account(self, focus: bool = False) -> None:
        form = self.account_form
        if form is None:
            return
        fields = form.fields()
        self.account_heading.setStringValue_(form.heading())
        self.account_subtitle.setStringValue_(form.subtitle())
        for field, shown in ((self.email_field, fields.email), (self.code_field, fields.code),
                             (self.password_field, fields.password)):
            field.setHidden_(not shown)
            self._boxes[field].setHidden_(not shown)
        self.password_field.setPlaceholderString_(form.password_placeholder())
        self.account_check.setHidden_(not form.done)
        if fields.email and not str(self.email_field.stringValue()) and form.email:
            self.email_field.setStringValue_(form.email)
        title = form.submit_title()
        self.submit_button.setHidden_(title is None)
        self.submit_button.setTitle_(title or "")
        self.submit_button.setKeyEquivalent_("\r" if title and not form.done else "")
        for control in (self.submit_button, self.email_field, self.password_field, self.code_field):
            control.setEnabled_(not form.busy)
        if form.busy:
            self.spinner.startAnimation_(None)
        else:
            self.spinner.stopAnimation_(None)
        message = form.error or form.info
        self.account_message.setStringValue_(message)
        self.account_message.setTextColor_(
            ws.RED if form.error else
            ws.ORANGE if (form.suggestion or form.info.startswith("You haven't")) else ws.TEXT_DIM)
        links = form.links()
        for i, link in enumerate(self.account_links):
            if i < len(links):
                link.setHidden_(False)
                link.setAttributedTitle_(_link_title(links[i][0]))
                link.setEnabled_(not form.busy)
            else:
                link.setHidden_(True)
        if len(links) == 1:  # a single link sits in the middle
            self.account_links[0].setFrame_(_rect((WIDTH - 260) / 2, 86, 260, 22))
        else:
            self.account_links[0].setFrame_(_rect(130, 86, 220, 22))
        self.flow.signed_in = form.done
        self.next_button.setEnabled_(self.flow.can_continue)
        self.next_button.setKeyEquivalent_("\r" if form.done else "")
        if focus and self.window is not None:
            first = (self.code_field if fields.code else
                     self.password_field if (fields.email and str(self.email_field.stringValue())) else
                     self.email_field if fields.email else None)
            if first is not None:
                self.window.makeFirstResponder_(first)

    def account(self, action: str) -> None:
        """A button or link on the account step."""
        form = self.account_form
        if form is None or form.busy:
            return
        if form.switch(action):
            self.password_field.setStringValue_("")
            self._update_account(focus=True)
            return
        if action == af.SIGN_OUT:
            self._sign_out()
            form.signed_out()
            self._update_account(focus=True)
            return
        if action == af.USE_SUGGESTION and form.suggestion:
            self.email_field.setStringValue_(form.suggestion)
            action = af.SUBMIT
        email = str(self.email_field.stringValue())
        password = str(self.password_field.stringValue())
        code = str(self.code_field.stringValue())
        problem = form.check(action, email, password, code)
        if problem:
            form.error, form.info = problem, ""
            self._update_account()
            return
        mode_before = form.mode
        form.busy = True
        self._update_account()

        def work():
            try:
                session = form.perform(action, email, password, code)
            except Exception as e:  # never leave the form stuck "busy"
                logger.error("Account action failed: %s", e, exc_info=True)
                form.error, session = "Something went wrong. Please try again.", None

            def finish():
                form.busy = False
                if session is not None:
                    self.password_field.setStringValue_("")
                    try:
                        self._signed_in(session)
                    except Exception as e:
                        logger.error("Sign-in callback failed: %s", e, exc_info=True)
                if form.mode != mode_before:
                    self.code_field.setStringValue_("")
                    if form.mode != af.SIGN_IN:
                        self.password_field.setStringValue_("")
                self._update_account(focus=form.mode != mode_before)
                if session is not None and self.is_visible:
                    self._bring_to_front()
            self._main(finish)
        self._background(work)

    # ---- Events ----------------------------------------------------------------------

    def _refresh_permissions(self, initial: bool = False) -> None:
        try:
            granted = self._permissions()
        except Exception as e:
            logger.debug("Could not read permissions: %s", e)
            return
        turned_on = self.flow.update_permissions(granted)
        for key in turned_on:
            if not initial:
                logger.info("[Setup] %s is now on.", self.flow.rows[key].name)
        if turned_on and not initial and self.is_visible:
            self._bring_to_front()  # back from System Settings, showing the new ✓
        ready = self.flow.hotkeys_became_ready()
        if ready and not initial:
            try:
                self._hotkeys_ready()
            except Exception as e:
                logger.error("Could not restart the hotkeys: %s", e, exc_info=True)

    def tick(self, _timer=None) -> None:
        self._refresh_permissions()
        if self.flow.step_name == "try" and self.flow.set_try_text(str(self.try_field.stringValue())):
            logger.info("[Setup] Try it: dictation worked.")
        self._update_views()

    def turn_on(self, key: str) -> None:
        self.flow.mark_asked(key, self._clock())
        self._turn_on(key)
        self._update_views()

    def next(self) -> None:
        if not self.flow.can_continue:
            return
        if self.flow.is_last:
            self._finished = True
            self._finish(self.flow.start_at_login and self.login_available)
            self.close()
            return
        self.flow.next(now=self._clock())
        self._show_step()

    def back(self) -> None:
        self.flow.back()
        self._show_step()

    def restart(self) -> None:
        if self._restart is not None:
            self._restart()

    def _toggle_login(self) -> None:
        self.flow.start_at_login = self.login_checkbox.state() == AppKit.NSControlStateValueOn

    def close(self) -> None:
        if self.window is not None:
            self.window.close()  # calls _on_close through the delegate

    def _on_close(self) -> None:
        self._stop_timer()
        try:
            self._closed()
        except Exception as e:
            logger.debug("Setup window close callback failed: %s", e)

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def is_visible(self) -> bool:
        return self.window is not None and bool(self.window.isVisible())
