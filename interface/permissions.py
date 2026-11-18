"""macOS permissions detection and guidance for Microphone, Accessibility, and Input Monitoring."""

from dataclasses import dataclass, replace
import json
import logging
from pathlib import Path
import subprocess
import sys
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PermissionItem:
    name: str
    granted: bool
    settings_path: str
    deep_link_url: str
    purpose: str


def check_microphone_permission() -> bool:
    """Check if the current process has been granted microphone access."""
    try:
        import AVFoundation
        # AVAuthorizationStatus: 0=NotDetermined, 1=Restricted, 2=Denied, 3=Authorized
        status = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(
            AVFoundation.AVMediaTypeAudio
        )
        return status == 3
    except Exception as e:
        logger.debug("Failed to query AVFoundation microphone authorization: %s", e)
        return False


def check_accessibility_permission() -> bool:
    """Check if the process is trusted for Accessibility (event taps / hotkeys)."""
    try:
        from ApplicationServices import AXIsProcessTrusted
        return bool(AXIsProcessTrusted())
    except Exception as e:
        logger.debug("Failed to query AXIsProcessTrusted: %s", e)
        return False


def check_input_monitoring_permission() -> bool:
    """Check if the process has Input Monitoring (CGEventTap listen-only) permission."""
    try:
        import Quartz
        if hasattr(Quartz, "CGPreflightListenEventAccess"):
            return bool(Quartz.CGPreflightListenEventAccess())
        return True
    except Exception as e:
        logger.debug("Failed to query CGPreflightListenEventAccess: %s", e)
        return False


def get_all_permissions() -> Dict[str, PermissionItem]:
    """Check all three required macOS permissions."""
    return {
        "microphone": PermissionItem(
            name="Microphone",
            granted=check_microphone_permission(),
            settings_path="System Settings > Privacy & Security > Microphone",
            deep_link_url="x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
            purpose="Required to capture voice audio for VAD and transcription.",
        ),
        "accessibility": PermissionItem(
            name="Accessibility",
            granted=check_accessibility_permission(),
            settings_path="System Settings > Privacy & Security > Accessibility",
            deep_link_url="x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
            purpose="Required for system-wide global hotkeys and future app control.",
        ),
        "input_monitoring": PermissionItem(
            name="Input Monitoring",
            granted=check_input_monitoring_permission(),
            settings_path="System Settings > Privacy & Security > Input Monitoring",
            deep_link_url="x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
            purpose="Required by pynput to listen for global hotkeys without suppressing them.",
        ),
    }


def open_settings_pane(pane_name: str) -> bool:
    """Open the specific macOS System Settings pane."""
    permissions = get_all_permissions()
    item = permissions.get(pane_name.lower())
    if not item:
        return False

    try:
        subprocess.run(["open", item.deep_link_url], check=False)
        return True
    except Exception as e:
        logger.error("Failed to open System Settings pane %s: %s", item.settings_path, e)
        return False


def request_missing_permissions(permissions: Optional[Dict[str, PermissionItem]] = None) -> List[str]:
    """
    Ask macOS for each missing permission, the way apps must: this shows macOS's own prompt
    and puts Utter in the right list in System Settings (the Microphone list has no + button,
    so an app only appears there after it asks). Returns the names asked for.
      Microphone        "Utter would like to access the microphone" (Allow / Don't Allow)
      Accessibility     "Utter would like to control this computer..." (Open System Settings)
      Input Monitoring  "Keystroke receiving" prompt (Open System Settings)
    A permission already refused once is not asked again by macOS; it stays in the list, off.
    """
    permissions = permissions if permissions is not None else get_all_permissions()
    asked: List[str] = []
    if not permissions["microphone"].granted:
        try:
            import AVFoundation
            status = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(AVFoundation.AVMediaTypeAudio)
            if status == 0:  # not asked yet
                AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
                    AVFoundation.AVMediaTypeAudio, lambda granted: logger.info("Microphone access granted: %s", granted)
                )
                asked.append("Microphone")
        except Exception as e:
            logger.debug("Could not ask for the microphone: %s", e)
    if not permissions["accessibility"].granted:
        try:
            from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
            AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
            asked.append("Accessibility")
        except Exception as e:
            logger.debug("Could not ask for Accessibility: %s", e)
    if not permissions["input_monitoring"].granted:
        try:
            import Quartz
            if hasattr(Quartz, "CGRequestListenEventAccess"):
                Quartz.CGRequestListenEventAccess()
                asked.append("Input Monitoring")
        except Exception as e:
            logger.debug("Could not ask for Input Monitoring: %s", e)
    if asked:
        logger.info("Asked macOS for: %s", ", ".join(asked))
    return asked


ASKED_FILE_NAME = "permissions_asked.json"


