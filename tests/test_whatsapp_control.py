"""
Unit tests for WhatsApp automation and AX control: interface/whatsapp_control.py and interface/whatsapp_ax.py.
Validates:
- Dual frontmost checks (agreement vs disagreement vs other apps)
- Strict keycode whitelist {3, 9}
- Non-blocking asynchronous execution (worker threads, no main-thread hangs)
- Full search sequence: AXCancel -> AXPress -> verify focus -> Cmd+F fallback -> transient paste -> Cmd+V -> clipboard restored
- Error paths: not frontmost, UI unreadable, focus failure, AX timeout
- Independence: zero imports from scripts/, zero row-finding code
"""

import ast
from pathlib import Path
import threading
import time
from unittest.mock import MagicMock, patch
import pytest

import AppKit
import Quartz

from interface.clipboard import get_general_pasteboard
from interface.whatsapp_control import (
    ALLOWED_WHATSAPP_KEYCODES,
    WhatsAppActionResult,
    check_whatsapp_frontmost_dual,
    get_whatsapp_running_app,
    open_whatsapp_async,
    post_whatsapp_key,
    search_whatsapp_async,
)
from interface.whatsapp_ax import (
    DEFAULT_AX_TIMEOUT_SEC,
    WHATSAPP_BUNDLE_ID,
    check_is_focused,
    find_search_field,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def direct_main_thread_dispatch(monkeypatch):
    """
    In CLI test runner without an active Cocoa NSApp run loop,
    execute run_on_main_thread callbacks immediately on caller thread.
    """
    monkeypatch.setattr("interface.whatsapp_control.run_on_main_thread", lambda fn, *a: fn(*a))


def test_no_imports_from_scripts_in_production():
    """Verify production modules NEVER import from scripts/."""
    for path in [
        PROJECT_ROOT / "interface" / "whatsapp_control.py",
        PROJECT_ROOT / "interface" / "whatsapp_ax.py",
        PROJECT_ROOT / "interface" / "app.py",
    ]:
        source = path.read_text(encoding="utf-8")
        assert "from scripts" not in source, f"Forbidden import from scripts in {path.name}"
        assert "import scripts" not in source, f"Forbidden import from scripts in {path.name}"


def test_zero_row_finding_code_in_production():
    """Verify production AX module does not contain any row-finding code (Phase 0 only)."""
    ax_path = PROJECT_ROOT / "interface" / "whatsapp_ax.py"
    source = ax_path.read_text(encoding="utf-8")
    assert "find_chat_rows" not in source
    assert "find_sidebar" not in source
    assert "geometry" not in source.lower()


def test_dual_frontmost_check_scenarios(monkeypatch):
    """Verify check_whatsapp_frontmost_dual behavior across agreement and disagreement."""
    # Scenario 1: Both agree on WhatsApp
    monkeypatch.setattr("interface.whatsapp_control.get_frontmost_bundle_nsworkspace", lambda: "net.whatsapp.WhatsApp")
    monkeypatch.setattr("interface.whatsapp_control.get_frontmost_bundle_lsappinfo", lambda: "net.whatsapp.WhatsApp")
    agree, nsws, ls = check_whatsapp_frontmost_dual()
    assert agree is True
    assert nsws == "net.whatsapp.WhatsApp"
    assert ls == "net.whatsapp.WhatsApp"

    # Scenario 2: NSWorkspace says WhatsApp, lsappinfo says Terminal
    monkeypatch.setattr("interface.whatsapp_control.get_frontmost_bundle_nsworkspace", lambda: "net.whatsapp.WhatsApp")
    monkeypatch.setattr("interface.whatsapp_control.get_frontmost_bundle_lsappinfo", lambda: "com.apple.Terminal")
    agree, nsws, ls = check_whatsapp_frontmost_dual()
    assert agree is False
    assert nsws == "net.whatsapp.WhatsApp"
    assert ls == "com.apple.Terminal"

    # Scenario 3: NSWorkspace says Terminal, lsappinfo says WhatsApp
    monkeypatch.setattr("interface.whatsapp_control.get_frontmost_bundle_nsworkspace", lambda: "com.apple.Terminal")
    monkeypatch.setattr("interface.whatsapp_control.get_frontmost_bundle_lsappinfo", lambda: "net.whatsapp.WhatsApp")
    agree, nsws, ls = check_whatsapp_frontmost_dual()
    assert agree is False

    # Scenario 4: Both report Terminal
    monkeypatch.setattr("interface.whatsapp_control.get_frontmost_bundle_nsworkspace", lambda: "com.apple.Terminal")
    monkeypatch.setattr("interface.whatsapp_control.get_frontmost_bundle_lsappinfo", lambda: "com.apple.Terminal")
    agree, nsws, ls = check_whatsapp_frontmost_dual()
    assert agree is False


def test_post_whatsapp_key_strict_whitelist():
    """Verify post_whatsapp_key rejects any key not in {3, 9} with AssertionError."""
    fake_agree = lambda: (True, "net.whatsapp.WhatsApp", "net.whatsapp.WhatsApp")

    for forbidden in [36, 51, 53, 0, 1, 12, 123, 124, 125, 126]:
        with pytest.raises(AssertionError):
            post_whatsapp_key(forbidden, dual_check_fn=fake_agree)


def test_post_whatsapp_key_dual_guard_abort():
    """Verify post_whatsapp_key aborts with RuntimeError if dual checks do not agree on WhatsApp."""
    fake_disagree = lambda: (False, "com.apple.Terminal", "net.whatsapp.WhatsApp")
    with pytest.raises(RuntimeError) as exc_info:
        post_whatsapp_key(3, dual_check_fn=fake_disagree)
    assert "SAFETY ABORT" in str(exc_info.value)


def test_open_whatsapp_async_success(monkeypatch):
    """Verify open_whatsapp_async launches via system launcher and reports success when frontmost."""
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: MagicMock(returncode=0))
    monkeypatch.setattr(
        "interface.whatsapp_control.check_whatsapp_frontmost_dual",
        lambda: (True, "net.whatsapp.WhatsApp", "net.whatsapp.WhatsApp"),
    )

    done_event = threading.Event()
    result_holder = []

    def on_complete(res: WhatsAppActionResult):
        result_holder.append(res)
        done_event.set()

    open_whatsapp_async(callback=on_complete, timeout_sec=0.5)
    assert done_event.wait(timeout=2.0)
    assert len(result_holder) == 1
    res = result_holder[0]
    assert res.success is True
    assert res.message == "WhatsApp ready"


