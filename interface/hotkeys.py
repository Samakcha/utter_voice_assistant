"""Global hotkey manager using pynput with explicit conflict and permission detection."""

import logging
import queue
import threading
import time
from typing import Callable, Optional
from pynput import keyboard

from .key_events import (
    ACTION_CANCEL,
    ACTION_COMMAND,
    ACTION_DICTATION_PRESS,
    ACTION_DICTATION_RELEASE,
    EVENT_FLAGS_CHANGED,
    EVENT_KEY_DOWN,
    EVENT_KEY_UP,
    KeyEventRouter,
    parse_key_combo,
)
from .permissions import check_accessibility_permission, check_input_monitoring_permission

logger = logging.getLogger(__name__)

# While the dictation key is held, check the keyboard's physical state this often, so a
# missed key-up can never leave dictation stuck in hold mode.
WATCHDOG_INTERVAL_SEC = 0.25


def _physical_key_state(keycode: int):
    """(key physically down, current modifier flags) straight from the keyboard hardware state."""
    import Quartz
    state = Quartz.kCGEventSourceStateHIDSystemState
    return bool(Quartz.CGEventSourceKeyState(state, keycode)), int(Quartz.CGEventSourceFlagsState(state))


class HotkeyRegistrationError(Exception):
    """Raised when a hotkey combination cannot be parsed or registered."""
    pass


