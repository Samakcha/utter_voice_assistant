#!/usr/bin/env python3
"""
WhatsApp Desktop Controlled Interactive Experiment (Phase 0 - Revised).
Tests search field focusing (AXPress vs Cmd+F), pre-paste focus guard (subrole AXSearchField only),
live search filtering, search clearing via AXCancel, structural row identification,
and post-selection focus role.

CRITICAL SAFETY & PRIVACY RULES:
1. FOCUS HANDBACK: Records Terminal at launch. After EVERY WhatsApp action, re-activates
   Terminal and confirms it is frontmost BEFORE asking for Enter. Aborts if focus cannot
   be handed back.
2. DRAFT WARNING: Warns user before actions to ensure WhatsApp has no draft message.
3. NEVER-SEND: Keycode whitelist is strictly {3 (Cmd+F), 9 (Cmd+V)}. Return/Enter is absent.
4. AXCancel: Clears and dismisses search via AXCancel on the search field (no Delete/Cmd+A).
5. STRUCTURE ONLY: Never reads, stores, or prints text, value, description, help,
   label, or selected text content attributes from any window content or chat.
"""

from collections import Counter, deque
from datetime import datetime
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

# Auto-reexec inside the virtual environment if available
PROJECT_ROOT = Path(__file__).resolve().parent.parent
venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
if venv_python.is_file() and sys.executable != str(venv_python):
    try:
        os.execv(str(venv_python), [str(venv_python)] + sys.argv)
    except Exception:
        pass

try:
    import ApplicationServices
    from AppKit import NSPasteboard, NSPasteboardTypeString, NSRunningApplication, NSWorkspace
    import Quartz
except ImportError as e:
    print(f"Error: Required macOS frameworks (PyObjC / Quartz) not available: {e}")
    sys.exit(1)


WHATSAPP_BUNDLE_ID = "net.whatsapp.WhatsApp"

# Absolute keycode whitelist: 3 = 'F' (for Cmd+F), 9 = 'V' (for Cmd+V).
# Return / Enter, Delete, and arrow keys are strictly forbidden.
ALLOWED_EXPERIMENT_KEYCODES = {3, 9}


def get_whatsapp_app() -> Optional[NSRunningApplication]:
    """Check if WhatsApp is currently running."""
    ws = NSWorkspace.sharedWorkspace()
    for app in ws.runningApplications():
        if app.bundleIdentifier() == WHATSAPP_BUNDLE_ID:
            return app
    return None


def activate_whatsapp(app: NSRunningApplication) -> bool:
    """Bring WhatsApp to the front and verify."""
    app.activateWithOptions_(ApplicationServices.NSApplicationActivateIgnoringOtherApps)
    for _ in range(30):
        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        if front and front.bundleIdentifier() == WHATSAPP_BUNDLE_ID:
            return True
        time.sleep(0.05)
    return False


def handback_focus(terminal_app: NSRunningApplication) -> bool:
    """
    CRITICAL SAFETY GUARD:
    Re-activate user's Terminal and verify it is frontmost before accepting input.
    Prevents user Enter from reaching WhatsApp.
    """
    terminal_app.activateWithOptions_(ApplicationServices.NSApplicationActivateIgnoringOtherApps)
    for _ in range(30):
        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        if front and front.processIdentifier() == terminal_app.processIdentifier():
            return True
        time.sleep(0.05)
    return False


def safe_input(prompt: str, terminal_app: NSRunningApplication) -> str:
    """Safely prompt the user only after Terminal focus is verified."""
    if not handback_focus(terminal_app):
        print("\n❌ CRITICAL SAFETY ERROR: Could not re-activate Terminal!")
        print("   Aborting script immediately to prevent stray Enter in WhatsApp.")
        sys.exit(1)
    return input(prompt)


def print_safety_warning() -> None:
    print("\n" + "-" * 70)
    print("⚠️  SAFETY CHECK:")
    print("   Please ensure WhatsApp has NO DRAFT in the composer.")
    print("   Select a 'Message yourself' chat or an empty chat, and clear any draft.")
    print("-" * 70)


def post_whitelisted_key(keycode: int, modifiers: int = 0) -> None:
    """
    Post a keyboard event strictly constrained to the whitelist {3, 9}.
    Asserts at runtime that Return or non-whitelisted keys cannot be posted.
    """
    assert keycode in ALLOWED_EXPERIMENT_KEYCODES, (
        f"NEVER-SEND VIOLATION: Keycode {keycode} is not in whitelist {ALLOWED_EXPERIMENT_KEYCODES}"
    )

    down = Quartz.CGEventCreateKeyboardEvent(None, keycode, True)
    up = Quartz.CGEventCreateKeyboardEvent(None, keycode, False)

    if modifiers:
        Quartz.CGEventSetFlags(down, modifiers)
        Quartz.CGEventSetFlags(up, modifiers)

    Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
    time.sleep(0.02)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)


