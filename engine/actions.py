"""
Action registry and execution engine for local voice commands.
Pure engine component with NO UI code.
"""

from dataclasses import dataclass
from enum import Enum
import logging
import subprocess
import threading
import time
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)


class ActionRisk(str, Enum):
    """Risk tier for voice actions."""
    LOW = "low"      # Runs immediately without user confirmation (e.g. opening an app)
    HIGH = "high"    # Requires explicit confirmation before execution (e.g. sending a message)


@dataclass(frozen=True)
class ActionResult:
    """Result of executing an action."""
    success: bool
    action_id: str
    message: str
    error: Optional[str] = None
    execution_time_sec: float = 0.0


@dataclass(frozen=True)
class ActionDefinition:
    """Metadata and execution handler for a registered action."""
    id: str
    label: str
    risk: ActionRisk
    handler: Callable[[float], ActionResult]


def _open_notes_handler(timeout_sec: float = 5.0) -> ActionResult:
    """
    Open the macOS Notes app via `open -a Notes`.
    Brings Notes to the front if already running, or launches it if closed.
    Avoids AppleScript to prevent requiring macOS Automation permissions in v1.
    """
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            ["open", "-a", "Notes"],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        t1 = time.perf_counter()
        elapsed = t1 - t0

        if proc.returncode == 0:
            logger.info("Successfully opened Notes app in %.3fs.", elapsed)
            return ActionResult(
                success=True,
                action_id="open_notes",
                message="Opening Notes",
                execution_time_sec=elapsed,
            )
        else:
            stderr_msg = proc.stderr.strip() or f"Process exited with code {proc.returncode}"
            logger.error("Failed to open Notes app: %s", stderr_msg)
            return ActionResult(
                success=False,
                action_id="open_notes",
                message="Failed to open Notes",
                error=stderr_msg,
                execution_time_sec=elapsed,
            )
    except subprocess.TimeoutExpired:
        elapsed = time.perf_counter() - t0
        logger.error("Timed out opening Notes app after %.1fs.", timeout_sec)
        return ActionResult(
            success=False,
            action_id="open_notes",
            message="Timed out opening Notes",
            error=f"Execution exceeded {timeout_sec}s timeout",
            execution_time_sec=elapsed,
        )
    except Exception as e:
        elapsed = time.perf_counter() - t0
        logger.error("Error executing open Notes action: %s", e, exc_info=True)
        return ActionResult(
            success=False,
            action_id="open_notes",
            message="Error opening Notes",
            error=str(e),
            execution_time_sec=elapsed,
        )


def _take_note_handler(timeout_sec: float = 5.0) -> ActionResult:
    """Action definition handler for initiating voice note-taking session."""
    return ActionResult(
        success=True,
        action_id="take_note",
        message="Taking a note...",
        execution_time_sec=0.0,
    )


# Fixed registry of supported actions
ACTION_REGISTRY: Dict[str, ActionDefinition] = {
    "open_notes": ActionDefinition(
        id="open_notes",
        label="Open Notes",
        risk=ActionRisk.LOW,
        handler=_open_notes_handler,
    ),
    "take_note": ActionDefinition(
        id="take_note",
        label="Take Note",
        risk=ActionRisk.LOW,
        handler=_take_note_handler,
    ),
}


def get_action(action_id: str) -> Optional[ActionDefinition]:
    """Retrieve an action definition from the registry."""
    return ACTION_REGISTRY.get(action_id)


def execute_action(
    action_id: str,
    confirmed: bool = False,
    timeout_sec: float = 5.0,
) -> ActionResult:
    """
    Synchronously execute an action by id.
    Low-risk actions execute immediately.
    High-risk actions require confirmed=True.
    """
    action = get_action(action_id)
    if action is None:
        logger.error("Unknown action ID requested: '%s'", action_id)
        return ActionResult(
            success=False,
            action_id=action_id,
            message=f"Unknown action: {action_id}",
            error="Action not found in registry",
        )

    # Plumbing for future high-risk confirmation check
    if action.risk == ActionRisk.HIGH and not confirmed:
        logger.warning("Action '%s' is high-risk but was not confirmed.", action_id)
        return ActionResult(
            success=False,
            action_id=action_id,
            message=f"Confirmation required for '{action.label}'",
            error="High-risk action requires user confirmation",
        )

    return action.handler(timeout_sec)


def execute_action_async(
    action_id: str,
    callback: Callable[[ActionResult], None],
    confirmed: bool = False,
    timeout_sec: float = 5.0,
) -> threading.Thread:
    """
    Asynchronously execute an action on a dedicated worker thread with timeout.
    Guarantees the main UI thread is NEVER blocked by subprocess calls.
    """
    def _worker():
        result = execute_action(action_id, confirmed=confirmed, timeout_sec=timeout_sec)
        callback(result)

    thread = threading.Thread(
        target=_worker,
        name=f"action-worker-{action_id}",
        daemon=True,
    )
    thread.start()
    return thread
