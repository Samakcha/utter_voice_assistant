"""Unit tests for HotkeyManager parsing, diagnostics, and behavior."""

import pytest
from interface.hotkeys import HotkeyManager, HotkeyRegistrationError


def test_hotkey_manager_initialization():
    mgr = HotkeyManager(
        command_hotkey_str="<ctrl>+<alt>+m",
        dictation_hotkey_str="<ctrl>+<alt>+d",
        on_command=lambda: None,
        on_dictation=lambda: None,
        on_cancel=lambda: None,
        is_session_active=lambda: False,
    )
    mapping = mgr._build_hotkey_map()
    assert "<ctrl>+<alt>+m" in mapping
    assert "<ctrl>+<alt>+d" in mapping


def test_invalid_hotkey_raises_clear_error():
    mgr = HotkeyManager(
        command_hotkey_str="<nonexistent_key_name>+xyz",
        dictation_hotkey_str="<ctrl>+<alt>+d",
        on_command=lambda: None,
        on_dictation=lambda: None,
        on_cancel=lambda: None,
        is_session_active=lambda: False,
    )
    try:
        mgr._build_hotkey_map()
        assert False, "Expected HotkeyRegistrationError"
    except HotkeyRegistrationError as e:
        assert "Invalid syntax for command_mode hotkey" in str(e)


def test_esc_only_acts_when_session_active():
    cancel_called = False

    def on_cancel():
        nonlocal cancel_called
        cancel_called = True

    is_active = False
    mgr = HotkeyManager(
        command_hotkey_str="<ctrl>+<alt>+m",
        dictation_hotkey_str="<ctrl>+<alt>+d",
        on_command=lambda: None,
        on_dictation=lambda: None,
        on_cancel=on_cancel,
        is_session_active=lambda: is_active,
    )

    from pynput.keyboard import Key

    # 1. When session is NOT active, Esc must do nothing
    is_active = False
    mgr._handle_raw_keypress(Key.esc)
    assert not cancel_called, "Esc must NOT trigger when session is inactive"

    # 2. When session IS active, Esc must trigger cancel
    is_active = True
    mgr._handle_raw_keypress(Key.esc)
    assert cancel_called, "Esc must trigger cancel when session is active"