def get_app_windows(app_element: Any) -> List[Any]:
    """Retrieve candidate root windows, prioritizing main and focused windows."""
    windows_list = []

    err_m, main_win = ApplicationServices.AXUIElementCopyAttributeValue(
        app_element, ApplicationServices.kAXMainWindowAttribute, None
    )
    if err_m == 0 and main_win:
        windows_list.append(main_win)

    err_f, foc_win = ApplicationServices.AXUIElementCopyAttributeValue(
        app_element, ApplicationServices.kAXFocusedWindowAttribute, None
    )
    if err_f == 0 and foc_win:
        if not any(foc_win == existing for existing in windows_list):
            windows_list.append(foc_win)

    err, windows = ApplicationServices.AXUIElementCopyAttributeValue(
        app_element, ApplicationServices.kAXWindowsAttribute, None
    )
    if err == 0 and windows:
        for w in windows:
            if not any(w == existing for existing in windows_list):
                windows_list.append(w)

    if not windows_list:
        err_c, children = ApplicationServices.AXUIElementCopyAttributeValue(
            app_element, ApplicationServices.kAXChildrenAttribute, None
        )
        if err_c == 0 and children:
            windows_list = list(children)

    return windows_list


def inspect_windows_metadata(windows: List[Any]) -> List[Dict[str, Any]]:
    """Gather structure-only metadata for each window (no content attributes)."""
    metas = []
    for idx, win in enumerate(windows, 1):
        err_r, role = ApplicationServices.AXUIElementCopyAttributeValue(
            win, ApplicationServices.kAXRoleAttribute, None
        )
        err_s, subrole = ApplicationServices.AXUIElementCopyAttributeValue(
            win, ApplicationServices.kAXSubroleAttribute, None
        )
        err_m, is_main = ApplicationServices.AXUIElementCopyAttributeValue(
            win, ApplicationServices.kAXMainAttribute, None
        )
        err_min, is_minimized = ApplicationServices.AXUIElementCopyAttributeValue(
            win, ApplicationServices.kAXMinimizedAttribute, None
        )

        metas.append({
            "index": idx,
            "role": str(role) if (err_r == 0 and role) else "Unknown",
            "subrole": str(subrole) if (err_s == 0 and subrole) else "None",
            "is_main": bool(err_m == 0 and is_main),
            "is_minimized": bool(err_min == 0 and is_minimized),
        })
    return metas


def enable_catalyst_accessibility(app_element: Any) -> None:
    """
    Set AXManualAccessibility and AXEnhancedUserInterface on the app element.
    Mac Catalyst UIKit views are lazily populated in the AX tree until an assistive client sets these.
    """
    try:
        ApplicationServices.AXUIElementSetAttributeValue(
            app_element, "AXManualAccessibility", True
        )
    except Exception:
        pass
    try:
        ApplicationServices.AXUIElementSetAttributeValue(
            app_element, "AXEnhancedUserInterface", True
        )
    except Exception:
        pass


def find_search_field_bfs(
    app_element: Any, max_depth: int = 15
) -> Tuple[Optional[Any], Dict[str, Any]]:
    """
    Breadth-first search (BFS) across all application windows for the search field.
    Matches:
      - subrole == 'AXSearchField' with role in ('AXStaticText', 'AXTextField', 'AXSearchField')
      - or role == 'AXSearchField'
    Collects structure-only diagnostics:
      - max depth reached
      - role histogram
      - subrole histogram
      - windows metadata
    Never queries or stores content attributes.
    """
    windows = get_app_windows(app_element)
    win_metas = inspect_windows_metadata(windows)

    role_counts: Counter = Counter()
    subrole_counts: Counter = Counter()
    max_depth_reached = 1 if windows else 0
    search_field = None

    queue = deque()
    for w in windows:
        queue.append((w, 1))

    while queue:
        el, depth = queue.popleft()
        if depth > max_depth_reached:
            max_depth_reached = depth

        err_r, role = ApplicationServices.AXUIElementCopyAttributeValue(
            el, ApplicationServices.kAXRoleAttribute, None
        )
        err_s, subrole = ApplicationServices.AXUIElementCopyAttributeValue(
            el, ApplicationServices.kAXSubroleAttribute, None
        )

        role_str = str(role) if (err_r == 0 and role) else "Unknown"
        subrole_str = str(subrole) if (err_s == 0 and subrole) else "None"

        role_counts[role_str] += 1
        subrole_counts[subrole_str] += 1

        if search_field is None:
            is_subrole_match = (
                (subrole_str == "AXSearchField" or "SearchField" in subrole_str)
                and role_str in ("AXStaticText", "AXTextField", "AXSearchField")
            )
            is_role_match = (role_str == "AXSearchField")
            if is_subrole_match or is_role_match:
                search_field = el
                break

        if depth < max_depth:
            err_c, children = ApplicationServices.AXUIElementCopyAttributeValue(
                el, ApplicationServices.kAXChildrenAttribute, None
            )
            if err_c == 0 and children:
                for child in children:
                    queue.append((child, depth + 1))

    diag = {
        "windows": win_metas,
        "max_depth_reached": max_depth_reached,
        "role_counts": role_counts,
        "subrole_counts": subrole_counts,
    }
    return search_field, diag