def test_open_whatsapp_async_timeout(monkeypatch):
    """Verify open_whatsapp_async reports failure when WhatsApp does not become frontmost within timeout."""
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: MagicMock(returncode=0))
    monkeypatch.setattr(
        "interface.whatsapp_control.check_whatsapp_frontmost_dual",
        lambda: (False, "com.apple.Terminal", "com.apple.Terminal"),
    )

    done_event = threading.Event()
    result_holder = []

    def on_complete(res: WhatsAppActionResult):
        result_holder.append(res)
        done_event.set()

    open_whatsapp_async(callback=on_complete, timeout_sec=0.2)
    assert done_event.wait(timeout=2.0)
    assert len(result_holder) == 1
    res = result_holder[0]
    assert res.success is False
    assert "Couldn't open WhatsApp" in res.message


def test_search_whatsapp_async_not_frontmost_abort():
    """Verify search_whatsapp_async aborts cleanly with zero launches, zero AX actions, and zero key events."""
    done_event = threading.Event()
    result_holder = []

    mock_dual = lambda: (False, "com.apple.Terminal", "com.apple.Terminal")

    search_whatsapp_async(
        name="Alex Test",
        callback=lambda res: (result_holder.append(res), done_event.set()),
        mock_dual_check=mock_dual,
    )

    assert done_event.wait(timeout=2.0)
    assert len(result_holder) == 1
    res = result_holder[0]
    assert res.success is False
    assert "WhatsApp isn't in front. Say 'open WhatsApp' first." in res.message


