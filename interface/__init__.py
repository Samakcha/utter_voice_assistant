"""
Interface package for Voice Assistant.
Contains macOS menu bar app, global hotkey handling, and permissions guidance.
Calls into engine/, never the reverse.
"""

from .app import VoiceAssistantApp
from .hotkeys import HotkeyManager, HotkeyRegistrationError
from .permissions import (
    check_accessibility_permission,
    check_input_monitoring_permission,
    check_microphone_permission,
    format_permissions_report,
    get_all_permissions,
    open_settings_pane,
)

__all__ = [
    "HotkeyManager",
    "HotkeyRegistrationError",
    "VoiceAssistantApp",
    "check_accessibility_permission",
    "check_input_monitoring_permission",
    "check_microphone_permission",
    "format_permissions_report",
    "get_all_permissions",
    "open_settings_pane",
]