def find_chat_list_container_bfs(app_element: Any, max_depth: int = 15) -> Optional[Any]:
    """Locate the scrollable chat list container element using BFS across all windows."""
    windows = get_app_windows(app_element)
    queue = deque()
    for w in windows:
        queue.append((w, 1))

    while queue:
        el, depth = queue.popleft()

        err_r, role = ApplicationServices.AXUIElementCopyAttributeValue(
            el, ApplicationServices.kAXRoleAttribute, None
        )
        err_act, actions = ApplicationServices.AXUIElementCopyActionNames(el, None)
        actions_list = list(actions) if (err_act == 0 and actions) else []

        err_c, children = ApplicationServices.AXUIElementCopyAttributeValue(
            el, ApplicationServices.kAXChildrenAttribute, None
        )
        children_list = list(children) if (err_c == 0 and children) else []
        count = len(children_list)

        role_str = str(role) if (err_r == 0 and role) else ""

        # Catalyst chat list is a container with scroll actions and children
        if (role_str in ("AXList", "AXTable", "AXOutline") or "AXScrollDownByPage" in actions_list) and count > 0:
            return el

        if depth < max_depth and children_list:
            for child in children_list:
                queue.append((child, depth + 1))

    return None


def format_diagnostic_report(
    is_trusted: bool,
    windows_info: List[Dict[str, Any]],
    max_depth_reached: int,
    role_counts: Counter,
    subrole_counts: Counter,
    catalyst_flags_attempted: bool,
) -> str:
    lines = [
        "=" * 70,
        "WHATSAPP ACCESSIBILITY STRUCTURE DIAGNOSTIC (SEARCH FIELD NOT FOUND)",
        f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 70,
        f"• Process Accessibility Trusted (AXIsProcessTrusted): {is_trusted}",
        f"• Mac Catalyst Assistive Flags Set: {catalyst_flags_attempted}",
        f"• Total Windows Detected: {len(windows_info)}",
    ]
    if windows_info:
        lines.append("  Window Details:")
        for w in windows_info:
            lines.append(
                f"    - Window #{w['index']}: Role={w['role']}, Subrole={w['subrole']}, Main={w['is_main']}, Minimized={w['is_minimized']}"
            )
    else:
        lines.append("  No accessible windows returned by kAXWindowsAttribute or kAXChildrenAttribute.")

    lines.append(f"• Tree Depth Reached: {max_depth_reached}")

    lines.append("\n• Role Histogram (Count):")
    if role_counts:
        for r, c in role_counts.most_common():
            lines.append(f"    {r}: {c}")
    else:
        lines.append("    (No elements traversed)")

    lines.append("\n• Subrole Histogram (Count):")
    if subrole_counts:
        for sr, c in subrole_counts.most_common():
            lines.append(f"    {sr}: {c}")
    else:
        lines.append("    (No subroles found)")

    lines.append("=" * 70)
    return "\n".join(lines)