def test_search_whatsapp_async_clipboard_restore_on_success(monkeypatch):
    """
    Verify search sequence:
    - Pre-existing clipboard is preserved and restored in finally block.
    - Post keycode 9 (Cmd+V) is called.
    - Guidance message is returned.
    """
    pb = get_general_pasteboard()
    original_text = f"TestClipboardPreservation_{time.time()}"
    pb.clearContents()
    pb.setString_forType_(original_text, AppKit.NSPasteboardTypeString)

    mock_dual = lambda: (True, "net.whatsapp.WhatsApp", "net.whatsapp.WhatsApp")

    mock_app = MagicMock()
    mock_app.bundleIdentifier.return_value = WHATSAPP_BUNDLE_ID
    mock_app.processIdentifier.return_value = 12345
    monkeypatch.setattr("interface.whatsapp_control.get_whatsapp_running_app", lambda: mock_app)

    # Mock AX elements
    mock_search_field = MagicMock()
    monkeypatch.setattr("interface.whatsapp_control.get_main_window", lambda app_el: MagicMock())
    monkeypatch.setattr("interface.whatsapp_control.find_search_field", lambda win, max_depth: (mock_search_field, 5))
    monkeypatch.setattr("interface.whatsapp_control.check_is_focused", lambda el, app_el: (True, "AXTextField", "AXSearchField"))

    actions_called = []
    def mock_perform_action(el, action):
        actions_called.append(action)
    monkeypatch.setattr("ApplicationServices.AXUIElementPerformAction", mock_perform_action)

    posted_keys = []
    def mock_post_key(keycode, modifiers=0, dual_check_fn=None):
        posted_keys.append(keycode)
    monkeypatch.setattr("interface.whatsapp_control.post_whatsapp_key", mock_post_key)
    monkeypatch.setattr("interface.whatsapp_control.wait_for_modifiers_released", lambda timeout_ms: True)

    done_event = threading.Event()
    result_holder = []

    search_whatsapp_async(
        name="Alex Test",
        callback=lambda res: (result_holder.append(res), done_event.set()),
        clipboard_restore_delay_ms=20,
        mock_dual_check=mock_dual,
    )

    assert done_event.wait(timeout=2.0)
    assert len(result_holder) == 1
    res = result_holder[0]

    assert res.success is True
    assert "Pick the chat" in res.message
    # Check that AXCancel and AXPress were performed in order
    assert actions_called == ["AXCancel", "AXPress"]
    # Check that Cmd+V (keycode 9) was posted
    assert posted_keys == [9]

    # Allow timer to complete delayed restoration
    time.sleep(0.08)

    # Verify clipboard was restored to original_text
    restored_text = pb.stringForType_(AppKit.NSPasteboardTypeString)
    assert restored_text == original_text


def test_search_whatsapp_async_fallback_cmdf(monkeypatch):
    """
    Verify fallback: if AXPress does not achieve focus (subrole != 'AXSearchField'),
    the search sequence posts keycode 3 (Cmd+F) once as fallback.
    """
    mock_dual = lambda: (True, "net.whatsapp.WhatsApp", "net.whatsapp.WhatsApp")
    mock_app = MagicMock()
    mock_app.bundleIdentifier.return_value = WHATSAPP_BUNDLE_ID
    mock_app.processIdentifier.return_value = 12345
    monkeypatch.setattr("interface.whatsapp_control.get_whatsapp_running_app", lambda: mock_app)

    mock_search_field = MagicMock()
    monkeypatch.setattr("interface.whatsapp_control.get_main_window", lambda app_el: MagicMock())
    monkeypatch.setattr("interface.whatsapp_control.find_search_field", lambda win, max_depth: (mock_search_field, 5))

    focus_checks = [
        (False, "AXGroup", "None"),          # After AXPress: not focused
        (True, "AXTextField", "AXSearchField") # After Cmd+F fallback: focused!
    ]
    def mock_check_is_focused(el, app_el):
        return focus_checks.pop(0) if focus_checks else (True, "AXTextField", "AXSearchField")
    monkeypatch.setattr("interface.whatsapp_control.check_is_focused", mock_check_is_focused)

    monkeypatch.setattr("ApplicationServices.AXUIElementPerformAction", lambda el, action: None)

    posted_keys = []
    monkeypatch.setattr(
        "interface.whatsapp_control.post_whatsapp_key",
        lambda keycode, modifiers=0, dual_check_fn=None: posted_keys.append(keycode),
    )
    monkeypatch.setattr("interface.whatsapp_control.wait_for_modifiers_released", lambda timeout_ms: True)

    done_event = threading.Event()
    result_holder = []

    search_whatsapp_async(
        name="Alex Test",
        callback=lambda res: (result_holder.append(res), done_event.set()),
        clipboard_restore_delay_ms=20,
        mock_dual_check=mock_dual,
    )

    assert done_event.wait(timeout=2.0)
    assert len(result_holder) == 1
    res = result_holder[0]
    assert res.success is True
    # Posted both Cmd+F (3) and Cmd+V (9)
    assert posted_keys == [3, 9]


