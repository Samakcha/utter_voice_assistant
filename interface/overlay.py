"""
Floating non-activating HUD overlay for voice status and command feedback.
Strictly non-activating: never steals keyboard focus from any running application.
Appears across all macOS Spaces and over full-screen applications.
All AppKit operations run on the main UI thread.
"""

import logging
import threading
from typing import Callable, Optional
import AppKit
import objc

from . import waveform

logger = logging.getLogger(__name__)


def run_on_main_thread(fn, *args):
    """Schedule a callable to run on the macOS main UI thread."""
    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(lambda: fn(*args))


class NonActivatingPanel(AppKit.NSPanel):
    """
    Subclass of NSPanel that explicitly refuses key and main window status.
    Guarantees keyboard focus remains with the user's active application.
    """

    def canBecomeKeyWindow(self) -> bool:
        return False

    def canBecomeMainWindow(self) -> bool:
        return False


class UtterWaveView(AppKit.NSView):
    """Draws the sound wave bars (white, rounded) from `heights` (0..1 each)."""

    def initWithFrame_(self, frame):
        self = objc.super(UtterWaveView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.heights = [0.0] * waveform.BAR_COUNT
        return self

    def drawRect_(self, rect):
        size = self.bounds().size
        AppKit.NSColor.colorWithWhite_alpha_(1.0, 0.95).setFill()
        for x, y, w, h in waveform.bar_rects(self.heights, size.width, size.height):
            AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                ((x, y), (w, h)), w / 2.0, w / 2.0
            ).fill()