def audit_container_children(container: Any) -> Tuple[int, List[Any], List[Dict[str, Any]]]:
    """
    Audit children of container using STRUCTURE ONLY.
    Returns: (total_children, accepted_rows, list_of_excluded_child_metadata)
    NEVER reads or prints any text or content attributes.
    """
    err, children = ApplicationServices.AXUIElementCopyAttributeValue(
        container, ApplicationServices.kAXChildrenAttribute, None
    )
    if err != 0 or not children:
        return 0, [], []

    total_count = len(children)
    accepted = []
    excluded = []

    for i, child in enumerate(children):
        err_r, role = ApplicationServices.AXUIElementCopyAttributeValue(
            child, ApplicationServices.kAXRoleAttribute, None
        )
        err_sub, subrole = ApplicationServices.AXUIElementCopyAttributeValue(
            child, ApplicationServices.kAXSubroleAttribute, None
        )
        err_act, actions = ApplicationServices.AXUIElementCopyActionNames(child, None)
        err_c, sub_children = ApplicationServices.AXUIElementCopyAttributeValue(
            child, ApplicationServices.kAXChildrenAttribute, None
        )

        role_str = str(role) if err_r == 0 and role else "Unknown"
        subrole_str = str(subrole) if err_sub == 0 and subrole else "None"
        actions_list = list(actions) if err_act == 0 and actions else []
        child_count = len(sub_children) if err_c == 0 and sub_children else 0

        is_header = role_str in ("AXHeading", "AXScrollBar", "AXSplitter") or ("Header" in subrole_str)
        has_press = "AXPress" in actions_list
        has_structure = child_count > 0 or role_str == "AXRow"

        if (not is_header) and has_press and has_structure:
            accepted.append(child)
        else:
            excluded.append({
                "index": i + 1,
                "role": role_str,
                "subrole": subrole_str,
                "child_count": child_count,
                "actions": actions_list,
                "reason": "Header" if is_header else ("No AXPress" if not has_press else "No child subviews"),
            })

    return total_count, accepted, excluded


def build_summary_text(summary: Dict[str, Any]) -> str:
    """Format structure-only summary of the experiment."""
    lines = [
        "=" * 70,
        "WHATSAPP EXPERIMENT STRUCTURE-ONLY SUMMARY",
        f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 70,
        f"Mac Catalyst Flags Needed:         {'YES' if summary.get('catalyst_flags_needed') else 'NO'}",
        f"Windows Count:                     {summary.get('windows_count', 1)}",
        f"Tree Depth Reached:                {summary.get('max_depth_reached', 0)}",
    ]
    if "step_a" in summary:
        s_a = summary["step_a"]
        lines.append(
            f"Step A (AXPress Search Focus):    {'PASS' if s_a.get('search_field_focused') else 'FAIL'} | Role: {s_a.get('focused_role')} | Subrole: {s_a.get('focused_subrole')} | Cursor in box: {'YES' if s_a.get('cursor_in_search_box') else 'NO'}"
        )
    if "step_c" in summary:
        s_c = summary["step_c"]
        lines.append(
            f"Step C (Pre-Paste Guard & Paste): {'PASS' if s_c.get('guard_passed') else 'BLOCKED'} | 'Alex Test' in box: {'YES' if s_c.get('alex_test_in_search_box') else 'NO'} | Chat filtered: {'YES' if s_c.get('chat_list_filtered') else 'NO'} | Rows: {s_c.get('filtered_accepted_rows', 0)}"
        )
    if "step_d" in summary:
        s_d = summary["step_d"]
        lines.append(
            f"Step D (AXCancel Clear Search):   {'PASS' if s_d.get('returned_full_list') else 'FAIL'} | Box emptied & returned: {'YES' if s_d.get('search_box_emptied_and_list_returned') else 'NO'} | Rows: {s_d.get('rows_after_cancel')}/{s_d.get('baseline_rows')}"
        )
    if "step_b" in summary:
        s_b = summary["step_b"]
        lines.append(
            f"Step B (Cmd+F from Unfocused):    {'PASS' if s_b.get('search_field_focused') else 'FAIL'} | Role: {s_b.get('focused_role')} | Subrole: {s_b.get('focused_subrole')} | Cursor in box: {'YES' if s_b.get('cursor_in_search_box') else 'NO'}"
        )
    if "step_e" in summary:
        s_e = summary["step_e"]
        if s_e.get("skipped"):
            lines.append(f"Step E (AXPress Row Selection):   SKIPPED ({s_e.get('reason')})")
        else:
            lines.append(
                f"Step E (AXPress Row Selection):   {'PASS' if s_e.get('chat_opened') else 'FAIL'} | Row #{s_e.get('row_index')}"
            )
    if "step_f" in summary:
        s_f = summary["step_f"]
        lines.append(
            f"Step F (Post-Open Focus Role):    Role: {s_f.get('focused_role')} | Subrole: {s_f.get('focused_subrole')}"
        )
    lines.append("=" * 70)
    return "\n".join(lines)


def check_is_focused(element: Any, app_element: Any) -> Tuple[bool, str, str]:
    """Check if element is focused and return focused element's role/subrole."""
    err, is_focused = ApplicationServices.AXUIElementCopyAttributeValue(
        element, ApplicationServices.kAXFocusedAttribute, None
    )
    focused_bool = bool(err == 0 and is_focused)

    err, focused_el = ApplicationServices.AXUIElementCopyAttributeValue(
        app_element, ApplicationServices.kAXFocusedUIElementAttribute, None
    )
    f_role = "None"
    f_subrole = "None"
    if err == 0 and focused_el:
        err_r, r = ApplicationServices.AXUIElementCopyAttributeValue(
            focused_el, ApplicationServices.kAXRoleAttribute, None
        )
        if err_r == 0 and r:
            f_role = str(r)
        err_sr, sr = ApplicationServices.AXUIElementCopyAttributeValue(
            focused_el, ApplicationServices.kAXSubroleAttribute, None
        )
        if err_sr == 0 and sr:
            f_subrole = str(sr)

    return focused_bool, f_role, f_subrole


