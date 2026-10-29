"""Global hotkey manager using pynput with explicit conflict and permission detection."""

import logging
from typing import Callable, Optional
from pynput import keyboard

from .permissions import check_accessibility_permission, check_input_monitoring_permission

logger = logging.getLogger(__name__)


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
    ):
        self._command_hotkey_str = command_hotkey_str
        self._dictation_hotkey_str = dictation_hotkey_str
        self._cancel_hotkey_str = cancel_hotkey_str
        self._on_command = on_command or (lambda: None)
        self._on_dictation = on_dictation or (lambda: None)
        self._on_cancel = on_cancel or (lambda: None)
        self._is_session_active = is_session_active or (lambda: False)

        self._enabled: bool = True
        self._hotkey_listener: Optional[keyboard.GlobalHotKeys] = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = bool(value)
        logger.info("Hotkeys enabled state set to: %s", self._enabled)

    def start(self) -> None:
        """Register and start listening for global hotkeys on a single listener thread."""
        self._verify_system_permissions()
        hotkey_bindings = self._build_hotkey_map()

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

        logger.info("Hotkey manager stopped.")

    def _verify_system_permissions(self) -> None:
        """Check whether macOS permissions allow global keyboard monitoring."""
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
