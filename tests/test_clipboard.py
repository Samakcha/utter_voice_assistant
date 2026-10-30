"""Unit tests for clipboard save/restore, transient markers, and formatting."""

import AppKit
import pytest

from engine.config import DictationConfig
from engine.insertion import format_dictation_insertion
from interface.clipboard import (
    CONCEALED_TYPE,
    TRANSIENT_TYPE,
    restore_pasteboard_snapshot,
    take_pasteboard_snapshot,
    wait_for_modifiers_released,
    write_dictation_to_pasteboard,
)


def get_test_pasteboard(name="TestClipboardUnit"):
    pb = AppKit.NSPasteboard.pasteboardWithName_(name)
    pb.clearContents()
    return pb


def test_format_dictation_insertion():
    # Empty / whitespace
    res = format_dictation_insertion("")
    assert res.should_insert is False
    assert res.word_count == 0

    res = format_dictation_insertion("   \n\t  ")
    assert res.should_insert is False

    # Normal text without trailing space
    res = format_dictation_insertion("  Hello world from test.  ", trailing_space=False)
    assert res.should_insert is True
    assert res.text == "Hello world from test."
    assert res.word_count == 4

    # With trailing space
    res = format_dictation_insertion("Hello world", trailing_space=True)
    assert res.should_insert is True
    assert res.text == "Hello world "
    assert res.word_count == 2


def test_clipboard_save_and_restore():
    pb = get_test_pasteboard("TestSaveRestore")

    # Set initial clipboard item
    item = AppKit.NSPasteboardItem.alloc().init()
    item.setString_forType_("Initial User Text", AppKit.NSPasteboardTypeString)
    pb.writeObjects_([item])

    # Snapshot
    snapshot = take_pasteboard_snapshot(pb)
    assert len(snapshot.items) == 1

    # Overwrite with dictation text
    new_count = write_dictation_to_pasteboard("Dictated text here", pb)
    assert pb.stringForType_(AppKit.NSPasteboardTypeString) == "Dictated text here"

    # Verify transient marker was attached
    items = pb.pasteboardItems()
    types = [t for t in items[0].types()]
    assert TRANSIENT_TYPE in types
    assert CONCEALED_TYPE in types

    # Restore snapshot
    restored = restore_pasteboard_snapshot(snapshot, new_count, pb)
    assert restored is True
    assert pb.stringForType_(AppKit.NSPasteboardTypeString) == "Initial User Text"


def test_clipboard_restore_aborted_if_change_count_moved():
    pb = get_test_pasteboard("TestChangeCountMismatch")

    item = AppKit.NSPasteboardItem.alloc().init()
    item.setString_forType_("Original Content", AppKit.NSPasteboardTypeString)
    pb.writeObjects_([item])

    snapshot = take_pasteboard_snapshot(pb)
    new_count = write_dictation_to_pasteboard("Temporary Dictation", pb)

    # Simulate another application or user copying something during the delay
    pb.clearContents()
    external_item = AppKit.NSPasteboardItem.alloc().init()
    external_item.setString_forType_("Fresh User Copy", AppKit.NSPasteboardTypeString)
    pb.writeObjects_([external_item])

    # Attempt restoration with stale change_count
    restored = restore_pasteboard_snapshot(snapshot, new_count, pb)
    assert restored is False
    # Fresh copy is strictly preserved!
    assert pb.stringForType_(AppKit.NSPasteboardTypeString) == "Fresh User Copy"


def test_modifier_release_wait():
    # In test environment where no keys are pressed, returns True promptly
    released = wait_for_modifiers_released(timeout_ms=50)
    assert released is True