def main() -> int:
    print("\n" + "=" * 70)
    print("WHATSAPP CONTROLLED INTERACTIVE EXPERIMENT (PHASE 0 - REVISED)")
    print("=" * 70)

    # 1. Capture user's Terminal application for Focus Handback
    terminal_app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if not terminal_app or terminal_app.bundleIdentifier() == WHATSAPP_BUNDLE_ID:
        print("❌ Error: Please run this script directly from Terminal, iTerm2, or IDE.")
        return 1

    term_name = terminal_app.localizedName() or "Terminal"
    print(f"• Recorded Control Terminal: {term_name} (PID: {terminal_app.processIdentifier()})")
    print("• Focus Handback is ACTIVE: Terminal will be re-focused after EVERY step.")

    # 2. Check WhatsApp process
    wa_app = get_whatsapp_app()
    if not wa_app:
        print("\n❌ WhatsApp is not running. Please open WhatsApp first: open -a WhatsApp")
        return 1

    print(f"• Found WhatsApp process (PID: {wa_app.processIdentifier()})")
    app_el = ApplicationServices.AXUIElementCreateApplication(wa_app.processIdentifier())

    is_trusted = bool(ApplicationServices.AXIsProcessTrusted())
    print(f"• Accessibility Permission Trusted (AXIsProcessTrusted): {is_trusted}")
    if not is_trusted:
        print("  ⚠️ WARNING: AXIsProcessTrusted() is False. Terminal may require Accessibility permission in System Settings.")

    print("• Scanning WhatsApp UI tree (BFS traversal across all windows, max depth 15)...")
    search_field, diag = find_search_field_bfs(app_el, max_depth=15)
    catalyst_needed = False

    if not search_field:
        print("• Search field not found on initial pass.")
        print("• Setting Mac Catalyst assistive flags (AXManualAccessibility & AXEnhancedUserInterface)...")
        enable_catalyst_accessibility(app_el)
        print("• Waiting 1.0s for Mac Catalyst UIKit tree to instantiate...")
        time.sleep(1.0)
        search_field, diag = find_search_field_bfs(app_el, max_depth=15)
        if search_field:
            catalyst_needed = True
            print("• SUCCESS: Search field located after enabling Mac Catalyst assistive flags!")

    if not search_field:
        print("\n❌ Could not locate search field in WhatsApp window tree (even after Mac Catalyst flags).")
        diag_report = format_diagnostic_report(
            is_trusted=is_trusted,
            windows_info=diag["windows"],
            max_depth_reached=diag["max_depth_reached"],
            role_counts=diag["role_counts"],
            subrole_counts=diag["subrole_counts"],
            catalyst_flags_attempted=True,
        )
        print("\n" + diag_report + "\n")

        summary_file = PROJECT_ROOT / "whatsapp_experiment_summary.txt"
        try:
            summary_file.write_text(diag_report, encoding="utf-8")
            print(f"Diagnostic report written to: {summary_file.name} (gitignored)")
        except Exception as e:
            print(f"Could not write summary file: {e}")

        print("\n👉 Please copy and paste the diagnostic summary above into the chat.")
        return 1

    container = find_chat_list_container_bfs(app_el, max_depth=15)
    if not container:
        if not catalyst_needed:
            print("• Chat container not found on initial pass. Enabling Mac Catalyst assistive flags...")
            enable_catalyst_accessibility(app_el)
            time.sleep(1.0)
            container = find_chat_list_container_bfs(app_el, max_depth=15)
            if container:
                catalyst_needed = True
                print("• SUCCESS: Chat container located after enabling Mac Catalyst assistive flags!")

    if not container:
        print("\n❌ Could not locate chat list container in WhatsApp window tree.")
        diag_report = format_diagnostic_report(
            is_trusted=is_trusted,
            windows_info=diag["windows"],
            max_depth_reached=diag["max_depth_reached"],
            role_counts=diag["role_counts"],
            subrole_counts=diag["subrole_counts"],
            catalyst_flags_attempted=True,
        )
        print("\n" + diag_report + "\n")

        summary_file = PROJECT_ROOT / "whatsapp_experiment_summary.txt"
        try:
            summary_file.write_text(diag_report, encoding="utf-8")
            print(f"Diagnostic report written to: {summary_file.name} (gitignored)")
        except Exception as e:
            print(f"Could not write summary file: {e}")

        print("\n👉 Please copy and paste the diagnostic summary above into the chat.")
        return 1

    print(f"• Located Search Field and Chat List Container. (Mac Catalyst flags needed: {'YES' if catalyst_needed else 'NO'})")

    summary: Dict[str, Any] = {
        "catalyst_flags_needed": catalyst_needed,
        "windows_count": len(diag["windows"]),
        "max_depth_reached": diag["max_depth_reached"],
    }

    # Initial baseline container audit
    tot_base, rows_base, excl_base = audit_container_children(container)
    print(f"\n[Baseline Normal Chat List Audit]")
    print(f"• Total Children in Container:   {tot_base}")
    print(f"• Filter-Accepted Chat Rows:     {len(rows_base)}")
    print(f"• Filter-Excluded Children:      {len(excl_base)}")
    if excl_base:
        print("  Excluded child structure (index | role | subrole | children | reason):")
        for ex in excl_base:
            print(f"    #{ex['index']}: {ex['role']} (subrole: {ex['subrole']}, children: {ex['child_count']}) -> {ex['reason']}")

    try:
        # -------------------------------------------------------------
        # STEP A: AXPress on Search Field
        # -------------------------------------------------------------
        print("\n" + "=" * 70)
        print("STEP A: Focus Search Field via AXPress Action")
        print("=" * 70)
        print_safety_warning()
        print("• Action: Call AXUIElementPerformAction(search_field, 'AXPress').")
        print("• Safety: Zero keystrokes posted.")
        safe_input("👉 Press [Enter] to execute Step A...", terminal_app)

        activate_whatsapp(wa_app)
        err_a = ApplicationServices.AXUIElementPerformAction(search_field, "AXPress")
        time.sleep(0.2)
        is_foc_a, role_a, subrole_a = check_is_focused(search_field, app_el)
        handback_focus(terminal_app)

        print(f"\n[Step A Result]")
        print(f"• Search Field Focused:      {is_foc_a}")
        print(f"• Focused Element Role:      {role_a}")
        print(f"• Focused Element Subrole:   {subrole_a}")
        print(f"• Matches 'AXSearchField':   {subrole_a == 'AXSearchField'}")
        ans_a = safe_input("👀 Look at WhatsApp: Is the cursor in the search box? [y/n]: ", terminal_app).strip().lower()
        summary["step_a"] = {
            "action": "AXPress on search field",
            "search_field_focused": is_foc_a,
            "focused_role": role_a,
            "focused_subrole": subrole_a,
            "matches_guard": subrole_a == "AXSearchField",
            "cursor_in_search_box": ans_a.startswith("y"),
        }

        # -------------------------------------------------------------
        # STEP C: Pre-Paste Focus Guard & Paste 'Alex Test'
        # -------------------------------------------------------------
        print("\n" + "=" * 70)
        print("STEP C: Test Pre-Paste Focus Guard & Paste 'Alex Test'")
        print("=" * 70)
        print_safety_warning()
        print("• Guard: Checks focused element subrole == 'AXSearchField'. If NOT, paste is ABORTED.")
        print("• Action: Paste 'Alex Test' via Cmd+V (keycode 9 with Command flag) and restore clipboard.")
        safe_input("👉 Press [Enter] to execute Step C...", terminal_app)

        activate_whatsapp(wa_app)
        is_foc_c, role_c, subrole_c = check_is_focused(search_field, app_el)

        guard_passed = (subrole_c == "AXSearchField")
        tot_after_c = 0
        accepted_after_c: List[Any] = []
        excl_after_c: List[Dict[str, Any]] = []

        if not guard_passed:
            handback_focus(terminal_app)
            print(f"\n❌ FOCUS GUARD BLOCKED PASTE: Focused subrole is '{subrole_c}' != 'AXSearchField'.")
            summary["step_c"] = {"guard_passed": False, "pasted": False}
        else:
            tot_pre, accepted_pre, _ = audit_container_children(container)

            pb = NSPasteboard.generalPasteboard()
            orig_text = pb.stringForType_(NSPasteboardTypeString)
            try:
                pb.clearContents()
                pb.setString_forType_("Alex Test", NSPasteboardTypeString)
                try:
                    pb.setString_forType_("1", "org.nspasteboard.TransientType")
                except Exception:
                    pass

                post_whitelisted_key(9, Quartz.kCGEventFlagMaskCommand)
                time.sleep(0.4)  # Allow search filter to populate

                tot_after_c, accepted_after_c, excl_after_c = audit_container_children(container)
            finally:
                pb.clearContents()
                if orig_text is not None:
                    pb.setString_forType_(orig_text, NSPasteboardTypeString)

            handback_focus(terminal_app)

            print(f"\n[Step C Result]")
            print(f"• Focus Guard Passed:        {guard_passed}")
            print(f"• Pre-Paste Rows Count:      {len(accepted_pre)}")
            print(f"• Filtered Total Children:   {tot_after_c}")
            print(f"• Filtered Accepted Rows:    {len(accepted_after_c)}")
            print(f"• Filtered Excluded Items:   {len(excl_after_c)}")
            if excl_after_c:
                print("  Excluded items during search (sections/headers):")
                for ex in excl_after_c:
                    print(f"    #{ex['index']}: {ex['role']} (subrole: {ex['subrole']}) -> {ex['reason']}")

            ans_c1 = safe_input("👀 Look at WhatsApp: Did 'Alex Test' appear in the search box? [y/n]: ", terminal_app).strip().lower()
            ans_c2 = safe_input("👀 Look at WhatsApp: Did the chat list filter? [y/n]: ", terminal_app).strip().lower()
            summary["step_c"] = {
                "guard_passed": True,
                "pasted": True,
                "pre_rows": len(accepted_pre),
                "filtered_total": tot_after_c,
                "filtered_accepted_rows": len(accepted_after_c),
                "filtered_excluded": len(excl_after_c),
                "alex_test_in_search_box": ans_c1.startswith("y"),
                "chat_list_filtered": ans_c2.startswith("y"),
            }

        # -------------------------------------------------------------
        # STEP D: Clear Search via AXCancel on Search Field
        # -------------------------------------------------------------
        print("\n" + "=" * 70)
        print("STEP D: Test Search Clear & Dismiss via AXCancel Action")
        print("=" * 70)
        print_safety_warning()
        print("• Action: Call AXUIElementPerformAction(search_field, 'AXCancel').")
        print("• Safety: Zero keystrokes (no Delete, no Cmd+A).")
        print("• Goal:   Verify if AXCancel restores the full chat list count.")
        safe_input("👉 Press [Enter] to execute Step D...", terminal_app)

        activate_whatsapp(wa_app)
        err_d = ApplicationServices.AXUIElementPerformAction(search_field, "AXCancel")
        time.sleep(0.4)
        tot_d, accepted_d, _ = audit_container_children(container)
        handback_focus(terminal_app)

        returned_full = (len(accepted_d) == len(rows_base))
        print(f"\n[Step D Result]")
        print(f"• AXCancel Call Return:      {'SUCCESS (0)' if err_d == 0 else f'ERROR ({err_d})'}")
        print(f"• Rows After AXCancel:       {len(accepted_d)} (Baseline was {len(rows_base)})")
        print(f"• Restored Full Chat List:   {'YES' if returned_full else 'NO'}")

        ans_d = safe_input("👀 Look at WhatsApp: Did the search box empty and the full list return? [y/n]: ", terminal_app).strip().lower()
        summary["step_d"] = {
            "action": "AXCancel on search field",
            "returned_full_list": returned_full,
            "rows_after_cancel": len(accepted_d),
            "baseline_rows": len(rows_base),
            "search_box_emptied_and_list_returned": ans_d.startswith("y"),
        }

        # -------------------------------------------------------------
        # STEP B: Focus Search Field via Cmd+F from Unfocused State (After AXCancel)
        # -------------------------------------------------------------
        print("\n" + "=" * 70)
        print("STEP B: Focus Search Field via Cmd+F (From Unfocused State)")
        print("=" * 70)
        print_safety_warning()
        print("• Action: Post Cmd+F (keycode 3 with Command modifier) after AXCancel dismissed search.")
        print("• Safety: Whitelisted keycode only; no Return.")
        safe_input("👉 Press [Enter] to execute Step B (Cmd+F from unfocused state)...", terminal_app)

        activate_whatsapp(wa_app)
        post_whitelisted_key(3, Quartz.kCGEventFlagMaskCommand)
        time.sleep(0.25)
        is_foc_b, role_b, subrole_b = check_is_focused(search_field, app_el)
        handback_focus(terminal_app)

        print(f"\n[Step B Result]")
        print(f"• Search Field Focused:      {is_foc_b}")
        print(f"• Focused Element Role:      {role_b}")
        print(f"• Focused Element Subrole:   {subrole_b}")
        print(f"• Matches 'AXSearchField':   {subrole_b == 'AXSearchField'}")
        ans_b = safe_input("👀 Look at WhatsApp: Is the cursor in the search box after Cmd+F? [y/n]: ", terminal_app).strip().lower()
        summary["step_b"] = {
            "action": "Cmd+F from unfocused state",
            "search_field_focused": is_foc_b,
            "focused_role": role_b,
            "focused_subrole": subrole_b,
            "matches_guard": subrole_b == "AXSearchField",
            "cursor_in_search_box": ans_b.startswith("y"),
        }

        # Dismiss search again with AXCancel to leave clean state for Step E
        activate_whatsapp(wa_app)
        ApplicationServices.AXUIElementPerformAction(search_field, "AXCancel")
        time.sleep(0.2)
        handback_focus(terminal_app)

        # -------------------------------------------------------------
        # STEP E: Select Row N via AXPress Action
        # -------------------------------------------------------------
        print("\n" + "=" * 70)
        print("STEP E: Select Row N via AXPress Action (Zero Keystrokes, NO Return)")
        print("=" * 70)
        print_safety_warning()

        current_tot, current_accepted, _ = audit_container_children(container)
        print(f"• Current selectable chat rows: {len(current_accepted)}")

        if len(current_accepted) == 0:
            print("⚠️ SKIPPING Step E: 0 selectable chat rows detected in container.")
            print("   (The probed container had 0 interactive row children).")
            summary["step_e"] = {
                "skipped": True,
                "reason": "0 selectable chat rows detected in container",
            }
        else:
            row_choice = safe_input(
                f"Enter row index to open (1 to {len(current_accepted)}) [default 1]: ",
                terminal_app,
            ).strip()
            row_idx = int(row_choice) if row_choice.isdigit() and 1 <= int(row_choice) <= len(current_accepted) else 1

            print(f"• Target: Row #{row_idx}")
            safe_input(f"👉 Press [Enter] to execute AXPress on Row #{row_idx}...", terminal_app)

            activate_whatsapp(wa_app)
            err_e = ApplicationServices.AXUIElementPerformAction(current_accepted[row_idx - 1], "AXPress")
            time.sleep(0.35)
            handback_focus(terminal_app)

            print(f"\n[Step E Result]")
            print(f"• AXPress Call Return:       {'SUCCESS (0)' if err_e == 0 else f'ERROR ({err_e})'}")
            user_obs = safe_input("👀 Look at WhatsApp on screen: Did the chat open? [y/n]: ", terminal_app).strip().lower()
            confirmed = user_obs.startswith("y")
            print(f"• User Confirmed Opened:     {'YES' if confirmed else 'NO'}")
            summary["step_e"] = {
                "skipped": False,
                "row_index": row_idx,
                "axpress_success": err_e == 0,
                "chat_opened": confirmed,
            }

        # -------------------------------------------------------------
        # STEP F: Post-Selection Focus Role Inspection
        # -------------------------------------------------------------
        print("\n" + "=" * 70)
        print("STEP F: Inspect Focused Element Role After Chat Opens")
        print("=" * 70)
        print("• Action: Query kAXFocusedUIElementAttribute from WhatsApp.")
        print("• Safety: Queries ROLE and SUBROLE ONLY. Never reads text, values, or names.")
        safe_input("👉 Press [Enter] to inspect focused element role...", terminal_app)

        activate_whatsapp(wa_app)
        time.sleep(0.1)
        err_f, focused_f = ApplicationServices.AXUIElementCopyAttributeValue(
            app_el, ApplicationServices.kAXFocusedUIElementAttribute, None
        )
        f_role_f = "None"
        f_subrole_f = "None"
        if err_f == 0 and focused_f:
            err_rf, rf = ApplicationServices.AXUIElementCopyAttributeValue(
                focused_f, ApplicationServices.kAXRoleAttribute, None
            )
            if err_rf == 0 and rf:
                f_role_f = str(rf)
            err_srf, srf = ApplicationServices.AXUIElementCopyAttributeValue(
                focused_f, ApplicationServices.kAXSubroleAttribute, None
            )
            if err_srf == 0 and srf:
                f_subrole_f = str(srf)
        handback_focus(terminal_app)

        print(f"\n[Step F Result]")
        print(f"• Focused Element Role:      {f_role_f}")
        print(f"• Focused Element Subrole:   {f_subrole_f}")
        summary["step_f"] = {
            "focused_role": f_role_f,
            "focused_subrole": f_subrole_f,
        }

        return 0

    finally:
        summary_text = build_summary_text(summary)
        print("\n" + summary_text + "\n")

        summary_file = PROJECT_ROOT / "whatsapp_experiment_summary.txt"
        try:
            summary_file.write_text(summary_text, encoding="utf-8")
            print(f"Summary written to: {summary_file.name} (gitignored)")
        except Exception as e:
            print(f"Could not write summary file: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
