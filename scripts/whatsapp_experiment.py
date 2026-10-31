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


def find_search_field(app_element: Any) -> Optional[Any]:
    """Locate the search field element in the WhatsApp window tree."""
    err, windows = ApplicationServices.AXUIElementCopyAttributeValue(
        app_element, ApplicationServices.kAXWindowsAttribute, None
    )
    if err != 0 or not windows:
        return None

    target = None

    def _walk(el: Any, depth: int):
        nonlocal target
        if target is not None or depth > 10:
            return

        err, role = ApplicationServices.AXUIElementCopyAttributeValue(
            el, ApplicationServices.kAXRoleAttribute, None
        )
        err_sub, subrole = ApplicationServices.AXUIElementCopyAttributeValue(
            el, ApplicationServices.kAXSubroleAttribute, None
        )

        role_str = str(role) if err == 0 and role else ""
        subrole_str = str(subrole) if err_sub == 0 and subrole else ""

        if "SearchField" in subrole_str or "SearchField" in role_str:
            target = el
            return

        err_c, children = ApplicationServices.AXUIElementCopyAttributeValue(
            el, ApplicationServices.kAXChildrenAttribute, None
        )
        if err_c == 0 and children:
            for child in children:
                _walk(child, depth + 1)

    _walk(windows[0], 1)
    return target


def find_chat_list_container(app_element: Any) -> Optional[Any]:
    """Locate the scrollable chat list container element."""
    err, windows = ApplicationServices.AXUIElementCopyAttributeValue(
        app_element, ApplicationServices.kAXWindowsAttribute, None
    )
    if err != 0 or not windows:
        return None

    container = None

    def _walk(el: Any, depth: int):
        nonlocal container
        if container is not None or depth > 10:
            return

        err, role = ApplicationServices.AXUIElementCopyAttributeValue(
            el, ApplicationServices.kAXRoleAttribute, None
        )
        err_act, actions = ApplicationServices.AXUIElementCopyActionNames(el, None)
        actions_list = list(actions) if (err_act == 0 and actions) else []

        err_c, children = ApplicationServices.AXUIElementCopyAttributeValue(
            el, ApplicationServices.kAXChildrenAttribute, None
        )
        count = len(children) if (err_c == 0 and children) else 0

        # Catalyst chat list is a container with scroll actions and children
        if (role in ("AXList", "AXTable", "AXOutline") or "AXScrollDownByPage" in actions_list) and count > 0:
            container = el
            return

        if err_c == 0 and children:
            for child in children:
                _walk(child, depth + 1)

    _walk(windows[0], 1)
    return container


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

    search_field = find_search_field(app_el)
    container = find_chat_list_container(app_el)

    if not search_field:
        print("❌ Could not locate search field in WhatsApp window tree.")
        return 1
    if not container:
        print("❌ Could not locate chat list container in WhatsApp window tree.")
        return 1

    print("• Located Search Field and Chat List Container.")

    summary: Dict[str, Any] = {}

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
    summary["step_a"] = {
        "action": "AXPress on search field",
        "search_field_focused": is_foc_a,
        "focused_role": role_a,
        "focused_subrole": subrole_a,
        "matches_guard": subrole_a == "AXSearchField",
    }

    # -------------------------------------------------------------
    # STEP B: Cmd+F on Search Field
    # -------------------------------------------------------------
    print("\n" + "=" * 70)
    print("STEP B: Focus Search Field via Cmd+F")
    print("=" * 70)
    print_safety_warning()
    print("• Action: Post Cmd+F (keycode 3 with Command modifier).")
    print("• Safety: Whitelisted keycode only; no Return.")
    safe_input("👉 Press [Enter] to execute Step B...", terminal_app)

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
    summary["step_b"] = {
        "action": "Cmd+F",
        "search_field_focused": is_foc_b,
        "focused_role": role_b,
        "focused_subrole": subrole_b,
        "matches_guard": subrole_b == "AXSearchField",
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
        # Pre-paste row count
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

        summary["step_c"] = {
            "guard_passed": True,
            "pasted": True,
            "pre_rows": len(accepted_pre),
            "filtered_total": tot_after_c,
            "filtered_accepted_rows": len(accepted_after_c),
            "filtered_excluded": len(excl_after_c),
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
    summary["step_d"] = {
        "action": "AXCancel on search field",
        "returned_full_list": returned_full,
        "rows_after_cancel": len(accepted_d),
        "baseline_rows": len(rows_base),
    }

    # -------------------------------------------------------------
    # STEP E: Select Row N via AXPress Action
    # -------------------------------------------------------------
    print("\n" + "=" * 70)
    print("STEP E: Select Row N via AXPress Action (Zero Keystrokes, NO Return)")
    print("=" * 70)
    print_safety_warning()

    current_tot, current_accepted, _ = audit_container_children(container)
    print(f"• Current selectable chat rows: {len(current_accepted)}")
    row_choice = safe_input(f"Enter row index to open (1 to {len(current_accepted)}) [default 1]: ", terminal_app).strip()
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

    # -------------------------------------------------------------
    # SUMMARY BLOCK (SAFE TO COPY AND PASTE)
    # -------------------------------------------------------------
    summary_lines = [
        "======================================================================",
        "WHATSAPP EXPERIMENT STRUCTURE-ONLY SUMMARY",
        f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "======================================================================",
        f"Step A (AXPress Search Focus):    {'PASS' if summary['step_a']['search_field_focused'] else 'FAIL'} | Role: {summary['step_a']['focused_role']} | Subrole: {summary['step_a']['focused_subrole']}",
        f"Step B (Cmd+F Search Focus):      {'PASS' if summary['step_b']['search_field_focused'] else 'FAIL'} | Role: {summary['step_b']['focused_role']} | Subrole: {summary['step_b']['focused_subrole']}",
        f"Focus Guard Matches Subrole:      {'YES' if summary['step_a']['matches_guard'] or summary['step_b']['matches_guard'] else 'NO'}",
        f"Step C (Pre-Paste Guard & Paste): {'PASS' if summary['step_c']['guard_passed'] else 'BLOCKED'} | Pre: {summary['step_c'].get('pre_rows', 0)} -> Filtered Rows: {summary['step_c'].get('filtered_accepted_rows', 0)} (Excluded: {summary['step_c'].get('filtered_excluded', 0)})",
        f"Step D (AXCancel Clear Search):   {'PASS' if summary['step_d']['returned_full_list'] else 'FAIL'} | Restored: {summary['step_d']['rows_after_cancel']}/{summary['step_d']['baseline_rows']} rows",
        f"Step E (AXPress Row Selection):   {'PASS' if summary['step_e']['chat_opened'] else 'FAIL'} | Row #{summary['step_e']['row_index']}",
        f"Step F (Post-Open Focus Role):    Role: {summary['step_f']['focused_role']} | Subrole: {summary['step_f']['focused_subrole']}",
        "======================================================================",
    ]
    summary_text = "\n".join(summary_lines)

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