class VoiceAssistantOverlay:
    """
    Manages the floating HUD overlay pill at top center of the screen.
    While Utter listens it shows only a moving sound wave (no text). Afterwards it stays
    hidden (no "Transcribing" / "Inserted" / "Heard" messages); only problems that need the
    user's attention are shown as text (unknown app, nothing recognized, note not saved, ...).
    Fades out automatically after final states, and cancels pending timers
    whenever a new state is presented.
    """

    # Compact pills (Wispr Flow style), kept at the top of the screen just under the menu bar
    DEFAULT_HEIGHT = 30.0  # text messages; the wave pill is waveform.HEIGHT (26)
    MIN_WIDTH = 120.0
    MAX_WIDTH = 560.0
    PADDING = 16.0
    TOP_MARGIN = 6.0  # gap below the menu bar
    FONT_SIZE = 12.5
    LABEL_HEIGHT = 17.0

    def __init__(self):
        self.panel: Optional[NonActivatingPanel] = None
        self.effect_view: Optional[AppKit.NSVisualEffectView] = None
        self.label: Optional[AppKit.NSTextField] = None
        self._dismiss_timer: Optional[threading.Timer] = None
        self._timer_lock = threading.Lock()
        self._initialized = False
        self.wave_view = None
        self._wave = waveform.WaveState()
        self._wave_timer = None
        # Microphone loudness 0..1 for the wave; set by the app (session_manager's level)
        self.level_source: Callable[[], float] = lambda: 0.0

        # Build overlay panel on the main thread
        run_on_main_thread(self._init_ui)

    def _init_ui(self) -> None:
        """Initialize panel and subviews on main thread."""
        try:
            rect = AppKit.NSMakeRect(0, 0, self.MIN_WIDTH, self.DEFAULT_HEIGHT)
            self.panel = NonActivatingPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                rect,
                AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel,
                AppKit.NSBackingStoreBuffered,
                False,
            )
            self.panel.setLevel_(AppKit.NSStatusWindowLevel)
            self.panel.setCollectionBehavior_(
                AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
                | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
                | AppKit.NSWindowCollectionBehaviorStationary
            )
            self.panel.setOpaque_(False)
            self.panel.setBackgroundColor_(AppKit.NSColor.clearColor())
            self.panel.setHasShadow_(True)

            # Frosted glass HUD effect view
            self.effect_view = AppKit.NSVisualEffectView.alloc().initWithFrame_(rect)
            self.effect_view.setMaterial_(AppKit.NSVisualEffectMaterialHUDWindow)
            self.effect_view.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
            self.effect_view.setState_(AppKit.NSVisualEffectStateActive)
            self.effect_view.setWantsLayer_(True)
            self.effect_view.layer().setCornerRadius_(self.DEFAULT_HEIGHT / 2.0)
            self.effect_view.layer().setMasksToBounds_(True)
            # Always the dark look, with a hairline edge (also in Light mode)
            self.effect_view.setAppearance_(AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameDarkAqua))
            self.effect_view.layer().setBorderWidth_(0.5)
            self.effect_view.layer().setBorderColor_(
                AppKit.NSColor.colorWithWhite_alpha_(1.0, 0.18).CGColor()
            )

            # Text label
            self.label = AppKit.NSTextField.alloc().initWithFrame_(
                AppKit.NSMakeRect(18, 10, self.MIN_WIDTH - 36, 24)
            )
            self.label.setBezeled_(False)
            self.label.setDrawsBackground_(False)
            self.label.setEditable_(False)
            self.label.setSelectable_(False)
            self.label.setTextColor_(AppKit.NSColor.whiteColor())
            self.label.setFont_(AppKit.NSFont.systemFontOfSize_weight_(self.FONT_SIZE, AppKit.NSFontWeightMedium))
            self.label.setAlignment_(AppKit.NSTextAlignmentCenter)

            self.effect_view.addSubview_(self.label)

            # The sound wave shown while listening (hidden while a message is shown)
            self.wave_view = UtterWaveView.alloc().initWithFrame_(
                AppKit.NSMakeRect(0, 0, waveform.WIDTH, waveform.HEIGHT)
            )
            self.wave_view.setHidden_(True)
            self.effect_view.addSubview_(self.wave_view)
            self.panel.setContentView_(self.effect_view)
            self._initialized = True
        except Exception as e:
            logger.error("Failed to initialize HUD overlay: %s", e, exc_info=True)
            self.panel = None

    def _cancel_dismiss_timer(self) -> None:
        """Cancel any pending auto-dismiss timer to prevent old timers hiding new states."""
        with self._timer_lock:
            if self._dismiss_timer is not None:
                self._dismiss_timer.cancel()
                self._dismiss_timer = None

    def _show_content(self, text: str, auto_dismiss_sec: Optional[float] = None) -> None:
        """Internal helper to configure text, resize, reposition, and display the panel."""
        self._cancel_dismiss_timer()

        def _update():
            if self.panel is None or not self._initialized:
                return

            try:
                self._stop_wave()
                self.wave_view.setHidden_(True)
                self.label.setHidden_(False)
                # Cancel any ongoing animator transitions and restore full opacity
                self.panel.setAlphaValue_(1.0)
                self.label.setStringValue_(text)

                # Measure text width to size the pill dynamically
                self.label.sizeToFit()
                text_width = self.label.frame().size.width
                pill_width = max(self.MIN_WIDTH, min(text_width + self.PADDING * 2, self.MAX_WIDTH))
                pill_height = self.DEFAULT_HEIGHT
                self._place(pill_width, pill_height)
                label_rect = AppKit.NSMakeRect(
                    self.PADDING, (pill_height - self.LABEL_HEIGHT) / 2.0,
                    pill_width - (self.PADDING * 2), self.LABEL_HEIGHT,
                )
                self.label.setFrame_(label_rect)

                # Show on screen without activating or stealing focus
                self.panel.orderFrontRegardless()
            except Exception as e:
                logger.error("Error updating overlay UI: %s", e, exc_info=True)

        run_on_main_thread(_update)

        if auto_dismiss_sec is not None:
            def _schedule():
                self._animate_fade_out()

            with self._timer_lock:
                self._dismiss_timer = threading.Timer(auto_dismiss_sec, _schedule)
                self._dismiss_timer.daemon = True
                self._dismiss_timer.start()

    def _animate_fade_out(self) -> None:
        """Animate alpha to 0.0 over 0.25s and order out."""
        def _fade():
            if self.panel is None:
                return
            try:
                AppKit.NSAnimationContext.beginGrouping()
                ctx = AppKit.NSAnimationContext.currentContext()
                ctx.setDuration_(0.25)

                def _completion():
                    if self.panel is not None:
                        self.panel.orderOut_(None)
                        self.panel.setAlphaValue_(1.0)

                ctx.setCompletionHandler_(_completion)
                self.panel.animator().setAlphaValue_(0.0)
                AppKit.NSAnimationContext.endGrouping()
            except Exception as e:
                logger.debug("Error during overlay fade out: %s", e)
                if self.panel is not None:
                    self.panel.orderOut_(None)

        run_on_main_thread(_fade)

    # ---- While listening: the moving sound wave ---------------------------------------------

    def show_listening(self, mode_name: str = "Command") -> None:
        """Command, dictation or note: the same moving sound wave, no text."""
        self._show_wave()

    def show_note_listening(self) -> None:
        self._show_wave()

    def _show_wave(self) -> None:
        self._cancel_dismiss_timer()

        def _update():
            if self.panel is None or not self._initialized or self.wave_view is None:
                return
            try:
                self.panel.setAlphaValue_(1.0)
                self.label.setHidden_(True)
                self._wave.reset()
                self.wave_view.heights = self._wave.heights()
                self.wave_view.setHidden_(False)
                self._place(waveform.WIDTH, waveform.HEIGHT)
                self.wave_view.setFrame_(AppKit.NSMakeRect(0, 0, waveform.WIDTH, waveform.HEIGHT))
                self.wave_view.setNeedsDisplay_(True)
                self.panel.orderFrontRegardless()
                self._start_wave()
            except Exception as e:
                logger.error("Error showing the sound wave: %s", e, exc_info=True)

        run_on_main_thread(_update)

    def _place(self, width: float, height: float) -> None:
        """Size the pill and put it at the top centre of the main screen (main thread)."""
        screen = AppKit.NSScreen.mainScreen() or AppKit.NSScreen.screens()[0]
        frame = screen.visibleFrame()
        x = frame.origin.x + (frame.size.width - width) / 2.0
        y = frame.origin.y + frame.size.height - height - self.TOP_MARGIN
        self.panel.setFrame_display_(AppKit.NSMakeRect(x, y, width, height), True)
        self.effect_view.setFrame_(AppKit.NSMakeRect(0, 0, width, height))
        self.effect_view.layer().setCornerRadius_(height / 2.0)

    def _wave_tick(self, _timer=None) -> None:
        try:
            level = float(self.level_source())
        except Exception:
            level = 0.0
        if self.wave_view is not None:
            self.wave_view.heights = self._wave.step(level)
            self.wave_view.setNeedsDisplay_(True)

    def _start_wave(self) -> None:
        """Redraw ~24 times a second (main thread); also while a menu is open."""
        if self._wave_timer is None:
            self._wave_timer = AppKit.NSTimer.timerWithTimeInterval_repeats_block_(
                1.0 / waveform.FRAMES_PER_SEC, True, self._wave_tick
            )
            AppKit.NSRunLoop.mainRunLoop().addTimer_forMode_(self._wave_timer, AppKit.NSRunLoopCommonModes)

    def _stop_wave(self) -> None:
        if self._wave_timer is not None:
            self._wave_timer.invalidate()
            self._wave_timer = None

    # ---- No progress or success messages: the pop-up just goes away ------------------------

    def show_transcribing(self) -> None:
        self.hide()

    def show_action_start(self, heard_text: str = "", action_message: str = "") -> None:
        self.hide()

    def show_action_success(self, heard_text: str = "", action_label: str = "") -> None:
        self.hide()

    def show_dictation_inserted(self, word_count: int = 0) -> None:
        self.hide()

    def show_saving_note(self) -> None:
        self.hide()

    def show_note_saved(self, word_count: int = 0) -> None:
        self.hide()

    def show_whatsapp_opening(self) -> None:
        self.hide()

    def show_whatsapp_ready(self) -> None:
        self.hide()

    def show_whatsapp_searching(self, name: str = "") -> None:
        self.hide()

    # ---- Problems that need attention: shown as text ----------------------------------------

    def show_unrecognized(self, heard_text: str) -> None:
        """Display unrecognized utterance feedback; auto-fades after ~2.5s."""
        clean_heard = heard_text.strip().replace("\n", " ")
        if len(clean_heard) > 28:
            clean_heard = clean_heard[:25] + "..."
        self._show_content(f'Heard: "{clean_heard}" → Nothing recognized', auto_dismiss_sec=2.5)

    def show_not_run(self, message: str) -> None:
        """A command that was understood but deliberately not run (e.g. unknown app); ~3.5s."""
        self._show_content(f"🤔 {message}", auto_dismiss_sec=3.5)

    def show_error(self, message: str) -> None:
        """Display action failure or error state; auto-fades after ~2.5s."""
        self._show_content(f"⚠️ Action failed: {message}", auto_dismiss_sec=2.5)

    def show_dictation_empty(self) -> None:
        """Display dictation empty feedback: 'Nothing to insert'; auto-fades after ~2.0s."""
        self._show_content("Nothing to insert", auto_dismiss_sec=2.0)

    def show_note_empty(self) -> None:
        """Display empty note feedback: 'Nothing to save'; auto-fades after ~2.0s."""
        self._show_content("Nothing to save", auto_dismiss_sec=2.0)

    def show_note_failed_copied(self, reason: str) -> None:
        """Display failure message: 'Couldn't save note: <reason>. Text copied to clipboard.'; auto-fades after 3.5s."""
        self._show_content(f"⚠️ Couldn't save note: {reason}. Text copied to clipboard.", auto_dismiss_sec=3.5)

    def show_whatsapp_search_success(self, name: Optional[str] = None) -> None:
        """Display guidance message after search paste; stays about 4.0s."""
        if name:
            text = (
                f"Searching WhatsApp for {name}\n"
                f"Pick the chat, click the message box, then press Control+Option+D to dictate. Press Enter to send."
            )
        else:
            text = (
                "Pick the chat, click the message box, then press Control+Option+D to dictate. Press Enter to send."
            )
        self._show_content(text, auto_dismiss_sec=4.0)

    def show_whatsapp_not_front(self) -> None:
        """Display warning when WhatsApp is not frontmost; auto-fades after ~3.0s."""
        self._show_content("⚠️ WhatsApp isn't in front. Say 'open WhatsApp' first.", auto_dismiss_sec=3.0)

    def show_whatsapp_focus_failed(self) -> None:
        """Display error when search field cannot be focused; auto-fades after ~3.0s."""
        self._show_content("⚠️ Couldn't focus WhatsApp search", auto_dismiss_sec=3.0)

    def show_whatsapp_ui_unreadable(self) -> None:
        """Display error when WhatsApp UI tree cannot be read; auto-fades after ~3.5s."""
        self._show_content("⚠️ WhatsApp's UI isn't readable. Try restarting WhatsApp.", auto_dismiss_sec=3.5)

    def show_whatsapp_open_failed(self, reason: str) -> None:
        """Display error when WhatsApp cannot be launched; auto-fades after ~3.0s."""
        self._show_content(f"⚠️ Couldn't open WhatsApp: {reason}", auto_dismiss_sec=3.0)

    def show_whatsapp_unresponsive(self) -> None:
        """Display error when WhatsApp AX messaging times out; auto-fades after ~3.0s."""
        self._show_content("⚠️ Couldn't reach WhatsApp", auto_dismiss_sec=3.0)

    def hide(self) -> None:
        """Hide overlay immediately (e.g. on Esc cancellation)."""
        self._cancel_dismiss_timer()

        def _do_hide():
            self._stop_wave()
            if self.panel is not None:
                self.panel.orderOut_(None)
                self.panel.setAlphaValue_(1.0)

        run_on_main_thread(_do_hide)
