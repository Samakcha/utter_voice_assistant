"""
The Settings window (menu bar → Settings…): a sidebar of pages, each a list of grouped
rows, laid out like macOS System Settings and drawn in Utter's own look (always dark, purple,
handwritten page titles: interface/welcome_style.py, shared with the welcome window). What each row means and allows lives in
interface/settings_model.py; this file only draws it. Main thread only.

Every change is saved the moment it is made (switches, menus, a recorded shortcut, a text
field when you press Return or leave it). The word list and app names have a Save button,
and are also saved when the window closes.
"""

import logging
from typing import Callable, Dict, List, Optional, Tuple

import AppKit

from . import settings_model as sm
from . import welcome_style as ws
from .settings_model import SettingsModel
from .setup_flow import format_hotkey
from .welcome_window import UtterSetupWindow, _Action, _Delegate, _label, _rect, _shared_app

logger = logging.getLogger(__name__)

WIDTH, HEIGHT = 760.0, 540.0
SIDEBAR = 200.0
PAD = 28.0
ROW_PAD = 12.0
CONTROL_W = 190.0
STATUS_H = 34.0
TITLEBAR_H = 30.0


class _FlippedView(AppKit.NSView):
    """Top-to-bottom coordinates, so pages are laid out like a document and can scroll."""

    def isFlipped(self):
        return True


class _KeyRecorder:
    """Catches the next key press in the window while a shortcut is being recorded."""

    def __init__(self, callback):
        self.callback = callback
        self.monitor = None

    def start(self):
        if self.monitor is None:
            self.monitor = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                AppKit.NSEventMaskKeyDown, self.handle)

    def handle(self, event):
        try:
            self.callback(int(event.keyCode()), int(event.modifierFlags()))
        except Exception as e:
            logger.error("Shortcut recording failed: %s", e, exc_info=True)
        return None  # the key press is used up here

    def stop(self):
        if self.monitor is not None:
            AppKit.NSEvent.removeMonitor_(self.monitor)
            self.monitor = None


def _symbol(name: str, size: float = 14.0, box: float = 22.0):
    """An SF Symbol centred in a fixed square, so every sidebar title starts at the same place."""
    try:
        image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    except AttributeError:
        return None
    if image is None:
        return None
    config = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(size, AppKit.NSFontWeightRegular)
    symbol = image.imageWithSymbolConfiguration_(config)
    w, h = symbol.size().width, symbol.size().height

    def draw(rect):
        symbol.drawInRect_(_rect((box - w) / 2, (box - h) / 2, w, h))
        return True

    fixed = AppKit.NSImage.imageWithSize_flipped_drawingHandler_(AppKit.NSMakeSize(box, box), False, draw)
    fixed.setTemplate_(True)
    return fixed


def _text_height(field, width: float) -> float:
    field.setPreferredMaxLayoutWidth_(width)
    return float(field.fittingSize().height)


