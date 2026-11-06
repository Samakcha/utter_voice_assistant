"""
Pure key-event logic for the hold-to-talk hotkey listener (no Quartz calls here).

Hotkeys are matched by physical key code, not by the typed character: holding Option
changes what "D" types ("∂"), but its key code stays the same. Key codes are the
standard macOS ANSI codes, so letters mean their position on a US (QWERTY) keyboard.

The router sees every keyboard event from one event tap and decides:
- which app actions to run (command, dictation press/release, cancel), and
- whether to swallow the event so the focused app never sees our hotkey (no beeps,
  no stray characters, no auto-repeat while the dictation key is held).
Esc is never swallowed; it still reaches the focused app.
"""

from dataclasses import dataclass
import threading
from typing import List, Optional, Tuple

# Quartz event types (CGEventType)
EVENT_KEY_DOWN = 10
EVENT_KEY_UP = 11
EVENT_FLAGS_CHANGED = 12

# Quartz modifier flag masks (CGEventFlags)
FLAG_SHIFT = 0x20000
FLAG_CONTROL = 0x40000
FLAG_OPTION = 0x80000
FLAG_COMMAND = 0x100000
_MODIFIER_MASK = FLAG_SHIFT | FLAG_CONTROL | FLAG_OPTION | FLAG_COMMAND

_MODIFIER_NAMES = {
    "ctrl": FLAG_CONTROL, "ctrl_l": FLAG_CONTROL, "ctrl_r": FLAG_CONTROL, "control": FLAG_CONTROL,
    "alt": FLAG_OPTION, "alt_l": FLAG_OPTION, "alt_r": FLAG_OPTION, "option": FLAG_OPTION,
    "alt_gr": FLAG_OPTION,
    "cmd": FLAG_COMMAND, "cmd_l": FLAG_COMMAND, "cmd_r": FLAG_COMMAND, "command": FLAG_COMMAND,
    "shift": FLAG_SHIFT, "shift_l": FLAG_SHIFT, "shift_r": FLAG_SHIFT,
}

# macOS virtual key codes (kVK_ANSI_* and kVK_*)
KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
    "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25, "7": 26,
    "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35,
    "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44,
    "n": 45, "m": 46, ".": 47, "`": 50,
    "enter": 36, "return": 36, "tab": 48, "space": 49, "backspace": 51, "esc": 53, "escape": 53,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100,
    "f9": 101, "f10": 109, "f11": 103, "f12": 111,
}

# Action names produced by the router
ACTION_COMMAND = "command"
ACTION_DICTATION_PRESS = "dictation_press"
ACTION_DICTATION_RELEASE = "dictation_release"
ACTION_CANCEL = "cancel"


@dataclass(frozen=True)
class KeyCombo:
    """A hotkey as a physical key code plus the exact modifiers that must be held."""
    keycode: int
    modifiers: int  # bitmask of FLAG_* values

    def matches(self, keycode: int, flags: int) -> bool:
        return keycode == self.keycode and (flags & _MODIFIER_MASK) == self.modifiers

    def modifiers_held(self, flags: int) -> bool:
        """True while every required modifier is still down (extra ones don't matter)."""
        return (flags & self.modifiers) == self.modifiers


def parse_key_combo(hotkey: str) -> KeyCombo:
    """
    Parse a pynput-style hotkey string ("<ctrl>+<alt>+d", "<esc>") into a KeyCombo.
    Raises ValueError for unknown keys or a combo without exactly one non-modifier key.
    """
    if not hotkey or not hotkey.strip():
        raise ValueError("empty hotkey")
    modifiers = 0
    key: Optional[str] = None
    for raw in hotkey.split("+"):
        token = raw.strip().lower()
        if token.startswith("<") and token.endswith(">"):
            token = token[1:-1]
        if not token:
            raise ValueError(f"empty part in hotkey '{hotkey}'")
        if token in _MODIFIER_NAMES:
            modifiers |= _MODIFIER_NAMES[token]
            continue
        if key is not None:
            raise ValueError(f"hotkey '{hotkey}' has more than one non-modifier key")
        if token not in KEYCODES:
            raise ValueError(f"key '{token}' in hotkey '{hotkey}' is not supported")
        key = token
    if key is None:
        raise ValueError(f"hotkey '{hotkey}' has no key besides modifiers")
    return KeyCombo(keycode=KEYCODES[key], modifiers=modifiers)