class HotkeyManager:
    """
    Manages global hotkeys for Command Mode, Dictation Mode, and Session Cancellation (Esc).
    Consolidates all hotkey handlers into a single listener to prevent concurrent TIS/TSM
    access crashes on macOS.
    """

    def __init__(
        self,
        command_hotkey_str: str,
        dictation_hotkey_str: str,
        cancel_hotkey_str: str = "<esc>",
        on_command: Optional[Callable[[], None]] = None,
        on_dictation: Optional[Callable[[], None]] = None,
        on_cancel: Optional[Callable[[], None]] = None,
        is_session_active: Optional[Callable[[], bool]] = None,
        hold_to_talk: bool = False,
        on_dictation_release: Optional[Callable[[float], None]] = None,
        key_state: Optional[Callable[[int], tuple]] = None,
    ):
        self._command_hotkey_str = command_hotkey_str
        self._dictation_hotkey_str = dictation_hotkey_str
        self._cancel_hotkey_str = cancel_hotkey_str
        self._on_command = on_command or (lambda: None)
        self._on_dictation = on_dictation or (lambda: None)
        self._on_cancel = on_cancel or (lambda: None)
        self._is_session_active = is_session_active or (lambda: False)
        self._on_dictation_release = on_dictation_release or (lambda held_sec: None)

        self._enabled: bool = True
        self._hotkey_listener = None

        # Hold-to-talk: one raw key listener (key-down, key-up, modifier changes) instead of
        # GlobalHotKeys, which only reports presses. Actions run on a separate dispatcher
        # thread so the event tap never waits on audio or UI work.
        self.hold_to_talk = bool(hold_to_talk)
        self._key_state = key_state or _physical_key_state
        self._router: Optional[KeyEventRouter] = None
        self._actions: "queue.Queue" = queue.Queue()
        self._dispatcher: Optional[threading.Thread] = None
        self._dispatcher_stop = threading.Event()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = bool(value)
        if self._router is not None:
            self._router.enabled = self._enabled
        logger.info("Hotkeys enabled state set to: %s", self._enabled)

    def start(self) -> None:
        """Register and start listening for global hotkeys on a single listener thread."""
        has_accessibility = self._verify_system_permissions()
        hotkey_bindings = self._build_hotkey_map()

        if self.hold_to_talk:
            if has_accessibility:
                self._start_hold_to_talk_listener()
                return
            # Swallowing keys needs an active event tap, which macOS only allows with
            # Accessibility. Without it, keep hotkeys working the old way (tap to toggle).
            logger.warning(
                "Hold-to-talk needs Accessibility permission; using tap-to-toggle hotkeys instead."
            )
            self.hold_to_talk = False

        try:
            self._hotkey_listener = keyboard.GlobalHotKeys(hotkey_bindings)
            self._hotkey_listener.start()
            logger.info(
                "Registered global hotkeys: Command=%s, Dictation=%s, Cancel=%s",
                self._command_hotkey_str,
                self._dictation_hotkey_str,
                self._cancel_hotkey_str,
            )
        except Exception as e:
            raise HotkeyRegistrationError(
                f"Failed to register global hotkeys ({self._command_hotkey_str}, "
                f"{self._dictation_hotkey_str}, {self._cancel_hotkey_str}): {e}\n"
                "This may indicate a conflict with an existing macOS system shortcut, or an invalid format."
            ) from e

    def stop(self) -> None:
        """Stop hotkey listener."""
        if self._hotkey_listener is not None:
            try:
                self._hotkey_listener.stop()
            except Exception as e:
                logger.debug("Error stopping hotkey listener: %s", e)
            self._hotkey_listener = None

        if self._dispatcher is not None:
            self._dispatcher_stop.set()
            self._actions.put(None)  # wake the dispatcher
            self._dispatcher.join(timeout=1.0)
            self._dispatcher = None

        logger.info("Hotkey manager stopped.")

    # ---- Hold-to-talk listener -------------------------------------------------------

    def build_router(self) -> KeyEventRouter:
        """Parse the three hotkeys into key codes (raises HotkeyRegistrationError)."""
        combos = {}
        for label, text in (
            ("command_mode", self._command_hotkey_str),
            ("dictation_mode", self._dictation_hotkey_str),
            ("cancel", self._cancel_hotkey_str),
        ):
            try:
                combos[label] = parse_key_combo(text)
            except ValueError as e:
                raise HotkeyRegistrationError(
                    f"Invalid syntax for {label} hotkey '{text}' (hold-to-talk listener): {e}. "
                    "Use a letter, digit, F1-F12, space, tab, return or esc, or set "
                    "hold_to_talk = false in config.toml."
                ) from e
        if combos["command_mode"] == combos["dictation_mode"]:
            raise HotkeyRegistrationError("command_mode and dictation_mode hotkeys must differ.")
        router = KeyEventRouter(combos["command_mode"], combos["dictation_mode"], combos["cancel"])
        router.enabled = self._enabled
        return router

    def _start_hold_to_talk_listener(self) -> None:
        self._router = self.build_router()
        self._dispatcher_stop.clear()
        self._dispatcher = threading.Thread(target=self._dispatch_loop, name="hotkey-dispatch", daemon=True)
        self._dispatcher.start()
        try:
            # darwin_intercept makes this an active event tap, so our hotkeys can be kept
            # away from the focused app (no beeps or auto-repeat while holding the keys).
            self._hotkey_listener = keyboard.Listener(darwin_intercept=self._intercept)
            self._hotkey_listener.start()
            logger.info(
                "Registered global hotkeys (hold-to-talk): Command=%s, Dictation=%s (tap to toggle, "
                "hold to talk), Cancel=%s",
                self._command_hotkey_str,
                self._dictation_hotkey_str,
                self._cancel_hotkey_str,
            )
        except Exception as e:
            raise HotkeyRegistrationError(
                f"Failed to start the hold-to-talk key listener: {e}\n"
                "Check Accessibility and Input Monitoring permissions, or set hold_to_talk = false."
            ) from e

    def _intercept(self, event_type, event):
        """
        Runs on the event-tap thread for every key event in the system, so it must be quick
        and must never raise (pynput would stop the listener). Returns None to swallow.
        """
        try:
            if event_type not in (EVENT_KEY_DOWN, EVENT_KEY_UP, EVENT_FLAGS_CHANGED):
                return event
            import Quartz
            keycode = int(Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode))
            flags = int(Quartz.CGEventGetFlags(event))
            is_repeat = bool(Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat))
            swallow, actions = self.route_event(event_type, keycode, flags, is_repeat)
            return None if swallow else event
        except Exception as e:  # never break typing because of a bug here
            logger.error("Hotkey intercept error (event passed through): %s", e)
            return event

    def route_event(self, event_type: int, keycode: int, flags: int, is_repeat: bool,
                    now: Optional[float] = None):
        """Feed one key event to the router and queue its actions. Used by the tap and by tests."""
        router = self._router
        if router is None:
            return False, []
        swallow, actions = router.handle(event_type, keycode, flags, is_repeat,
                                         time.monotonic() if now is None else now)
        for action in actions:
            self._actions.put(action)
        return swallow, actions

    def _dispatch_loop(self) -> None:
        while not self._dispatcher_stop.is_set():
            try:
                item = self._actions.get(timeout=WATCHDOG_INTERVAL_SEC)
            except queue.Empty:
                item = None
            if item is not None:
                self.run_action(item)
            self.poll_watchdog()

    def poll_watchdog(self, now: Optional[float] = None) -> None:
        """While the dictation key is held, release it if the keyboard says it is physically up."""
        router = self._router
        if router is None or not router.dictation_held:
            return
        try:
            key_down, flags = self._key_state(router.dictation.keycode)
        except Exception as e:
            logger.debug("Could not read physical key state: %s", e)
            return
        for action in router.check_watchdog(key_down, flags, time.monotonic() if now is None else now):
            logger.info("Hold-to-talk: key release was missed; releasing via watchdog.")
            self.run_action(action)

    def run_action(self, action) -> None:
        name, value = action
        if name == ACTION_COMMAND:
            self._handle_command_trigger()
        elif name == ACTION_DICTATION_PRESS:
            self._handle_dictation_trigger()
        elif name == ACTION_DICTATION_RELEASE:
            if not self._enabled:
                return
            try:
                self._on_dictation_release(float(value))
            except Exception as e:
                logger.error("Error executing dictation release callback: %s", e, exc_info=True)
        elif name == ACTION_CANCEL:
            self._handle_cancel_trigger()

    def _verify_system_permissions(self) -> bool:
        """Check whether macOS permissions allow global keyboard monitoring. Returns Accessibility state."""
        has_ax = check_accessibility_permission()
        has_input = check_input_monitoring_permission()

        if not has_ax or not has_input:
            msg = (
                "⚠️ Warning: Missing macOS permissions required for global hotkeys:\n"
                + (f"  - Accessibility: {'GRANTED' if has_ax else 'MISSING (System Settings > Privacy & Security > Accessibility)'}\n")
                + (f"  - Input Monitoring: {'GRANTED' if has_input else 'MISSING (System Settings > Privacy & Security > Input Monitoring)'}\n")
                + "Global hotkeys may fail to capture keystrokes until these permissions are enabled."
            )
            logger.warning(msg)
        return bool(has_ax)

    def _build_hotkey_map(self):
        """Parse hotkey definitions from config and associate callbacks."""
        mapping = {}

        try:
            keyboard.HotKey.parse(self._command_hotkey_str)
            mapping[self._command_hotkey_str] = self._handle_command_trigger
        except Exception as e:
            raise HotkeyRegistrationError(
                f"Invalid syntax for command_mode hotkey '{self._command_hotkey_str}': {e}"
            ) from e

        try:
            keyboard.HotKey.parse(self._dictation_hotkey_str)
            mapping[self._dictation_hotkey_str] = self._handle_dictation_trigger
        except Exception as e:
            raise HotkeyRegistrationError(
                f"Invalid syntax for dictation_mode hotkey '{self._dictation_hotkey_str}': {e}"
            ) from e

        try:
            keyboard.HotKey.parse(self._cancel_hotkey_str)
            mapping[self._cancel_hotkey_str] = self._handle_cancel_trigger
        except Exception as e:
            raise HotkeyRegistrationError(
                f"Invalid syntax for cancel hotkey '{self._cancel_hotkey_str}': {e}"
            ) from e

        return mapping

    def _handle_command_trigger(self) -> None:
        if not self._enabled:
            logger.debug("Command hotkey ignored (app is stopped).")
            return
        logger.info("Command hotkey detected (%s)", self._command_hotkey_str)
        try:
            self._on_command()
        except Exception as e:
            logger.error("Error executing command callback: %s", e, exc_info=True)

    def _handle_dictation_trigger(self) -> None:
        if not self._enabled:
            logger.debug("Dictation hotkey ignored (app is stopped).")
            return
        logger.info("Dictation hotkey detected (%s)", self._dictation_hotkey_str)
        try:
            self._on_dictation()
        except Exception as e:
            logger.error("Error executing dictation callback: %s", e, exc_info=True)

    def _handle_cancel_trigger(self) -> None:
        if not self._enabled:
            return

        # Esc strictly acts only while a session is active; never suppresses the key
        if self._is_session_active():
            logger.info("Cancel hotkey detected (%s) while session active; canceling.", self._cancel_hotkey_str)
            try:
                self._on_cancel()
            except Exception as e:
                logger.error("Error executing cancel callback: %s", e, exc_info=True)
