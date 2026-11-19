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
from . import setup_flow
from .setup_flow import PERMISSION_ORDER, SetupFlow

logger = logging.getLogger(__name__)

WIDTH, HEIGHT = 560.0, 440.0
MARGIN = 32.0
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
        AppKit.NSForegroundColorAttributeName: AppKit.NSColor.linkColor(),
        AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(12.5),
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
        self._delegate = _Delegate.alloc().initWithCallback_(self._on_close)
        self.window.setDelegate_(self._delegate)
        content = self.window.contentView()

        builders = [("welcome", self._build_welcome), ("permissions", self._build_permissions),
                    ("try", self._build_try), ("finish", self._build_finish)]
        if self.account_form is not None:
            builders.insert(1, ("account", self._build_account))
        for name, build in builders:
            page = AppKit.NSView.alloc().initWithFrame_(_rect(0, 64, WIDTH, HEIGHT - 64))
            build(page)
            page.setHidden_(True)
            content.addSubview_(page)
            self.pages[name] = page

        self.step_label = _label("", _rect(MARGIN, 26, 150, 18), 12, color=AppKit.NSColor.secondaryLabelColor())
        content.addSubview_(self.step_label)
        self.next_button = _button("", _rect(WIDTH - MARGIN - 170, 20, 170, 32), self._target(self.next), primary=True)
        content.addSubview_(self.next_button)
        self.back_button = _button("Back", _rect(WIDTH - MARGIN - 170 - 110, 20, 100, 32), self._target(self.back))
        content.addSubview_(self.back_button)

    def _title(self, page, text: str, y: float, centered: bool = False) -> None:
        align = AppKit.NSTextAlignmentCenter if centered else AppKit.NSTextAlignmentLeft
        page.addSubview_(_label(text, _rect(MARGIN, y, WIDTH - 2 * MARGIN, 34), 24,
                                AppKit.NSFontWeightBold, align=align))

    def _build_welcome(self, page) -> None:
        if ICON_PATH.is_file():
            icon = AppKit.NSImageView.alloc().initWithFrame_(_rect((WIDTH - 88) / 2, 252, 88, 88))
            icon.setImage_(AppKit.NSImage.alloc().initWithContentsOfFile_(str(ICON_PATH)))
            page.addSubview_(icon)
        self._title(page, "Welcome to Utter", 210, centered=True)
        page.addSubview_(_label(
            "Talk to your Mac. Utter types what you say and opens apps for you. "
            "Your voice never leaves this Mac.",
            _rect(70, 148, WIDTH - 140, 56), 13, color=AppKit.NSColor.secondaryLabelColor(),
            align=AppKit.NSTextAlignmentCenter))
        for y, key, text in ((106, self.dictation_key, "Dictation: type by speaking, in any text box"),
                             (68, self.command_key, "Command: “open Safari”, “take a note”…")):
            cap = _label(key, _rect(120, y, 76, 26), 15, AppKit.NSFontWeightSemibold,
                         align=AppKit.NSTextAlignmentCenter)
            cap.setWantsLayer_(True)
            cap.setDrawsBackground_(True)
            cap.setBackgroundColor_(AppKit.NSColor.quaternaryLabelColor())
            cap.layer().setCornerRadius_(6)
            page.addSubview_(cap)
            page.addSubview_(_label(text, _rect(210, y + 3, 300, 20), 13))
        page.addSubview_(_label("Tap a hotkey and speak; Utter stops when you stop talking. Esc cancels.",
                                _rect(MARGIN, 26, WIDTH - 2 * MARGIN, 18), 12,
                                color=AppKit.NSColor.secondaryLabelColor(), align=AppKit.NSTextAlignmentCenter))

    def _build_permissions(self, page) -> None:
        self._title(page, "Give Utter three permissions", 306)
        page.addSubview_(_label(
            "Press each button and switch Utter on in System Settings. "
            "The ✓ appears here by itself; macOS only asks once.",
            _rect(MARGIN, 266, WIDTH - 2 * MARGIN, 36), 13, color=AppKit.NSColor.secondaryLabelColor()))
        for i, key in enumerate(PERMISSION_ORDER):
            y = 196 - i * 88
            row = self.flow.rows[key]
            mark = _label("", _rect(MARGIN, y + 30, 30, 28), 20, AppKit.NSFontWeightBold)
            page.addSubview_(mark)
            page.addSubview_(_label(row.name, _rect(MARGIN + 36, y + 44, 320, 20), 14, AppKit.NSFontWeightSemibold))
            page.addSubview_(_label(row.why, _rect(MARGIN + 36, y + 24, WIDTH - 2 * MARGIN - 36 - 140, 20), 12,
                                    color=AppKit.NSColor.secondaryLabelColor()))
            button = _button("", _rect(WIDTH - MARGIN - 130, y + 32, 130, 30),
                             self._target(lambda k=key: self.turn_on(k)))
            page.addSubview_(button)
            hint = _label(row.stuck_hint(), _rect(MARGIN + 36, y - 12, WIDTH - 2 * MARGIN - 36, 34), 11,
                          color=AppKit.NSColor.systemOrangeColor(), selectable=True)
            hint.setHidden_(True)
            page.addSubview_(hint)
            self.rows[key] = (mark, button, hint)

    def _build_account(self, page) -> None:
        self.account_heading = _label("", _rect(MARGIN, 306, WIDTH - 2 * MARGIN, 34), 24,
                                      AppKit.NSFontWeightBold, align=AppKit.NSTextAlignmentCenter)
        page.addSubview_(self.account_heading)
        self.account_subtitle = _label("", _rect(60, 262, WIDTH - 120, 36), 13,
                                       color=AppKit.NSColor.secondaryLabelColor(),
                                       align=AppKit.NSTextAlignmentCenter)
        page.addSubview_(self.account_subtitle)
        x, w = (WIDTH - 320) / 2, 320
        self.email_field = AppKit.NSTextField.alloc().initWithFrame_(_rect(x, 218, w, 28))
        self.email_field.setPlaceholderString_("Email")
        self.password_field = AppKit.NSSecureTextField.alloc().initWithFrame_(_rect(x, 180, w, 28))
        self.code_field = AppKit.NSTextField.alloc().initWithFrame_(_rect(x, 218, w, 28))
        self.code_field.setPlaceholderString_("6-digit code")
        for field in (self.email_field, self.password_field, self.code_field):
            field.setFont_(AppKit.NSFont.systemFontOfSize_(14))
            field.setBezelStyle_(AppKit.NSTextFieldRoundedBezel)
            page.addSubview_(field)
        self.account_check = _label("✓", _rect((WIDTH - 60) / 2, 196, 60, 48), 40, AppKit.NSFontWeightBold,
                                    color=AppKit.NSColor.systemGreenColor(), align=AppKit.NSTextAlignmentCenter)
        page.addSubview_(self.account_check)
        self.submit_button = _button("", _rect(x, 132, w, 32), self._target(lambda: self.account(af.SUBMIT)))
        page.addSubview_(self.submit_button)
        self.spinner = AppKit.NSProgressIndicator.alloc().initWithFrame_(_rect(x + w + 10, 140, 16, 16))
        self.spinner.setStyle_(AppKit.NSProgressIndicatorStyleSpinning)
        self.spinner.setDisplayedWhenStopped_(False)
        page.addSubview_(self.spinner)
        self.account_message = _label("", _rect(70, 88, WIDTH - 140, 36), 12,
                                      align=AppKit.NSTextAlignmentCenter, selectable=True)
        page.addSubview_(self.account_message)
        self.account_links = [
            _link("", _rect(70, 56, 200, 22), self._target(lambda: self._account_link(0))),
            _link("", _rect(WIDTH - 270, 56, 200, 22), self._target(lambda: self._account_link(1))),
        ]
        for link in self.account_links:
            page.addSubview_(link)

    def _account_link(self, index: int) -> None:
        links = self.account_form.links()
        if index < len(links):
            self.account(links[index][1])

    def _build_try(self, page) -> None:
        self._title(page, "Try it", 306)
        page.addSubview_(_label(
            f"Press {self.dictation_key} and say “Hello Utter, this is my first dictation.” "
            "Then stop talking: Utter types it into the box below.",
            _rect(MARGIN, 254, WIDTH - 2 * MARGIN, 40), 13))
        self.try_field = AppKit.NSTextField.alloc().initWithFrame_(_rect(MARGIN, 172, WIDTH - 2 * MARGIN, 64))
        self.try_field.setPlaceholderString_("Your words appear here")
        self.try_field.setFont_(AppKit.NSFont.systemFontOfSize_(15))
        self.try_field.cell().setWraps_(True)
        page.addSubview_(self.try_field)
        self.try_status = _label("", _rect(MARGIN, 138, WIDTH - 2 * MARGIN, 22), 13, AppKit.NSFontWeightMedium)
        page.addSubview_(self.try_status)
        self.try_help = _label(
            "Nothing happening? After switching on Input Monitoring, macOS sometimes needs Utter "
            "to be restarted.", _rect(MARGIN, 84, WIDTH - 2 * MARGIN - 150, 36), 12,
            color=AppKit.NSColor.secondaryLabelColor())
        page.addSubview_(self.try_help)
        self.restart_button = _button("Restart Utter", _rect(WIDTH - MARGIN - 130, 88, 130, 30),
                                      self._target(self.restart))
        page.addSubview_(self.restart_button)
        for view in (self.try_help, self.restart_button):
            view.setHidden_(True)

    def _build_finish(self, page) -> None:
        if ICON_PATH.is_file():
            icon = AppKit.NSImageView.alloc().initWithFrame_(_rect((WIDTH - 72) / 2, 266, 72, 72))
            icon.setImage_(AppKit.NSImage.alloc().initWithContentsOfFile_(str(ICON_PATH)))
            page.addSubview_(icon)
        self._title(page, "You're all set", 224, centered=True)
        page.addSubview_(_label(
            "Utter lives in the menu bar: look for the wave icon at the top of your screen. "
            "You can open this window again from its menu (Setup…).",
            _rect(70, 172, WIDTH - 140, 40), 13, color=AppKit.NSColor.secondaryLabelColor(),
            align=AppKit.NSTextAlignmentCenter))
        self.login_checkbox = AppKit.NSButton.checkboxWithTitle_target_action_(
            "Start Utter when I log in", self._target(self._toggle_login), "fire:")
        self.login_checkbox.setFrame_(_rect((WIDTH - 220) / 2, 120, 220, 24))
        self.login_checkbox.setFont_(AppKit.NSFont.systemFontOfSize_(14))
        page.addSubview_(self.login_checkbox)
        note = ("Utter is added to System Settings > General > Login Items. You can change this "
                "any time in Utter's menu." if self.login_available else
                "Only available in Utter.app (this copy runs from the project folder).")
        page.addSubview_(_label(note, _rect(90, 76, WIDTH - 180, 34), 11,
                                color=AppKit.NSColor.secondaryLabelColor(), align=AppKit.NSTextAlignmentCenter))

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
        for key, (mark, button, hint) in self.rows.items():
            row = self.flow.rows[key]
            if row.granted:
                mark.setStringValue_("✓")
                mark.setTextColor_(AppKit.NSColor.systemGreenColor())
            else:
                mark.setStringValue_(str(PERMISSION_ORDER.index(key) + 1))
                mark.setTextColor_(AppKit.NSColor.labelColor() if key == highlight
                                   else AppKit.NSColor.tertiaryLabelColor())
            button.setTitle_(row.button_title(self._asked_before(key)))
            button.setEnabled_(not row.granted)
            button.setKeyEquivalent_("")
            hint.setHidden_(not row.stuck(now))
        self.try_status.setStringValue_(self.flow.try_status())
        self.try_status.setTextColor_(AppKit.NSColor.systemGreenColor() if self.flow.try_passed
                                      else AppKit.NSColor.secondaryLabelColor())
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
        self.email_field.setHidden_(not fields.email)
        self.code_field.setHidden_(not fields.code)
        self.password_field.setHidden_(not fields.password)
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
            AppKit.NSColor.systemRedColor() if form.error else
            AppKit.NSColor.systemOrangeColor() if (form.suggestion or form.info.startswith("You haven't")) else
            AppKit.NSColor.secondaryLabelColor())
        links = form.links()
        for i, link in enumerate(self.account_links):
            if i < len(links):
                link.setHidden_(False)
                link.setAttributedTitle_(_link_title(links[i][0]))
                link.setEnabled_(not form.busy)
            else:
                link.setHidden_(True)
        if len(links) == 1:  # a single link sits in the middle
            self.account_links[0].setFrame_(_rect((WIDTH - 240) / 2, 56, 240, 22))
        else:
            self.account_links[0].setFrame_(_rect(70, 56, 200, 22))
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