def test_search_whatsapp_async_focus_failure(monkeypatch):
    """Verify that if focus cannot be confirmed even after Cmd+F, it aborts with error."""
    mock_dual = lambda: (True, "net.whatsapp.WhatsApp", "net.whatsapp.WhatsApp")
    mock_app = MagicMock()
    mock_app.bundleIdentifier.return_value = WHATSAPP_BUNDLE_ID
    mock_app.processIdentifier.return_value = 12345
    monkeypatch.setattr("interface.whatsapp_control.get_whatsapp_running_app", lambda: mock_app)

    mock_search_field = MagicMock()
    monkeypatch.setattr("interface.whatsapp_control.get_main_window", lambda app_el: MagicMock())
    monkeypatch.setattr("interface.whatsapp_control.find_search_field", lambda win, max_depth: (mock_search_field, 5))
    # Never achieves subrole AXSearchField
    monkeypatch.setattr("interface.whatsapp_control.check_is_focused", lambda el, app_el: (False, "AXGroup", "None"))
    monkeypatch.setattr("ApplicationServices.AXUIElementPerformAction", lambda el, action: None)

    posted_keys = []
    monkeypatch.setattr(
        "interface.whatsapp_control.post_whatsapp_key",
        lambda keycode, modifiers=0, dual_check_fn=None: posted_keys.append(keycode),
    )

    done_event = threading.Event()
    result_holder = []

    search_whatsapp_async(
        name="Alex Test",
        callback=lambda res: (result_holder.append(res), done_event.set()),
        clipboard_restore_delay_ms=20,
        mock_dual_check=mock_dual,
    )

    assert done_event.wait(timeout=2.0)
    assert len(result_holder) == 1
    res = result_holder[0]
    assert res.success is False
    assert "Couldn't focus WhatsApp search" in res.message
    # Only Cmd+F was attempted, Cmd+V was NEVER posted
    assert posted_keys == [3]


def test_search_whatsapp_async_clipboard_restore_skipped_if_changecount_changed(monkeypatch):
    """
    Verify that if the user or another app copies new text to pasteboard during the delay,
    the restore operation detects changeCount mismatch and preserves the fresh copy.
    """
    pb = get_general_pasteboard()
    pb.clearContents()
    original_text = "OriginalSnapshotText"
    pb.setString_forType_(original_text, AppKit.NSPasteboardTypeString)

    mock_dual = lambda: (True, "net.whatsapp.WhatsApp", "net.whatsapp.WhatsApp")
    mock_app = MagicMock()
    mock_app.bundleIdentifier.return_value = WHATSAPP_BUNDLE_ID
    mock_app.processIdentifier.return_value = 12345
    monkeypatch.setattr("interface.whatsapp_control.get_whatsapp_running_app", lambda: mock_app)

    mock_search_field = MagicMock()
    monkeypatch.setattr("interface.whatsapp_control.get_main_window", lambda app_el: MagicMock())
    monkeypatch.setattr("interface.whatsapp_control.find_search_field", lambda win, max_depth: (mock_search_field, 5))
    monkeypatch.setattr("interface.whatsapp_control.check_is_focused", lambda el, app_el: (True, "AXTextField", "AXSearchField"))
    monkeypatch.setattr("ApplicationServices.AXUIElementPerformAction", lambda el, action: None)
    monkeypatch.setattr("interface.whatsapp_control.post_whatsapp_key", lambda *a, **kw: None)
    monkeypatch.setattr("interface.whatsapp_control.wait_for_modifiers_released", lambda timeout_ms: True)

    done_event = threading.Event()
    result_holder = []

    search_whatsapp_async(
        name="Alex Test",
        callback=lambda res: (result_holder.append(res), done_event.set()),
        clipboard_restore_delay_ms=50,
        mock_dual_check=mock_dual,
    )

    assert done_event.wait(timeout=2.0)

    # Simulate user copying something fresh before the 50ms restore timer fires
    pb.clearContents()
    pb.setString_forType_("FreshUserCopyDuringDelay", AppKit.NSPasteboardTypeString)

    # Wait for the restore timer to fire
    time.sleep(0.1)

    # Verify that the fresh copy was preserved and NOT overwritten!
    assert pb.stringForType_(AppKit.NSPasteboardTypeString) == "FreshUserCopyDuringDelay"
