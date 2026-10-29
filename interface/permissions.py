"""macOS permissions detection and guidance for Microphone, Accessibility, and Input Monitoring."""

from dataclasses import dataclass
import logging
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
    lines.append(f"Running on: Python ({sys.executable})")
    lines.append("Please enable the following permissions so the Voice Assistant can operate:")
    lines.append("")

    for item in missing:
        lines.append(f"• {item.name}:")
        lines.append(f"    Path:    {item.settings_path}")
        lines.append(f"    Purpose: {item.purpose}")
        lines.append(f"    Direct URL: open '{item.deep_link_url}'")
        lines.append("")

    lines.append("Note: If running from Terminal, iTerm2, or VS Code, you may need to grant")
    lines.append("      permission to that specific terminal application or to the Python binary.")
    lines.append("=" * 70)

    return "\n".join(lines)
