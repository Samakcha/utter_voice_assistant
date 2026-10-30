"""
Floating non-activating HUD overlay for voice status and command feedback.
Strictly non-activating: never steals keyboard focus from any running application.
Appears across all macOS Spaces and over full-screen applications.
All AppKit operations run on the main UI thread.
"""

import logging
import threading
from typing import Optional
import AppKit

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


class VoiceAssistantOverlay:
    """
    Manages the floating HUD overlay pill at top center of the screen.
    Handles listening, transcribing, recognized action, and error states.
    Fades out automatically after final states, and cancels pending timers
    whenever a new state is presented.
    """

    DEFAULT_HEIGHT = 44.0
    MIN_WIDTH = 260.0
    MAX_WIDTH = 680.0
    PADDING = 36.0

    def __init__(self):
        self.panel: Optional[NonActivatingPanel] = None
        self.effect_view: Optional[AppKit.NSVisualEffectView] = None
        self.label: Optional[AppKit.NSTextField] = None
        self._dismiss_timer: Optional[threading.Timer] = None
        self._timer_lock = threading.Lock()
        self._initialized = False

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

            # Text label
            self.label = AppKit.NSTextField.alloc().initWithFrame_(
                AppKit.NSMakeRect(18, 10, self.MIN_WIDTH - 36, 24)
            )
            self.label.setBezeled_(False)
            self.label.setDrawsBackground_(False)
            self.label.setEditable_(False)
            self.label.setSelectable_(False)
            self.label.setTextColor_(AppKit.NSColor.whiteColor())
            self.label.setFont_(AppKit.NSFont.systemFontOfSize_weight_(13.5, AppKit.NSFontWeightMedium))
            self.label.setAlignment_(AppKit.NSTextAlignmentCenter)

            self.effect_view.addSubview_(self.label)
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
                # Cancel any ongoing animator transitions and restore full opacity
                self.panel.setAlphaValue_(1.0)
                self.label.setStringValue_(text)

                # Measure text width to size the pill dynamically
                self.label.sizeToFit()
                text_width = self.label.frame().size.width
                pill_width = max(self.MIN_WIDTH, min(text_width + self.PADDING * 2, self.MAX_WIDTH))
                pill_height = self.DEFAULT_HEIGHT

                # Position at top center of main display
                screen = AppKit.NSScreen.mainScreen() or AppKit.NSScreen.screens()[0]
                screen_frame = screen.visibleFrame()

                x = screen_frame.origin.x + (screen_frame.size.width - pill_width) / 2.0
                y = screen_frame.origin.y + screen_frame.size.height - pill_height - 35.0

                pill_rect = AppKit.NSMakeRect(x, y, pill_width, pill_height)
                self.panel.setFrame_display_(pill_rect, True)

                # Update subview frames
                content_rect = AppKit.NSMakeRect(0, 0, pill_width, pill_height)
                self.effect_view.setFrame_(content_rect)
                self.effect_view.layer().setCornerRadius_(pill_height / 2.0)

                label_rect = AppKit.NSMakeRect(self.PADDING, (pill_height - 22.0) / 2.0, pill_width - (self.PADDING * 2), 22.0)
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

    def show_listening(self, mode_name: str = "Command") -> None:
        """Display listening state."""
        icon = "🔴" if mode_name.lower() == "command" else "🔵"
        self._show_content(f"{icon} Listening ({mode_name.capitalize()})...", auto_dismiss_sec=None)

    def show_transcribing(self) -> None:
        """Display transcribing state."""
        self._show_content("⏳ Transcribing...", auto_dismiss_sec=None)

    def show_action_start(self, heard_text: str, action_message: str = "Opening Notes...") -> None:
        """Display action starting state."""
        clean_heard = heard_text.strip().replace("\n", " ")
        if len(clean_heard) > 28:
            clean_heard = clean_heard[:25] + "..."
        self._show_content(f'Heard: "{clean_heard}" → {action_message}', auto_dismiss_sec=None)

    def show_action_success(self, heard_text: str, action_label: str = "Opening Notes") -> None:
        """Display recognized and successfully initiated action; auto-fades after ~2.5s."""
        clean_heard = heard_text.strip().replace("\n", " ")
        if len(clean_heard) > 28:
            clean_heard = clean_heard[:25] + "..."
        self._show_content(f'Heard: "{clean_heard}" → {action_label}', auto_dismiss_sec=2.5)

    def show_unrecognized(self, heard_text: str) -> None:
        """Display unrecognized utterance feedback; auto-fades after ~2.5s."""
        clean_heard = heard_text.strip().replace("\n", " ")
        if len(clean_heard) > 28:
            clean_heard = clean_heard[:25] + "..."
        self._show_content(f'Heard: "{clean_heard}" → Nothing recognized', auto_dismiss_sec=2.5)

    def show_error(self, message: str) -> None:
        """Display action failure or error state; auto-fades after ~2.5s."""
        self._show_content(f"⚠️ Action failed: {message}", auto_dismiss_sec=2.5)

    def show_dictation_inserted(self, word_count: int) -> None:
        """Display dictation completion: 'Inserted N words'; auto-fades after ~2.0s."""
        noun = "word" if word_count == 1 else "words"
        self._show_content(f"✍️ Inserted {word_count} {noun}", auto_dismiss_sec=2.0)

    def show_dictation_empty(self) -> None:
        """Display dictation empty feedback: 'Nothing to insert'; auto-fades after ~2.0s."""
        self._show_content("Nothing to insert", auto_dismiss_sec=2.0)

    def hide(self) -> None:
        """Hide overlay immediately (e.g. on Esc cancellation)."""
        self._cancel_dismiss_timer()

        def _do_hide():
            if self.panel is not None:
                self.panel.orderOut_(None)
                self.panel.setAlphaValue_(1.0)

        run_on_main_thread(_do_hide)