class SettingsWindow:
    """
    model: SettingsModel (the settings and how to change them).
    Callbacks from the app (all optional):
      pause_hotkeys() / resume_hotkeys()  while a shortcut is being recorded
      login_status() -> "on"|"off"|"needs_approval"|"unavailable"; set_login(on); open_login_settings()
      account_email() -> str ("" = not signed in, None = accounts off); sign_in(); sign_out()
      show_setup(); check_permissions(); open_recordings(); open_log(); open_settings_file()
      restart()  (None: can't restart this copy, e.g. from the terminal)
      models() -> [(model_size, title)]; active_model() -> the model Utter runs now
      version: shown on the General page
    """

    def __init__(self, model: SettingsModel, *, pause_hotkeys=lambda: None, resume_hotkeys=lambda: None,
                 login_status=lambda: "unavailable", set_login=lambda on: None,
                 open_login_settings=lambda: None, account_email=lambda: None,
                 sign_in=lambda: None, sign_out=lambda: None, show_setup=lambda: None,
                 check_permissions=lambda: None, open_recordings=lambda: None, open_log=lambda: None,
                 open_settings_file=lambda: None, restart: Optional[Callable[[], None]] = None,
                 models=lambda: list(sm.MODELS), active_model=lambda: "", version: str = "",
                 closed=lambda: None, page: str = sm.GENERAL):
        self.model = model
        self._cb = dict(pause_hotkeys=pause_hotkeys, resume_hotkeys=resume_hotkeys,
                        login_status=login_status, set_login=set_login,
                        open_login_settings=open_login_settings, account_email=account_email,
                        sign_in=sign_in, sign_out=sign_out, show_setup=show_setup,
                        check_permissions=check_permissions, open_recordings=open_recordings,
                        open_log=open_log, open_settings_file=open_settings_file,
                        models=models, active_model=active_model, closed=closed)
        self._restart = restart
        self.version = version
        self.page = page
        self._targets: List[object] = []
        self.toggles: Dict[Tuple[str, str], object] = {}  # (section, key) -> ws.Switch
        self.choices: Dict[Tuple[str, str], object] = {}
        self.shortcut_buttons: Dict[str, object] = {}
        self.text_fields: Dict[str, object] = {}
        self.text_areas: Dict[str, object] = {}
        self.buttons: Dict[str, object] = {}
        self.sidebar_buttons: Dict[str, object] = {}
        self.recording: Optional[str] = None  # which shortcut is being recorded
        self._recorder = _KeyRecorder(self._key_pressed)
        self._saved_areas: Dict[str, str] = {}
        self.message = ""
        self.message_is_error = False
        self._build()

    # ---- Building -------------------------------------------------------------------------

    def _target(self, callback):
        target = _Action.alloc().initWithCallback_(callback)
        self._targets.append(target)
        return target

    def _build(self) -> None:
        style = (AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable
                 | AppKit.NSWindowStyleMaskMiniaturizable | AppKit.NSWindowStyleMaskFullSizeContentView)
        self.window = UtterSetupWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            _rect(0, 0, WIDTH, HEIGHT), style, AppKit.NSBackingStoreBuffered, False)
        self.window.setTitle_("Utter Settings")
        self.window.setTitlebarAppearsTransparent_(True)
        self.window.setTitleVisibility_(AppKit.NSWindowTitleHidden)
        self.window.setAppearance_(AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameDarkAqua))
        self.window.setBackgroundColor_(ws.BACKGROUND)
        self.window.setReleasedWhenClosed_(False)
        self._delegate = _Delegate.alloc().initWithCallback_(self._on_close)
        self.window.setDelegate_(self._delegate)
        content = self.window.contentView()

        sidebar = ws.SidebarBackdrop.alloc().initWithFrame_(_rect(0, 0, SIDEBAR, HEIGHT))
        content.addSubview_(sidebar)
        sidebar.addSubview_(ws.icon_tile(_rect(14, HEIGHT - 96, 48, 48)))
        sidebar.addSubview_(ws.hand_title([("Utter", None)], _rect(62, HEIGHT - 98, SIDEBAR - 70, 50), 34,
                                          align="left"))

        self._selection = ws.Selection.alloc().initWithFrame_(_rect(12, 0, SIDEBAR - 24, 32))
        sidebar.addSubview_(self._selection)
        y = HEIGHT - 128
        for page, title, symbol in sm.PAGES:
            y -= 36
            button = AppKit.NSButton.alloc().initWithFrame_(_rect(12, y, SIDEBAR - 24, 32))
            button.setBordered_(False)
            button.setTitle_("  " + title)
            button.setAlignment_(AppKit.NSTextAlignmentLeft)
            button.setFont_(ws.ui_font(13.5))
            image = _symbol(symbol)
            if image is not None:
                image.setTemplate_(True)
                button.setImage_(image)
                button.setImagePosition_(AppKit.NSImageLeft)
            button.setTarget_(self._target(lambda p=page: self.show_page(p)))
            button.setAction_("fire:")
            sidebar.addSubview_(button)
            self.sidebar_buttons[page] = button

        right = SIDEBAR
        # below the (transparent) title bar; macOS's own automatic inset for it only applied
        # to the first page shown, so the pages started at different heights
        self._scroll = AppKit.NSScrollView.alloc().initWithFrame_(
            _rect(right, STATUS_H + 1, WIDTH - right, HEIGHT - STATUS_H - 1 - TITLEBAR_H))
        self._scroll.setAutomaticallyAdjustsContentInsets_(False)
        self._scroll.setHasVerticalScroller_(True)
        self._scroll.setAutohidesScrollers_(True)
        self._scroll.setDrawsBackground_(False)
        self._scroll.setBorderType_(AppKit.NSNoBorder)
        content.addSubview_(self._scroll)
        content.addSubview_(ws.divider(_rect(right, STATUS_H, WIDTH - right, 1)))
        self._status = ws.text_label("", _rect(right + PAD, 9, WIDTH - right - 2 * PAD, 18), ws.ui_font(12.5),
                                     ws.TEXT_DIM)
        content.addSubview_(self._status)
        self.show_page(self.page)

    def show_page(self, page: str) -> None:
        self._stop_recording()
        if getattr(self, "_doc", None) is not None:
            self.window.makeFirstResponder_(None)  # a text field being edited is saved first
        self._save_text_areas()
        self.page = page
        self.toggles, self.choices, self.shortcut_buttons = {}, {}, {}
        self.text_fields, self.text_areas, self.buttons = {}, {}, {}
        for name, button in self.sidebar_buttons.items():
            selected = name == page
            color = ws.TEXT if selected else ws.TEXT_DIM
            title = button.title()
            button.setAttributedTitle_(AppKit.NSAttributedString.alloc().initWithString_attributes_(
                title, {AppKit.NSForegroundColorAttributeName: color,
                        AppKit.NSFontAttributeName: ws.ui_font(13.5, AppKit.NSFontWeightSemibold if selected
                                                               else AppKit.NSFontWeightRegular)}))
            button.setContentTintColor_(color)
            if selected:
                self._selection.setFrame_(button.frame())
        width = WIDTH - SIDEBAR
        doc = _FlippedView.alloc().initWithFrame_(_rect(0, 0, width, 100))
        self._doc = doc
        self._y = 4.0
        self._width = width - 2 * PAD
        title = dict((p, t) for p, t, _ in sm.PAGES)[page]
        doc.addSubview_(ws.hand_title([(title, "swash")], _rect(PAD - 2, self._y - 4, self._width, 58), 42,
                                      align="left"))
        self._y += 62
        getattr(self, f"_page_{page}")()
        visible = float(self._scroll.contentSize().height)
        doc.setFrameSize_(AppKit.NSMakeSize(width, max(self._y + PAD, visible)))
        self._scroll.setDocumentView_(doc)
        # every page starts at its top (the document is flipped: y = 0 is the top)
        self._scroll.contentView().scrollToPoint_(AppKit.NSMakePoint(0, 0))
        self._scroll.reflectScrolledClipView_(self._scroll.contentView())
        self.say("")

    # ---- Rows -----------------------------------------------------------------------------

    def _group(self, rows: List[Callable[[float], Tuple[object, float]]], header: str = "") -> None:
        """A rounded card of rows; each row builder gets the row width and returns (view, height)."""
        if header:
            self._doc.addSubview_(_label(header, _rect(PAD + 4, self._y, self._width, 18), size=12,
                                         weight=AppKit.NSFontWeightSemibold, color=ws.TEXT_DIM))
            self._y += 22
        built = [row(self._width) for row in rows]
        height = sum(h for _, h in built)
        box = ws.card(_rect(PAD, self._y, self._width, height), radius=14)
        inner = _FlippedView.alloc().initWithFrame_(_rect(0, 0, self._width, height))
        box.addSubview_(inner)
        y = 0.0
        for index, (view, h) in enumerate(built):
            view.setFrameOrigin_(AppKit.NSMakePoint(0, y))
            inner.addSubview_(view)
            if index:
                inner.addSubview_(ws.divider(_rect(ROW_PAD + 2, y, self._width - 2 * ROW_PAD - 4, 1)))
            y += h
        self._doc.addSubview_(box)
        self._y += height + 18

    def _texts(self, row, title: str, detail: str, width: float):
        """Title and grey detail on the left of a row; returns the row height."""
        t = _label(title, _rect(ROW_PAD, 0, width, 18), size=13.5, color=ws.TEXT)
        th = _text_height(t, width)
        h = ROW_PAD + th
        d = None
        if detail:
            d = _label(detail, _rect(ROW_PAD, 0, width, 16), size=11.5, color=ws.TEXT_DIM)
            dh = _text_height(d, width)
            h += 2 + dh
        h = max(h + ROW_PAD, 48.0)
        top = (h - (th + (2 + dh if d else 0))) / 2
        t.setFrame_(_rect(ROW_PAD, top, width, th))
        row.addSubview_(t)
        if d is not None:
            d.setFrame_(_rect(ROW_PAD, top + th + 2, width, dh))
            row.addSubview_(d)
        return h

    def _row_with(self, title: str, detail: str, control, control_w: float, control_h: float = 24.0):
        def build(width):
            row = _FlippedView.alloc().initWithFrame_(_rect(0, 0, width, 44))
            h = self._texts(row, title, detail, width - control_w - 3 * ROW_PAD)
            row.setFrameSize_(AppKit.NSMakeSize(width, h))
            control.setFrame_(_rect(width - ROW_PAD - control_w, (h - control_h) / 2, control_w, control_h))
            row.addSubview_(control)
            return row, h
        return build

    def _toggle(self, toggle: sm.Toggle):
        switch = ws.switch(_rect(0, 0, 44, 26), sm.toggle_value(self.model.config, toggle.section, toggle.key),
                           self._target(lambda: self._toggled(toggle)))
        self.toggles[(toggle.section, toggle.key)] = switch
        return self._row_with(toggle.title, toggle.detail, switch, 44, 26)

    def _choice(self, choice: sm.Choice):
        popup = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(_rect(0, 0, 150, 26), False)
        for title, _ in sm.choice_options(choice, self.model.config):
            popup.addItemWithTitle_(title)
        popup.selectItemAtIndex_(sm.choice_index(choice, self.model.config))
        popup.setTarget_(self._target(lambda: self._chose(choice)))
        popup.setAction_("fire:")
        self.choices[(choice.section, choice.key)] = popup
        return self._row_with(choice.title, choice.detail, popup, 150, 26)

    def _action_button(self, name: str, title: str, callback, primary: bool = False):
        button = ws.pill_button(title, _rect(0, 0, 150, 32), self._target(callback), style="ghost")
        if primary:
            button.setKeyEquivalent_("\r")
        self.buttons[name] = button
        return button

    def _button_row(self, name: str, title: str, detail: str, button_title: str, callback, width: float = 150):
        button = self._action_button(name, button_title, callback)
        return self._row_with(title, detail, button, width, 32)

    def _text_row(self, name: str, title: str, detail: str, value: str, placeholder: str, on_commit):
        box = ws.card(_rect(0, 0, 240, 32), radius=10, fill=ws.rgb(255, 255, 255, 0.06),
                      edge=ws.rgb(255, 255, 255, 0.14))
        field = AppKit.NSTextField.alloc().initWithFrame_(_rect(10, 7, 220, 18))
        field.setAutoresizingMask_(AppKit.NSViewWidthSizable)
        field.setBezeled_(False)
        field.setBordered_(False)
        field.setDrawsBackground_(False)
        field.setFocusRingType_(AppKit.NSFocusRingTypeNone)
        field.setFont_(ws.ui_font(13.5))
        field.setTextColor_(ws.TEXT)
        box.addSubview_(field)
        field.setStringValue_(value)
        field.setPlaceholderString_(placeholder)
        field.cell().setSendsActionOnEndEditing_(True)
        field.cell().setScrollable_(True)
        field.setTarget_(self._target(lambda: self._text_committed(name, on_commit)))
        field.setAction_("fire:")
        self.text_fields[name] = field
        self._field_values = getattr(self, "_field_values", {})
        self._field_values[name] = value
        return self._row_with(title, detail, box, 240, 32)

    def _text_area(self, name: str, title: str, detail: str, value: str, on_save, height: float = 140):
        def build(width):
            row = _FlippedView.alloc().initWithFrame_(_rect(0, 0, width, 100))
            th = self._texts(row, title, detail, width - 2 * ROW_PAD - 120)
            box = ws.card(_rect(ROW_PAD, th, width - 2 * ROW_PAD, height), radius=10,
                          fill=ws.rgb(255, 255, 255, 0.05), edge=ws.rgb(255, 255, 255, 0.14))
            scroll = AppKit.NSScrollView.alloc().initWithFrame_(_rect(1, 1, width - 2 * ROW_PAD - 2, height - 2))
            scroll.setHasVerticalScroller_(True)
            scroll.setAutohidesScrollers_(True)
            scroll.setBorderType_(AppKit.NSNoBorder)
            scroll.setDrawsBackground_(False)
            text = AppKit.NSTextView.alloc().initWithFrame_(_rect(0, 0, width - 2 * ROW_PAD - 6, height - 2))
            text.setDrawsBackground_(False)
            text.setTextColor_(ws.TEXT)
            text.setInsertionPointColor_(ws.LAVENDER)
            text.setFont_(ws.ui_font(13.5))
            text.setRichText_(False)
            text.setAutomaticQuoteSubstitutionEnabled_(False)
            text.setAutomaticSpellingCorrectionEnabled_(False)
            text.setAutomaticTextReplacementEnabled_(False)
            text.setAutomaticDashSubstitutionEnabled_(False)
            text.setTextContainerInset_(AppKit.NSMakeSize(6, 8))
            text.setString_(value)
            text.setTextColor_(ws.TEXT)
            text.setVerticallyResizable_(True)
            text.textContainer().setWidthTracksTextView_(True)
            scroll.setDocumentView_(text)
            box.addSubview_(scroll)
            row.addSubview_(box)
            save = self._action_button(f"save_{name}", "Save", lambda: self._save_area(name, on_save))
            save.setFrame_(_rect(width - ROW_PAD - 90, (th - 32) / 2, 90, 32))
            row.addSubview_(save)
            self.text_areas[name] = (text, on_save)
            self._saved_areas[name] = value
            h = th + height + ROW_PAD
            row.setFrameSize_(AppKit.NSMakeSize(width, h))
            return row, h
        return build

    def _shortcut(self, which: str, title: str, detail: str):
        button = ws.pill_button(format_hotkey(getattr(self.model.config.hotkeys, which)), _rect(0, 0, 130, 34),
                                self._target(lambda: self.record_shortcut(which)), style="key")
        self.shortcut_buttons[which] = button
        return self._row_with(title, detail, button, 130, 34)

    def _info(self, title: str, detail: str, value: str):
        label = _label(value, _rect(0, 0, 150, 18), size=13.5, color=ws.TEXT_DIM, align=AppKit.NSTextAlignmentRight)
        return self._row_with(title, detail, label, 150, 18)

    def _find(self, items, section, key):
        return next(i for i in items if (i.section, i.key) == (section, key))

    def _t(self, section, key):
        return self._toggle(self._find(sm.TOGGLES, section, key))

    def _c(self, section, key):
        return self._choice(self._find(sm.CHOICES, section, key))

    # ---- Pages ------------------------------------------------------------------------------

    def _page_general(self) -> None:
        email = self._cb["account_email"]()
        if email is not None:
            if email:
                self._group([self._button_row("sign_out", "Utter account", f"Signed in as {email}.",
                                              "Sign Out…", self._cb["sign_out"])])
            else:
                self._group([self._button_row("sign_in", "Utter account", "You are not signed in.",
                                              "Sign In…", self._cb["sign_in"])])
        status = self._cb["login_status"]()
        if status == "unavailable":
            login = self._info("Start Utter when you log in",
                               "Works in Utter.app (this copy runs from the project folder).", "Not available")
        elif status == "needs_approval":
            login = self._button_row("login_approve", "Start Utter when you log in",
                                     "Waiting for your OK in System Settings → General → Login Items.",
                                     "Open Settings", self._cb["open_login_settings"])
        else:
            switch = ws.switch(_rect(0, 0, 44, 26), status == "on", self._target(self._login_toggled))
            self.toggles[("general", "start_at_login")] = switch
            login = self._row_with("Start Utter when you log in",
                                   "Utter waits in the menu bar, ready for your shortcuts.", switch, 44, 26)
        self._group([login])
        self._group([
            self._button_row("permissions", "Permissions",
                             "Microphone, Accessibility and Input Monitoring.", "Check…",
                             self._cb["check_permissions"]),
            self._button_row("setup", "Welcome window", "The first-start guide, with a place to try Utter.",
                             "Show…", self._cb["show_setup"]),
        ])
        if self.version:
            self._doc.addSubview_(_label(f"Utter {self.version}", _rect(PAD + 4, self._y, self._width, 16),
                                         size=11, color=ws.TEXT_FAINT))
            self._y += 20

    def _page_shortcuts(self) -> None:
        self._group([
            self._shortcut("command_mode", "Commands", "“Open notes”, “take a note”, “open Safari”…"),
            self._shortcut("dictation_mode", "Dictation", "Speak, and the text is typed where your cursor is."),
            self._info("Stop listening", "Stops at once; nothing is typed or run.", "Esc"),
        ])
        self._doc.addSubview_(_label("Click a shortcut, then press the new keys (with ⌃ Control or ⌥ Option).",
                                     _rect(PAD + 4, self._y - 12, self._width, 16), size=11,
                                     color=ws.TEXT_DIM))
        self._y += 12
        self._group([self._t("hotkeys", "hold_to_talk"), self._c("timeouts", "command_silence_sec")])

    def _page_dictation(self) -> None:
        self._group([self._t("cleanup", "enabled"), self._t("cleanup", "fillers"),
                     self._t("cleanup", "repeats"), self._t("cleanup", "corrections")])
        self._group([
            self._c("timeouts", "dictation_silence_sec"),
            self._text_row("dictation_stop", "Stop phrases", "Say one at the end to stop. Separate with commas.",
                           sm.phrases_text(self.model.config.dictation.stop_phrases), "stop dictation",
                           lambda text: self.model.set_phrases("dictation", text)),
            self._t("dictation", "trailing_space"),
        ])
        self._update_dependent()

    def _page_notes(self) -> None:
        notes = self.model.config.notes
        self._doc.addSubview_(_label("Say “take a note” with the command shortcut, then speak your note.",
                                     _rect(PAD + 4, self._y - 8, self._width, 16), size=12,
                                     color=ws.TEXT_DIM))
        self._y += 16
        self._group([
            self._text_row("notes_folder", "Notes folder", "Leave empty for the default folder.",
                           notes.folder, "Notes", self.model.set_notes_folder),
            self._text_row("note_title", "Note title", "{date} and {time} fill in the date and time.",
                           notes.title_template, "Voice note {date} {time}", self.model.set_note_title),
        ])
        self._group([
            self._c("notes", "silence_sec"),
            self._text_row("notes_stop", "Stop phrases", "Say one at the end to save. Separate with commas.",
                           sm.phrases_text(notes.stop_phrases), "stop note",
                           lambda text: self.model.set_phrases("notes", text)),
            self._t("notes", "reveal_after_save"),
            self._t("notes", "ready_sound"),
        ])

    def _page_words(self) -> None:
        config = self.model.config
        words = "\n".join(sm.word_list_terms(config.vocabulary.file))
        self._group([
            self._t("vocabulary", "enabled"),
            self._t("vocabulary", "auto_correct"),
            self._text_area("words", "Your words", "One name or term per line, spelled the way you want it.",
                            words, self.model.save_word_list, height=110),
        ])
        self._group([
            self._t("apps", "enabled"),
            self._text_area("aliases", "Your names for apps",
                            "One per line: what you say = the app's name, like “browser = Safari”.",
                            sm.aliases_text(config.apps.aliases), self.model.set_aliases, height=70),
        ])
        self._update_dependent()

    def _page_privacy(self) -> None:
        self._doc.addSubview_(_label("Your voice and text never leave this Mac. Only your email is used, to sign in.",
                                     _rect(PAD + 4, self._y - 8, self._width, 16), size=12,
                                     color=ws.TEXT_DIM))
        self._y += 16
        self._group([self._t("privacy", "keep_recordings"), self._t("privacy", "keep_whatsapp_audio"),
                     self._t("privacy", "keep_audio_when_whatsapp_front")])
        self._group([self._t("privacy", "redact_names_in_logs")])
        self._group([
            self._button_row("recordings", "Recordings folder", "Where kept recordings go.", "Show in Finder",
                             self._cb["open_recordings"]),
            self._button_row("log", "Log", "Timings and errors (never what you said).", "Open Log",
                             self._cb["open_log"]),
        ])
        self._update_dependent()

    def _page_advanced(self) -> None:
        models = self._cb["models"]()
        popup = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(_rect(0, 0, 220, 26), False)
        for _, title in models:
            popup.addItemWithTitle_(title)
        sizes = [size for size, _ in models]
        current = self.model.config.whisper.model_size
        if current in sizes:
            popup.selectItemAtIndex_(sizes.index(current))
        popup.setTarget_(self._target(lambda: self._model_chosen(sizes)))
        popup.setAction_("fire:")
        self.choices[("whisper", "model_size")] = popup
        rows = [self._row_with("Speech model", "Turns your voice into text. Runs on this Mac.", popup, 220, 26)]
        if self._needs_restart():
            rows.append(self._button_row("restart", "Restart Utter to switch models",
                                         "The new model loads when Utter starts again.",
                                         "Restart Now", self._restart_now) if self._restart is not None
                        else self._info("Restart Utter to switch models",
                                        "The new model loads when Utter starts again.", ""))
        self._group(rows)
        self._group([self._button_row("settings_file", "Settings file",
                                      "Every setting, with explanations, including technical ones not shown "
                                      "here. Restart Utter after editing it by hand.",
                                      "Open File", self._cb["open_settings_file"])])

    # ---- Doing ------------------------------------------------------------------------------

    def say(self, message: str, error: bool = False) -> None:
        self.message, self.message_is_error = message, error
        self._status.setStringValue_(message)
        self._status.setTextColor_(ws.RED if error else ws.TEXT_DIM)

    def _result(self, problem: Optional[str], saved: str = "Saved.") -> bool:
        if problem:
            self.say(problem, error=True)
            return False
        self.say(saved)
        return True

    def _toggled(self, toggle: sm.Toggle) -> None:
        switch = self.toggles[(toggle.section, toggle.key)]
        on = switch.state() == AppKit.NSControlStateValueOn
        if not self._result(self.model.set_toggle(toggle, on)):
            switch.setState_(AppKit.NSControlStateValueOff if on else AppKit.NSControlStateValueOn)
        self._update_dependent()

    def _update_dependent(self) -> None:
        for toggle in sm.TOGGLES:
            if toggle.depends_on and (toggle.section, toggle.key) in self.toggles:
                parent_on = sm.toggle_value(self.model.config, *toggle.depends_on)
                self.toggles[(toggle.section, toggle.key)].setEnabled_(parent_on)
        if "words" in self.text_areas:
            self.text_areas["words"][0].setEditable_(self.model.config.vocabulary.file is not None)

    def _chose(self, choice: sm.Choice) -> None:
        popup = self.choices[(choice.section, choice.key)]
        self._result(self.model.set_choice(choice, int(popup.indexOfSelectedItem())))

    def _login_toggled(self) -> None:
        switch = self.toggles[("general", "start_at_login")]
        self._cb["set_login"](switch.state() == AppKit.NSControlStateValueOn)
        status = self._cb["login_status"]()
        switch.setState_(AppKit.NSControlStateValueOn if status == "on" else AppKit.NSControlStateValueOff)
        if status == "needs_approval":
            self.show_page(sm.GENERAL)
            self.say("Approve Utter in System Settings → General → Login Items.")

    def _model_chosen(self, sizes: List[str]) -> None:
        popup = self.choices[("whisper", "model_size")]
        index = int(popup.indexOfSelectedItem())
        if 0 <= index < len(sizes) and sizes[index] != self.model.config.whisper.model_size:
            if self._result(self.model.set_model(sizes[index])):
                self.show_page(sm.ADVANCED)
                if self._needs_restart():
                    self.say("Saved. Restart Utter to use the new model.")

    def _needs_restart(self) -> bool:
        active = self._cb["active_model"]()
        return bool(active) and active != self.model.config.whisper.model_size

    def _restart_now(self) -> None:
        self._save_text_areas()
        self._restart()

    def _text_committed(self, name: str, on_commit) -> None:
        if name not in self.text_fields:
            return
        value = str(self.text_fields[name].stringValue())
        if value == self._field_values.get(name):
            return
        if self._result(on_commit(value)):
            self._field_values[name] = value

    def _save_area(self, name: str, on_save) -> bool:
        text = str(self.text_areas[name][0].string())
        ok = self._result(on_save(text))
        if ok:
            self._saved_areas[name] = text
        return ok

    def _save_text_areas(self) -> None:
        """Typed into the word list or app names and didn't press Save: save anyway."""
        for name, (view, on_save) in list(self.text_areas.items()):
            if str(view.string()) != self._saved_areas.get(name):
                self._save_area(name, on_save)

    # ---- Recording a shortcut ------------------------------------------------------------------

    def record_shortcut(self, which: str) -> None:
        if self.recording == which:
            self._stop_recording()
            return
        self._stop_recording()
        self.recording = which
        self.shortcut_buttons[which].setTitle_("Press keys…")
        self._cb["pause_hotkeys"]()  # so pressing the current shortcut doesn't start Utter
        self._recorder.start()
        self.say("Press the new shortcut, or Esc to keep the old one.")

    def _key_pressed(self, keycode: int, flags: int) -> None:
        which = self.recording
        if which is None:
            return
        if keycode == sm.ESC_KEYCODE and not flags & (sm.FLAG_CONTROL | sm.FLAG_OPTION | sm.FLAG_COMMAND):
            self._stop_recording()
            self.say("")
            return
        problem = self.model.set_shortcut(which, sm.combo_from_key(keycode, flags))
        if problem:
            self.say(problem, error=True)  # keep listening for another try
            return
        self._stop_recording()
        self.say(f"Saved. {format_hotkey(getattr(self.model.config.hotkeys, which))} now starts "
                 f"{sm.SHORTCUT_NAMES[which]}.")

    def _stop_recording(self) -> None:
        if self.recording is None:
            return
        which, self.recording = self.recording, None
        self._recorder.stop()
        if which in self.shortcut_buttons:
            self.shortcut_buttons[which].setTitle_(format_hotkey(getattr(self.model.config.hotkeys, which)))
        self._cb["resume_hotkeys"]()

    # ---- Window ---------------------------------------------------------------------------------

    def show(self, page: Optional[str] = None) -> None:
        if page is not None and page != self.page:
            self.show_page(page)
        if not self.window.isVisible():
            self.window.center()
        app = _shared_app()
        try:
            app.activateIgnoringOtherApps_(True)
        except AttributeError:
            app.activate()
        self.window.makeKeyAndOrderFront_(None)
        self.window.orderFrontRegardless()

    def refresh(self) -> None:
        """Settings changed elsewhere (sign-in, start at login): redraw the page."""
        if self.is_visible:
            message, error = self.message, self.message_is_error
            self.show_page(self.page)
            self.say(message, error)

    def close(self) -> None:
        self.window.close()

    def _on_close(self) -> None:
        self._stop_recording()
        # A text field still being edited: commit it, then the text areas
        self.window.makeFirstResponder_(None)
        self._save_text_areas()
        try:
            self._cb["closed"]()
        except Exception as e:
            logger.debug("Settings close callback failed: %s", e)

    @property
    def is_visible(self) -> bool:
        return bool(self.window.isVisible())

