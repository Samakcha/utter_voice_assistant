"""
The first-run welcome ("Setup…"): its steps and what each one shows, without any window.

  1. Welcome      what Utter does, and its two hotkeys
  2. Permissions  Microphone, Accessibility, Input Monitoring, one row each; every row ticks
                  itself (✓) as soon as macOS reports it on (checked every second). A row that
                  stays off after its button was used shows how to fix a stuck entry.
  3. Try it       press the dictation hotkey and speak into a box in the window
  4. Finish       Start at login (on by default), then Utter keeps running in the menu bar

Shown automatically once (the first time Utter.app opens); afterwards only from the menu.
Plain logic here (testable); interface/welcome_window.py draws it.
"""

from dataclasses import dataclass
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

SETUP_FILE_NAME = "setup.json"
STEPS = ["welcome", "permissions", "try", "finish"]
PERMISSION_ORDER = ["microphone", "accessibility", "input_monitoring"]
STUCK_AFTER_SEC = 12.0  # a row still off this long after its button: show the fix
TRY_HELP_AFTER_SEC = 20.0  # nothing typed this long on the Try step: offer a restart
START_AT_LOGIN_DEFAULT = True

BUNDLE_ID = "com.utter.mac"
# tccutil's names for the three lists in System Settings > Privacy & Security
TCC_SERVICES = {"microphone": "Microphone", "accessibility": "Accessibility", "input_monitoring": "ListenEvent"}

PERMISSION_TEXT = {
    "microphone": ("Microphone", "Hears you only while you use a hotkey."),
    "accessibility": ("Accessibility", "Types your dictation and opens apps for you."),
    "input_monitoring": ("Input Monitoring", "Notices the hotkeys in any app."),
}

_KEY_SYMBOLS = {"<ctrl>": "⌃", "<alt>": "⌥", "<shift>": "⇧", "<cmd>": "⌘",
                "<esc>": "Esc", "<space>": "Space", "<tab>": "Tab", "<enter>": "Return"}


def format_hotkey(text: str) -> str:
    """'<ctrl>+<alt>+m' -> '⌃⌥M' (how Mac menus write shortcuts)."""
    parts = [p.strip().lower() for p in text.split("+") if p.strip()]
    mods = "".join(_KEY_SYMBOLS[p] for p in parts if p in ("<ctrl>", "<alt>", "<shift>", "<cmd>"))
    keys = [p for p in parts if p not in ("<ctrl>", "<alt>", "<shift>", "<cmd>")]
    key = " ".join(_KEY_SYMBOLS.get(k, k.strip("<>").upper()) for k in keys)
    return mods + key


def reset_command(key: str) -> str:
    """The Terminal command that clears a stuck permission entry (the user runs it)."""
    return f"tccutil reset {TCC_SERVICES[key]} {BUNDLE_ID}"


# ---- Remembering that setup was shown ----------------------------------------------------