Action = Tuple[str, float]  # (action name, value: press time or held seconds)


class KeyEventRouter:
    """
    State machine for the hotkeys. Thread-safe: events arrive on the event-tap thread,
    watchdog checks on another thread.

    Dictation key states: idle -> held (key-down) -> idle (key-up of the key, release of a
    required modifier, or the watchdog seeing the keys physically up). Auto-repeat
    key-downs while held are swallowed and ignored.
    """

    def __init__(self, command: KeyCombo, dictation: KeyCombo, cancel: KeyCombo):
        self.command = command
        self.dictation = dictation
        self.cancel = cancel
        self.enabled = True
        self._lock = threading.Lock()
        self._dictation_held = False
        self._dictation_press_time = 0.0
        self._watchdog_misses = 0
        # Key codes whose key-down we swallowed; their key-up is swallowed too, even if
        # the modifiers were let go first (otherwise the app would see a lone key-up).
        self._swallowed: set = set()

    @property
    def dictation_held(self) -> bool:
        with self._lock:
            return self._dictation_held

    def handle(
        self, event_type: int, keycode: int, flags: int, is_repeat: bool, now: float
    ) -> Tuple[bool, List[Action]]:
        """Process one keyboard event. Returns (swallow_event, actions_to_run)."""
        with self._lock:
            actions: List[Action] = []

            if event_type == EVENT_KEY_DOWN:
                if is_repeat and keycode in self._swallowed:
                    # Still the same physical press of our hotkey: keep its auto-repeat away
                    # from the app even if a modifier was let go first (Ctrl+D deletes text).
                    return True, actions
                if not self.enabled:
                    return False, actions
                if self.dictation.matches(keycode, flags):
                    if not is_repeat and not self._dictation_held:
                        self._dictation_held = True
                        self._dictation_press_time = now
                        self._watchdog_misses = 0
                        actions.append((ACTION_DICTATION_PRESS, now))
                    self._swallowed.add(keycode)
                    return True, actions
                if self.command.matches(keycode, flags):
                    if not is_repeat:
                        actions.append((ACTION_COMMAND, now))
                    self._swallowed.add(keycode)
                    return True, actions
                if self.cancel.matches(keycode, flags):
                    if not is_repeat:
                        actions.append((ACTION_CANCEL, now))
                    return False, actions  # Esc always reaches the focused app
                return False, actions

            if event_type == EVENT_KEY_UP:
                if keycode == self.dictation.keycode and self._dictation_held:
                    actions.append(self._release(now))
                swallow = keycode in self._swallowed
                self._swallowed.discard(keycode)
                return swallow, actions

            if event_type == EVENT_FLAGS_CHANGED:
                if self._dictation_held and not self.dictation.modifiers_held(flags):
                    actions.append(self._release(now))
                return False, actions  # modifier changes are never swallowed

            return False, actions

    def check_watchdog(self, key_down: bool, flags: int, now: float) -> List[Action]:
        """
        Called periodically with the keyboard's physical state while the dictation key is
        held. If the keys are physically up on two checks in a row, a key-up was missed
        (for example the event tap was paused), so release anyway.
        """
        with self._lock:
            if not self._dictation_held:
                self._watchdog_misses = 0
                return []
            if key_down and self.dictation.modifiers_held(flags):
                self._watchdog_misses = 0
                return []
            self._watchdog_misses += 1
            if self._watchdog_misses < 2:
                return []
            self._swallowed.discard(self.dictation.keycode)
            return [self._release(now)]

    def _release(self, now: float) -> Action:
        """Must hold the lock. Ends the current hold and reports how long it lasted."""
        self._dictation_held = False
        self._watchdog_misses = 0
        return (ACTION_DICTATION_RELEASE, max(0.0, now - self._dictation_press_time))


def is_hold(held_sec: float, min_hold_sec: float) -> bool:
    """A press held at least this long is hold-to-talk (release stops); shorter is a tap (toggle)."""
    return held_sec >= min_hold_sec
