"""Unit tests for HotkeyManager parsing, diagnostics, and behavior."""

import pytest
from interface.hotkeys import HotkeyManager, HotkeyRegistrationError


def test_hotkey_manager_initialization():
    mgr = HotkeyManager(
        command_hotkey_str="<ctrl>+<alt>+m",
        dictation_hotkey_str="<ctrl>+<alt>+d",
        cancel_hotkey_str="<esc>",
        on_command=lambda: None,
        on_dictation=lambda: None,
        on_cancel=lambda: None,
        is_session_active=lambda: False,
    )
    mapping = mgr._build_hotkey_map()
    assert "<ctrl>+<alt>+m" in mapping
    assert "<ctrl>+<alt>+d" in mapping
    assert "<esc>" in mapping


def test_invalid_hotkey_raises_clear_error():
    mgr = HotkeyManager(
        command_hotkey_str="<nonexistent_key_name>+xyz",
        dictation_hotkey_str="<ctrl>+<alt>+d",
        cancel_hotkey_str="<esc>",
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
        cancel_hotkey_str="<esc>",
        on_command=lambda: None,
        on_dictation=lambda: None,
        on_cancel=on_cancel,
        is_session_active=lambda: is_active,
    )

    # 1. When session is NOT active, Esc must do nothing
    is_active = False
    mgr._handle_cancel_trigger()
    assert not cancel_called, "Esc must NOT trigger when session is inactive"

    # 2. When session IS active, Esc must trigger cancel
    is_active = True
    mgr._handle_cancel_trigger()
    assert cancel_called, "Esc must trigger cancel when session is active"


def test_key_listener_never_receives_system_defined_events():
    # Caps Lock sends one; pynput converting it off the main thread made macOS stop Utter
    import Quartz
    from pynput.keyboard import _darwin
    import interface.hotkeys  # noqa: F401  (applies the change on import)
    mask = _darwin.Listener._EVENTS
    assert not mask & Quartz.CGEventMaskBit(Quartz.NSSystemDefined)
    for needed in (Quartz.kCGEventKeyDown, Quartz.kCGEventKeyUp, Quartz.kCGEventFlagsChanged):
        assert mask & Quartz.CGEventMaskBit(needed)  # the hotkeys still see every key
    interface.hotkeys.ignore_system_defined_key_events()  # running it twice changes nothing
    assert _darwin.Listener._EVENTS == mask


def test_key_listener_thread_never_reads_the_keyboard_layout(monkeypatch):
    # pynput read it on its own thread each time it started; with a Utter window in front
    # macOS stopped Utter for that (pressing "Press keys…" in Settings restarts the listener)
    import contextlib
    import threading
    from pynput.keyboard import _darwin
    import interface.hotkeys as hotkeys

    reads = []

    @contextlib.contextmanager
    def reading_layout():
        reads.append(threading.current_thread() is threading.main_thread())
        yield ("kbd", b"layout")

    monkeypatch.setattr(hotkeys, "_original_keycode_context", reading_layout)
    monkeypatch.setattr(hotkeys, "_keyboard_layout", None)
    assert _darwin.keycode_context is not reading_layout  # pynput's listener uses Utter's

    seen = []

    def listener_thread():
        assert hotkeys.refresh_keyboard_layout() is False  # off the main thread: not read
        with _darwin.keycode_context() as context:
            seen.append(context)

    assert hotkeys.refresh_keyboard_layout() is True  # what HotkeyManager.start() does
    thread = threading.Thread(target=listener_thread)
    thread.start()
    thread.join()
    assert seen == [("kbd", b"layout")]
    assert reads == [True]  # read once, on the main thread only


def test_hotkey_manager_start_reads_the_layout_first(monkeypatch):
    import interface.hotkeys as hotkeys
    calls = []
    monkeypatch.setattr(hotkeys, "refresh_keyboard_layout", lambda: calls.append("layout"))
    manager = hotkeys.HotkeyManager("<ctrl>+<alt>+m", "<ctrl>+<alt>+d")
    monkeypatch.setattr(manager, "_verify_system_permissions", lambda: calls.append("perm") or False)
    monkeypatch.setattr(hotkeys.keyboard, "GlobalHotKeys",
                        lambda bindings: type("L", (), {"start": lambda self: None})())
    manager.start()
    assert calls[:2] == ["layout", "perm"]