def read_record(record_file: Path) -> Dict:
    try:
        data = json.loads(record_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def setup_needed(record_file: Path) -> bool:
    return not read_record(record_file).get("done", False)


def resume_step(record_file: Path) -> int:
    """Where to reopen after "Restart Utter" in the middle of setup (else the beginning)."""
    step = read_record(record_file).get("resume")
    return STEPS.index(step) if step in STEPS else 0


def write_record(record_file: Path, **fields) -> None:
    data = read_record(record_file)
    data.update(fields)
    try:
        record_file.parent.mkdir(parents=True, exist_ok=True)
        record_file.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    except OSError as e:
        logger.warning("Could not save the setup state: %s", e)


def mark_setup_done(record_file: Path, start_at_login: Optional[bool] = None) -> None:
    fields = {"done": True, "resume": None}
    if start_at_login is not None:
        fields["start_at_login"] = bool(start_at_login)
    write_record(record_file, **fields)


# ---- Permission rows ---------------------------------------------------------------------

@dataclass
class PermissionRow:
    key: str
    granted: bool = False
    asked_at: Optional[float] = None  # when its button was last used

    @property
    def name(self) -> str:
        return PERMISSION_TEXT[self.key][0]

    @property
    def why(self) -> str:
        return PERMISSION_TEXT[self.key][1]

    def button_title(self, asked_before: bool) -> str:
        if self.granted:
            return "On"
        return "Open Settings" if (asked_before or self.asked_at is not None) else "Turn On"

    def stuck(self, now: float) -> bool:
        return (not self.granted and self.asked_at is not None
                and now - self.asked_at >= STUCK_AFTER_SEC)

    def stuck_hint(self) -> str:
        return (f"Already on for Utter but no ✓? Select Utter in the list, remove it with −, "
                f"then press Open Settings again. Or run in Terminal: {reset_command(self.key)}")


class SetupFlow:
    """State of the welcome window. `now` is passed in (seconds), so tests control time."""

    def __init__(self, start_step: int = 0, start_at_login: bool = START_AT_LOGIN_DEFAULT):
        self.step = max(0, min(start_step, len(STEPS) - 1))
        self.rows: Dict[str, PermissionRow] = {k: PermissionRow(k) for k in PERMISSION_ORDER}
        self.start_at_login = start_at_login
        self.try_text = ""
        self.try_started_at: Optional[float] = None
        self._hotkeys_ready = False

    # Steps
    @property
    def step_name(self) -> str:
        return STEPS[self.step]

    def next(self, now: float = 0.0) -> str:
        self.step = min(self.step + 1, len(STEPS) - 1)
        if self.step_name == "try" and self.try_started_at is None:
            self.try_started_at = now
        return self.step_name

    def back(self) -> str:
        self.step = max(self.step - 1, 0)
        return self.step_name

    def go_to(self, name: str, now: float = 0.0) -> str:
        self.step = STEPS.index(name)
        if name == "try" and self.try_started_at is None:
            self.try_started_at = now
        return name

    @property
    def is_last(self) -> bool:
        return self.step == len(STEPS) - 1

    # Permissions
    def update_permissions(self, granted: Dict[str, bool]) -> List[str]:
        """Take macOS's current answers; returns the keys that just turned on."""
        turned_on = []
        for key, row in self.rows.items():
            now_on = bool(granted.get(key, row.granted))
            if now_on and not row.granted:
                turned_on.append(key)
            row.granted = now_on
        return turned_on

    def mark_asked(self, key: str, now: float) -> None:
        self.rows[key].asked_at = now

    @property
    def all_granted(self) -> bool:
        return all(r.granted for r in self.rows.values())

    @property
    def missing(self) -> List[str]:
        return [r.name for r in self.rows.values() if not r.granted]

    def next_permission(self) -> Optional[str]:
        """The first permission still off (its row is highlighted)."""
        return next((k for k in PERMISSION_ORDER if not self.rows[k].granted), None)

    def hotkeys_became_ready(self) -> bool:
        """True once, when Accessibility and Input Monitoring are both on: the hotkeys
        should then be started again (they could not listen without them)."""
        ready = self.rows["accessibility"].granted and self.rows["input_monitoring"].granted
        if ready and not self._hotkeys_ready:
            self._hotkeys_ready = True
            return True
        self._hotkeys_ready = ready
        return False

    def continue_title(self) -> str:
        name = self.step_name
        if name == "welcome":
            return "Get Started"
        if name == "permissions":
            return "Continue" if self.all_granted else "Skip for Now"
        if name == "try":
            return "Continue" if self.try_passed else "Skip"
        return "Start Using Utter"

    # Try it
    def set_try_text(self, text: str) -> bool:
        """The text in the Try box changed; True when it now shows that dictation works."""
        before = self.try_passed
        self.try_text = text or ""
        return self.try_passed and not before

    @property
    def try_passed(self) -> bool:
        return bool(self.try_text.strip())

    def try_needs_help(self, now: float) -> bool:
        return (self.step_name == "try" and not self.try_passed and self.try_started_at is not None
                and now - self.try_started_at >= TRY_HELP_AFTER_SEC)

    def try_status(self) -> str:
        if self.try_passed:
            return "✓ It works! Utter typed that for you."
        if not self.all_granted:
            return f"Still off: {', '.join(self.missing)}. Dictation may not work until it's on."
        return "Waiting for you to speak…"