def ask_missing_once(permissions: Dict[str, PermissionItem], record_file: Path) -> List[str]:
    """
    Ask macOS for missing permissions at start, but each one only once ever: macOS's window
    ("Utter would like to control this Mac...") appears on the first start, never on every
    start. What was asked is remembered in record_file (in the data folder). If a permission
    is still missing later, Utter only says so in its menu; "Check Permissions" asks again.
    """
    asked_before = _read_asked(record_file)
    pending = {
        key: (replace(item, granted=True) if item.name in asked_before else item)
        for key, item in permissions.items()
    }
    asked = request_missing_permissions(pending)
    if asked:
        try:
            record_file.parent.mkdir(parents=True, exist_ok=True)
            record_file.write_text(json.dumps(sorted(asked_before | set(asked))), encoding="utf-8")
        except OSError as e:
            logger.warning("Could not remember which permissions were asked: %s", e)
    return asked


def _read_asked(record_file: Optional[Path]) -> set:
    if record_file is None:
        return set()
    try:
        return set(json.loads(record_file.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return set()


def was_asked(name: str, record_file: Optional[Path]) -> bool:
    """macOS was already asked once for this permission (by ask_missing_once or setup)."""
    return name in _read_asked(record_file)


def microphone_not_asked_yet() -> bool:
    try:
        import AVFoundation
        return AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(AVFoundation.AVMediaTypeAudio) == 0
    except Exception:
        return False


def turn_on_permission(key: str, record_file: Optional[Path] = None,
                       permissions: Optional[Dict[str, PermissionItem]] = None) -> str:
    """
    The welcome window's button for one permission. The first time, ask macOS (its own
    window appears, and Utter is added to the list in System Settings); after that, open the
    right page of System Settings instead (macOS never asks twice). Returns "asked",
    "opened_settings" or "granted".
    """
    permissions = permissions if permissions is not None else get_all_permissions()
    item = permissions[key]
    if item.granted:
        return "granted"
    first_time = (microphone_not_asked_yet() if key == "microphone"
                  else not was_asked(item.name, record_file))
    if first_time:
        only_this = {k: (p if k == key else replace(p, granted=True)) for k, p in permissions.items()}
        if request_missing_permissions(only_this):
            if record_file is not None:
                try:
                    record_file.parent.mkdir(parents=True, exist_ok=True)
                    record_file.write_text(json.dumps(sorted(_read_asked(record_file) | {item.name})),
                                           encoding="utf-8")
                except OSError as e:
                    logger.warning("Could not remember which permissions were asked: %s", e)
            return "asked"
    try:
        subprocess.run(["open", item.deep_link_url], check=False)
    except Exception as e:
        logger.error("Failed to open System Settings pane %s: %s", item.settings_path, e)
    return "opened_settings"


def request_permissions_prompt() -> None:
    """Trigger system dialogs if permissions are not yet requested."""
    try:
        import AVFoundation
        AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVFoundation.AVMediaTypeAudio, lambda granted: None
        )
    except Exception:
        pass

    try:
        import Quartz
        if hasattr(Quartz, "CGRequestListenEventAccess"):
            Quartz.CGRequestListenEventAccess()
    except Exception:
        pass


def format_permissions_report(permissions: Optional[Dict[str, PermissionItem]] = None) -> str:
    """Generate a clear, human-readable terminal and UI report for missing permissions."""
    if permissions is None:
        permissions = get_all_permissions()

    missing = [item for item in permissions.values() if not item.granted]
    lines: List[str] = []

    if not missing:
        lines.append("✓ All required macOS permissions are granted (Microphone, Accessibility, Input Monitoring).")
        return "\n".join(lines)

    lines.append("=" * 70)
    lines.append("⚠️  MISSING macOS PERMISSIONS DETECTED")
    lines.append("=" * 70)
    from engine.config import running_as_app
    as_app = running_as_app()
    lines.append("Running as: Utter.app" if as_app else f"Running on: Python ({sys.executable})")
    lines.append("Please enable the following permissions so Utter can work:")
    lines.append("")

    for item in missing:
        lines.append(f"• {item.name}:")
        lines.append(f"    Path:    {item.settings_path}")
        lines.append(f"    Purpose: {item.purpose}")
        lines.append(f"    Direct URL: open '{item.deep_link_url}'")
        lines.append("")

    if as_app:
        lines.append("Note: turn each one on for \"Utter\" in System Settings, then quit and reopen")
        lines.append("      Utter (the hotkeys start only after Accessibility and Input Monitoring")
        lines.append("      are on). If Utter is switched on and still not working, remove it with -")
        lines.append("      and open Utter again.")
    else:
        lines.append("Note: If running from Terminal, iTerm2, or VS Code, you may need to grant")
        lines.append("      permission to that specific terminal application or to the Python binary.")
    lines.append("=" * 70)

    return "\n".join(lines)
