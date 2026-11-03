"""macOS clipboard management and Quartz key event insertion for Dictation Mode."""

import logging
import time
from typing import Callable, List, Optional
import AppKit
import Quartz

from engine.config import DictationConfig
from engine.insertion import ClipboardSnapshot

logger = logging.getLogger(__name__)

# Virtual keycode for ANSI 'v' on standard macOS keyboard layout
kVK_ANSI_V = 9

# Transient and concealed marker types to notify clipboard managers (e.g. Maccy, Paste, Raycast)
TRANSIENT_TYPE = "org.nspasteboard.TransientType"
CONCEALED_TYPE = "org.nspasteboard.ConcealedType"


def get_general_pasteboard() -> AppKit.NSPasteboard:
    """Return the general NSPasteboard (or isolated test pasteboard during testing)."""
    return AppKit.NSPasteboard.generalPasteboard()


def take_pasteboard_snapshot(pasteboard: Optional[AppKit.NSPasteboard] = None) -> ClipboardSnapshot:
    """
    Take a full snapshot of all items and data types currently on the NSPasteboard.
    Saves raw NSData for every UTI type present on each item.
    """
    pb = pasteboard or get_general_pasteboard()
    change_count = pb.changeCount()
    saved_items = []

    for item in pb.pasteboardItems() or []:
        item_data = {}
        for ptype in item.types():
            data = item.dataForType_(ptype)
            if data is not None:
                item_data[ptype] = data
        if item_data:
            saved_items.append(item_data)

    return ClipboardSnapshot(change_count=change_count, items=saved_items)


def write_dictation_to_pasteboard(
    text: str,
    pasteboard: Optional[AppKit.NSPasteboard] = None,
) -> int:
    """
    Write dictated text to NSPasteboard, accompanied by transient and concealed markers.
    Returns the new changeCount after writing.
    """
    pb = pasteboard or get_general_pasteboard()
    pb.clearContents()

    item = AppKit.NSPasteboardItem.alloc().init()
    item.setString_forType_(text, AppKit.NSPasteboardTypeString)

    # Attach transient / concealed markers so clipboard managers skip recording it
    empty_data = AppKit.NSData.data()
    item.setData_forType_(empty_data, TRANSIENT_TYPE)
    item.setData_forType_(empty_data, CONCEALED_TYPE)

    pb.writeObjects_([item])
    return pb.changeCount()


def restore_pasteboard_snapshot(
    snapshot: ClipboardSnapshot,
    expected_change_count: int,
    pasteboard: Optional[AppKit.NSPasteboard] = None,
) -> bool:
    """
    Restore the original items onto NSPasteboard.
    Only restores if changeCount matches expected_change_count (i.e. nothing else
    was copied by the user or another app in the meantime).
    
    Returns:
        True if restored, False if skipped due to changeCount mismatch.
    """
    pb = pasteboard or get_general_pasteboard()
    current_count = pb.changeCount()

    if current_count != expected_change_count:
        logger.info(
            "Pasteboard changeCount moved from %d to %d; skipping restore to preserve new clipboard contents.",
            expected_change_count,
            current_count,
        )
        return False

    pb.clearContents()
    items_to_write = []
    for item_dict in snapshot.items:
        pb_item = AppKit.NSPasteboardItem.alloc().init()
        for ptype, data in item_dict.items():
            pb_item.setData_forType_(data, ptype)
        items_to_write.append(pb_item)

    if items_to_write:
        pb.writeObjects_(items_to_write)

    logger.debug("Restored original pasteboard snapshot (%d items).", len(items_to_write))
    return True


def wait_for_modifiers_released(timeout_ms: int = 500) -> bool:
    """
    Wait until Control and Option modifier keys are physically released.
    Useful when dictation was stopped via Control+Option+D so that Cmd+V
    does not accidentally become Ctrl+Opt+Cmd+V.
    """
    deadline = time.monotonic() + (timeout_ms / 1000.0)
    while time.monotonic() < deadline:
        flags = Quartz.CGEventSourceFlagsState(Quartz.kCGEventSourceStateCombinedSessionState)
        has_ctrl = bool(flags & Quartz.kCGEventFlagMaskControl)
        has_alt = bool(flags & Quartz.kCGEventFlagMaskAlternate)
        if not (has_ctrl or has_alt):
            return True
        time.sleep(0.02)
    logger.debug("Modifier release wait timed out after %d ms; proceeding with paste.", timeout_ms)
    return False


def post_cmd_v_key_event() -> None:
    """
    Post a synthesized Cmd+V key event to the macOS window server.
    Flags are explicitly set to Command ONLY.
    Must be called from the macOS main thread.
    """
    event_down = Quartz.CGEventCreateKeyboardEvent(None, kVK_ANSI_V, True)
    Quartz.CGEventSetFlags(event_down, Quartz.kCGEventFlagMaskCommand)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event_down)

    event_up = Quartz.CGEventCreateKeyboardEvent(None, kVK_ANSI_V, False)
    Quartz.CGEventSetFlags(event_up, Quartz.kCGEventFlagMaskCommand)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event_up)


def paste_and_restore(
    text: str,
    config: DictationConfig,
    pasteboard: Optional[AppKit.NSPasteboard] = None,
    on_complete: Optional[Callable[[bool], None]] = None,
) -> None:
    """
    Perform clipboard save, write, Cmd+V insertion, and delayed restoration.
    Must be called on the macOS main thread.
    """
    if not text:
        if on_complete:
            on_complete(False)
        return

    pb = pasteboard or get_general_pasteboard()

    # 1. Take snapshot of existing clipboard
    snapshot = take_pasteboard_snapshot(pb)

    # 2. Write dictated text with transient marker
    post_write_change_count = write_dictation_to_pasteboard(text, pb)

    # 3. Wait for Control and Option keys to be physically released
    wait_for_modifiers_released(timeout_ms=config.modifier_release_timeout_ms)

    # 4. Synthesize Cmd+V with Command flag only
    post_cmd_v_key_event()

    # 5. Schedule delayed restoration on the main thread
    delay_sec = max(0.05, config.clipboard_restore_delay_ms / 1000.0)

    def _do_restore():
        restored = restore_pasteboard_snapshot(snapshot, post_write_change_count, pb)
        if on_complete:
            try:
                on_complete(restored)
            except Exception as e:
                logger.error("Error in paste_and_restore on_complete callback: %s", e)

    # Schedule via NSTimer on main run loop
    AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
        delay_sec,
        False,
        lambda timer: _do_restore(),
    )
