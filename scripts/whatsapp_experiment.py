#!/usr/bin/env python3
"""
WhatsApp Desktop Controlled Interactive Experiment (Phase 0 - Geometry & Safety Edition).
Tests search field focusing (AXPress vs Cmd+F), pre-paste focus guard (subrole AXSearchField only),
live search filtering, search clearing via AXCancel, geometry-based row identification,
and post-selection focus role.

CRITICAL SAFETY & PRIVACY RULES:
1. STRICT APP ACTIVATION & FRONTMOST VERIFICATION:
   Before every action step, activates WhatsApp, waits up to 2.0s until confirmed frontmost,
   and aborts if not.
   Immediately before posting ANY key, verifies frontmost is net.whatsapp.WhatsApp and prints
   the frontmost bundle ID.
2. FOCUS HANDBACK:
   After every step, re-activates Terminal and confirms it is frontmost BEFORE printing any
   prompt. Pressing Enter in Terminal can never reach WhatsApp.
3. EXPLICIT INPUT:
   Questions require explicit 'y' or 'n' input; blank input is rejected.
4. NEVER-SEND:
   Keycode whitelist is strictly {3 (Cmd+F), 9 (Cmd+V)}. Return/Enter is completely absent.
5. GEOMETRY-BASED ROW TARGETING:
   Rows are AXGroup wrappers (~550 wide, ~76 tall) in the sidebar with an AXButton child
   supporting AXPress. Pressing acts on the child AXButton, not the wrapper.
6. STRUCTURE ONLY:
   Never reads, stores, or prints text, values, descriptions, help, labels, or content.
"""

from collections import Counter, deque
from datetime import datetime
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

# Auto-reexec inside the virtual environment if available
PROJECT_ROOT = Path(__file__).resolve().parent.parent
venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
if venv_python.is_file() and sys.executable != str(venv_python):
    try:
        os.execv(str(venv_python), [str(venv_python)] + sys.argv)
    except Exception:
        pass

# Include scripts directory in sys.path for shared modules
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

try:
    import ApplicationServices
    from AppKit import (
        NSPasteboard,
        NSPasteboardTypeString,
        NSRunningApplication,
        NSScreen,
        NSWorkspace,
    )
    from Foundation import NSDate, NSRunLoop
    import Quartz
except ImportError as e:
    print(f"Error: Required macOS frameworks (PyObjC / Quartz) not available: {e}")
    sys.exit(1)

from whatsapp_geometry import (
    enable_catalyst_accessibility,
    extract_point,
    extract_size,
    find_chat_rows_geometry,
    find_search_field,
    get_main_window,
    get_sidebar_x_range,
)

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


def get_frontmost_bundle_nsworkspace() -> str:
    """Check frontmost app bundle ID via NSWorkspace."""
    ws = NSWorkspace.sharedWorkspace()
    front = ws.frontmostApplication()
    if front and front.bundleIdentifier():
        return str(front.bundleIdentifier())
    return "None"


def get_frontmost_bundle_lsappinfo() -> str:
    """
    Independent frontmost check via LaunchServices daemon CLI (`lsappinfo`).
    Queries launchservicesd directly, bypassing NSWorkspace / event loop.
    """
    try:
        asn = subprocess.check_output(
            ["lsappinfo", "front"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if not asn:
            return "None"
        info = subprocess.check_output(
            ["lsappinfo", "info", "-only", "bundleid", asn],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        m = re.search(r'bundleID="([^"]+)"', info)
        if m:
            return m.group(1)
        for line in info.splitlines():
            line = line.strip()
            if "bundleID=" in line:
                return line.split("bundleID=", 1)[1].strip().strip('"')
    except Exception:
        pass
    return "None"


def get_frontmost_bundle_cgwindow() -> str:
    """Alternative independent check via Quartz WindowServer layer-0 on-screen window list."""
    try:
        options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
        win_list = Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID)
        if win_list:
            for win in win_list:
                layer = win.get("kCGWindowLayer", -1)
                if layer == 0:
                    pid = win.get("kCGWindowOwnerPID")
                    if pid:
                        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
                        if app and app.bundleIdentifier():
                            return str(app.bundleIdentifier())
    except Exception:
        pass
    return "None"


def check_frontmost_dual() -> Tuple[str, str, bool]:
    """
    Query frontmost app via both NSWorkspace and independent check (lsappinfo).
    Returns: (nsworkspace_bundle, independent_bundle, both_are_whatsapp)
    """
    b_nsws = get_frontmost_bundle_nsworkspace()
    b_indep = get_frontmost_bundle_lsappinfo()
    if b_indep == "None":
        b_indep = get_frontmost_bundle_cgwindow()
    both_wa = (b_nsws == WHATSAPP_BUNDLE_ID and b_indep == WHATSAPP_BUNDLE_ID)
    return b_nsws, b_indep, both_wa


def evaluate_dual_check(b_nsws: str, b_indep: str, context: str = "") -> Dict[str, Any]:
    """
    Evaluate agreement between NSWorkspace and independent (lsappinfo) frontmost checks.
    Categorizes result into:
    - AGREE_WHATSAPP: Both report net.whatsapp.WhatsApp
    - AGREE_OTHER: Both agree on another application (e.g. Terminal)
    - DISAGREE: One reports WhatsApp and the other does not, or they report different apps.
    Records which check disagreed so we learn which check is unreliable.
    """
    both_wa = (b_nsws == WHATSAPP_BUNDLE_ID and b_indep == WHATSAPP_BUNDLE_ID)
    both_agree = (b_nsws == b_indep)

    if both_wa:
        status = "AGREE_WHATSAPP"
        disagreement_note = f"AGREED on WhatsApp (NSWorkspace='{b_nsws}', lsappinfo='{b_indep}')"
        unreliable_check = "None"
    elif both_agree:
        status = "AGREE_OTHER"
        disagreement_note = f"AGREED on other app (both reported '{b_nsws}')"
        unreliable_check = "None"
    else:
        status = "DISAGREE"
        if b_nsws == WHATSAPP_BUNDLE_ID and b_indep != WHATSAPP_BUNDLE_ID:
            disagreement_note = f"DISAGREEMENT: NSWorkspace detected WhatsApp, but lsappinfo reported '{b_indep}'"
            unreliable_check = "lsappinfo"
        elif b_indep == WHATSAPP_BUNDLE_ID and b_nsws != WHATSAPP_BUNDLE_ID:
            disagreement_note = f"DISAGREEMENT: lsappinfo detected WhatsApp, but NSWorkspace reported '{b_nsws}'"
            unreliable_check = "NSWorkspace"
        else:
            disagreement_note = f"DISAGREEMENT: NSWorkspace reported '{b_nsws}', lsappinfo reported '{b_indep}'"
            unreliable_check = "Both"

    return {
        "context": context,
        "status": status,
        "nsworkspace": b_nsws,
        "lsappinfo": b_indep,
        "both_whatsapp": both_wa,
        "both_agree": both_agree,
        "disagreement_note": disagreement_note,
        "unreliable_check": unreliable_check,
    }


def activate_whatsapp_and_verify(
    app: NSRunningApplication,
    timeout_sec: float = 3.0,
    step_name: str = "",
    summary: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Activate WhatsApp and verify it is frontmost using dual checks:
    1. Check if already frontmost (pumps run loop).
    2. Bring WhatsApp forward with system launcher: subprocess `open -b net.whatsapp.WhatsApp`
       and poll up to timeout_sec (3 s), pumping NSRunLoop on every poll.
    3. If still not frontmost, second attempt using NSRunningApplication.activateWithOptions_,
       polling and pumping NSRunLoop.
    4. Manual focus mode: if automatic activation fails, prompt user to click WhatsApp window,
       then run a 10-second countdown polling every 0.5 s and pumping NSRunLoop.
       Prints both results on each poll, and prints which method detected WhatsApp.
       No Enter press is needed in manual mode (prevents Enter from reaching WhatsApp).
    5. Records all check results and any disagreements into summary.
    """
    # 0. Initial check
    now = NSDate.date()
    NSRunLoop.currentRunLoop().runUntilDate_(now.dateByAddingTimeInterval_(0.1))
    b_nsws, b_indep, both_wa = check_frontmost_dual()
    print(f"• Initial dual frontmost check: NSWorkspace='{b_nsws}', lsappinfo='{b_indep}'")
    if both_wa:
        rec = evaluate_dual_check(b_nsws, b_indep, context=f"{step_name} (Initial)")
        if summary is not None:
            summary.setdefault("dual_check_records", []).append(rec)
        return True

    # 1. System launcher: open -b net.whatsapp.WhatsApp
    print("• Bringing WhatsApp forward via system launcher: open -b net.whatsapp.WhatsApp...")
    try:
        subprocess.run(["open", "-b", WHATSAPP_BUNDLE_ID], capture_output=True, check=False)
    except Exception as e:
        print(f"  (System launcher notice: {e})")

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        now = NSDate.date()
        NSRunLoop.currentRunLoop().runUntilDate_(now.dateByAddingTimeInterval_(0.1))
        b_nsws, b_indep, both_wa = check_frontmost_dual()
        print(f"  [Launcher poll] NSWorkspace='{b_nsws}', lsappinfo='{b_indep}'")
        if both_wa:
            print("✅ Detected WhatsApp frontmost via BOTH checks (launcher)!")
            rec = evaluate_dual_check(b_nsws, b_indep, context=f"{step_name} (Launcher)")
            if summary is not None:
                summary.setdefault("dual_check_records", []).append(rec)
            return True

    # 2. Second attempt: NSRunningApplication.activateWithOptions_
    print("• Second activation attempt: NSRunningApplication.activateWithOptions_...")
    app.activateWithOptions_(ApplicationServices.NSApplicationActivateIgnoringOtherApps)
    second_deadline = time.time() + 1.0
    while time.time() < second_deadline:
        now = NSDate.date()
        NSRunLoop.currentRunLoop().runUntilDate_(now.dateByAddingTimeInterval_(0.1))
        b_nsws, b_indep, both_wa = check_frontmost_dual()
        print(f"  [ActivateWithOptions poll] NSWorkspace='{b_nsws}', lsappinfo='{b_indep}'")
        if both_wa:
            print("✅ Detected WhatsApp frontmost via BOTH checks (activateWithOptions)!")
            rec = evaluate_dual_check(b_nsws, b_indep, context=f"{step_name} (ActivateWithOptions)")
            if summary is not None:
                summary.setdefault("dual_check_records", []).append(rec)
            return True

    # 3. Manual focus mode (extended to 10s)
    b_nsws, b_indep, both_wa = check_frontmost_dual()
    print(f"\n⚠️ Automatic activation did not bring WhatsApp frontmost.")
    print(f"   Current status: NSWorkspace='{b_nsws}', lsappinfo='{b_indep}'")
    print("👉 MANUAL FOCUS MODE: Please click the WhatsApp window with your mouse now!")
    print("   (Waiting up to 10s, checking every 0.5s; do NOT press Enter)...")

    countdown_deadline = time.time() + 10.0
    tick = 0

    while time.time() < countdown_deadline:
        now = NSDate.date()
        NSRunLoop.currentRunLoop().runUntilDate_(now.dateByAddingTimeInterval_(0.1))
        time.sleep(0.4)
        tick += 1

        b_nsws, b_indep, both_wa = check_frontmost_dual()
        print(f"   [Manual poll #{tick}] NSWorkspace='{b_nsws}' | lsappinfo='{b_indep}'")

        if both_wa:
            detected_method = "BOTH (NSWorkspace and lsappinfo agreed)"
            print(f"\n✅ WhatsApp frontmost detected by: {detected_method}! Continuing with step...")
            rec = evaluate_dual_check(b_nsws, b_indep, context=f"{step_name} (Manual Focus)")
            if summary is not None:
                summary.setdefault("dual_check_records", []).append(rec)
            return True

        wa_nsws = (b_nsws == WHATSAPP_BUNDLE_ID)
        wa_indep = (b_indep == WHATSAPP_BUNDLE_ID)

        detected_method = None
        if wa_nsws and not wa_indep:
            detected_method = f"NSWorkspace ONLY (lsappinfo reported '{b_indep}')"
        elif wa_indep and not wa_nsws:
            detected_method = f"lsappinfo ONLY (NSWorkspace reported '{b_nsws}')"

        if detected_method:
            # Pump run loop for up to 1.0s to give the second check opportunity to catch up
            recheck_deadline = time.time() + 1.0
            while time.time() < recheck_deadline:
                now = NSDate.date()
                NSRunLoop.currentRunLoop().runUntilDate_(now.dateByAddingTimeInterval_(0.1))
                b_nsws2, b_indep2, both_wa2 = check_frontmost_dual()
                if both_wa2:
                    detected_method = "BOTH (NSWorkspace and lsappinfo agreed after pump)"
                    print(f"\n✅ WhatsApp frontmost detected by: {detected_method}! Continuing with step...")
                    rec = evaluate_dual_check(b_nsws2, b_indep2, context=f"{step_name} (Manual Focus)")
                    if summary is not None:
                        summary.setdefault("dual_check_records", []).append(rec)
                    return True
                time.sleep(0.1)

            print(f"\n⚠️ WhatsApp frontmost detected by: {detected_method}!")
            print(f"   NSWorkspace: '{b_nsws}', lsappinfo: '{b_indep}'")
            rec = evaluate_dual_check(b_nsws, b_indep, context=f"{step_name} (Manual Focus)")
            if summary is not None:
                summary.setdefault("dual_check_records", []).append(rec)
            return True

        if tick % 2 == 0:
            remaining = int(round(max(0.0, countdown_deadline - time.time())))
            print(f"   ...waiting for click ({remaining}s remaining)...")

    b_nsws, b_indep, _ = check_frontmost_dual()
    print(f"\n❌ ACTIVATION FAILED: WhatsApp not detected within 10s manual focus period.")
    print(f"   Final status: NSWorkspace='{b_nsws}', lsappinfo='{b_indep}'")
    rec = evaluate_dual_check(b_nsws, b_indep, context=f"{step_name} (Manual Focus Failed)")
    if summary is not None:
        summary.setdefault("dual_check_records", []).append(rec)
    return False


def handback_focus(terminal_app: NSRunningApplication, timeout_sec: float = 2.0) -> bool:
    """
    CRITICAL SAFETY GUARD:
    Re-activate user's Terminal and verify it is frontmost before accepting input.
    Pumps NSRunLoop while polling. Prevents user Enter from reaching WhatsApp.
    """
    ws = NSWorkspace.sharedWorkspace()
    now = NSDate.date()
    NSRunLoop.currentRunLoop().runUntilDate_(now.dateByAddingTimeInterval_(0.05))
    front = ws.frontmostApplication()
    if front and front.processIdentifier() == terminal_app.processIdentifier():
        return True

    terminal_app.activateWithOptions_(ApplicationServices.NSApplicationActivateIgnoringOtherApps)
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        now = NSDate.date()
        NSRunLoop.currentRunLoop().runUntilDate_(now.dateByAddingTimeInterval_(0.05))
        front = ws.frontmostApplication()
        if front and front.processIdentifier() == terminal_app.processIdentifier():
            return True

    # Fallback to system launcher for terminal bundle
    term_bundle = terminal_app.bundleIdentifier()
    if term_bundle:
        try:
            subprocess.run(["open", "-b", str(term_bundle)], capture_output=True, check=False)
        except Exception:
            pass
        fallback_deadline = time.time() + 1.0
        while time.time() < fallback_deadline:
            now = NSDate.date()
            NSRunLoop.currentRunLoop().runUntilDate_(now.dateByAddingTimeInterval_(0.05))
            front = ws.frontmostApplication()
            if front and front.processIdentifier() == terminal_app.processIdentifier():
                return True

    return False


def safe_input(prompt: str, terminal_app: NSRunningApplication) -> str:
    """Safely prompt the user only after Terminal focus is verified."""
    if not handback_focus(terminal_app):
        print("\n❌ CRITICAL SAFETY ERROR: Could not re-activate Terminal!")
        print("   Aborting script immediately to prevent stray Enter in WhatsApp.")
        sys.exit(1)
    return input(prompt)


def ask_yes_no(prompt: str, terminal_app: NSRunningApplication) -> bool:
    """
    Ask user a yes/no question in Terminal.
    Requires explicit 'y', 'yes', 'n', or 'no'.
    Rejects blank input (e.g. just pressing Enter) and prompts again.
    """
    while True:
        raw = safe_input(prompt, terminal_app).strip().lower()
        if raw in ("y", "yes"):
            return True
        elif raw in ("n", "no"):
            return False
        print("   ⚠️ Input required: please enter 'y' or 'n' (blank/Enter rejected).")


def print_safety_warning() -> None:
    print("\n" + "-" * 70)
    print("⚠️  SAFETY CHECK:")
    print("   Please ensure WhatsApp has NO DRAFT in the composer.")
    print("   Select a 'Message yourself' chat or an empty chat, and clear any draft.")
    print("-" * 70)


def post_whitelisted_key(
    keycode: int,
    modifiers: int = 0,
    dual_check_fn: Optional[Callable[[], Tuple[str, str, bool]]] = None,
    summary: Optional[Dict[str, Any]] = None,
    context: str = "Key Press",
) -> None:
    """
    Post a keyboard event strictly constrained to the whitelist {3, 9}.
    CRITICAL SAFETY RULE:
    Post key ONLY if BOTH independent checks report net.whatsapp.WhatsApp.
    If they disagree or either is not WhatsApp, abort the step and print both values.
    """
    assert keycode in ALLOWED_EXPERIMENT_KEYCODES, (
        f"NEVER-SEND VIOLATION: Keycode {keycode} is not in whitelist {ALLOWED_EXPERIMENT_KEYCODES}"
    )

    check_fn = dual_check_fn or check_frontmost_dual
    b_nsws, b_indep, both_wa = check_fn()
    rec = evaluate_dual_check(b_nsws, b_indep, context=context)
    if summary is not None:
        summary.setdefault("dual_check_records", []).append(rec)

    print(f"• Pre-key dual frontmost verification: NSWorkspace='{b_nsws}', lsappinfo='{b_indep}'")

    if not both_wa:
        print(f"\n❌ CRITICAL SAFETY ABORT: Refusing to post key {keycode}!")
        print(f"   NSWorkspace check: '{b_nsws}'")
        print(f"   lsappinfo check:   '{b_indep}'")
        print(f"   Evaluation:        {rec['disagreement_note']}")
        raise RuntimeError(
            f"DUAL FRONTMOST SAFETY ABORT: Key {keycode} blocked. "
            f"NSWorkspace='{b_nsws}', lsappinfo='{b_indep}'. Both must be '{WHATSAPP_BUNDLE_ID}'."
        )

    down = Quartz.CGEventCreateKeyboardEvent(None, keycode, True)
    up = Quartz.CGEventCreateKeyboardEvent(None, keycode, False)

    if modifiers:
        Quartz.CGEventSetFlags(down, modifiers)
        Quartz.CGEventSetFlags(up, modifiers)

    Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
    time.sleep(0.02)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)


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


def report_window_and_screen_visibility(main_win: Optional[Any]) -> Dict[str, Any]:
    """
    Report whether the WhatsApp window is on-screen:
    Print window position and size (numbers only), and the bounds of each connected display
    (NSScreen frames, numbers only), showing if the window sits outside the visible area.
    """
    screens = NSScreen.screens() or []
    primary_h = float(screens[0].frame().size.height) if len(screens) > 0 else 0.0

    print("\n" + "=" * 70)
    print("DISPLAY & WINDOW VISIBILITY REPORT (NUMBERS ONLY)")
    print("=" * 70)
    print(f"• Connected Displays: {len(screens)}")

    screen_reports = []
    for i, scr in enumerate(screens):
        f = scr.frame()
        c_x, c_y, c_w, c_h = float(f.origin.x), float(f.origin.y), float(f.size.width), float(f.size.height)
        ax_x = c_x
        ax_y = primary_h - (c_y + c_h)
        ax_w = c_w
        ax_h = c_h
        print(
            f"  - Display #{i + 1}: NSScreen frame origin=({c_x:.1f}, {c_y:.1f}) size=({c_w:.1f}, {c_h:.1f}) "
            f"| AX bounds origin=({ax_x:.1f}, {ax_y:.1f}) size=({ax_w:.1f}, {ax_h:.1f})"
        )
        screen_reports.append({
            "index": i + 1,
            "cocoa_frame": (c_x, c_y, c_w, c_h),
            "ax_bounds": (ax_x, ax_y, ax_w, ax_h),
        })

    if not main_win:
        print("• WhatsApp Main Window: NOT LOCATED")
        print("=" * 70)
        return {
            "on_screen": False,
            "reason": "Main window not located",
            "window_pos": (0.0, 0.0),
            "window_size": (0.0, 0.0),
            "screens": screen_reports,
            "overlapping_displays": [],
        }

    err_pos, pos_val = ApplicationServices.AXUIElementCopyAttributeValue(
        main_win, ApplicationServices.kAXPositionAttribute, None
    )
    win_x, win_y = extract_point(pos_val) if err_pos == 0 else (0.0, 0.0)

    err_sz, sz_val = ApplicationServices.AXUIElementCopyAttributeValue(
        main_win, ApplicationServices.kAXSizeAttribute, None
    )
    win_w, win_h = extract_size(sz_val) if err_sz == 0 else (0.0, 0.0)

    print(f"• WhatsApp Window Frame: pos=({win_x:.1f}, {win_y:.1f}) size=({win_w:.1f}, {win_h:.1f})")

    # Evaluate overlap with connected displays in AX coordinate space
    is_on_screen = False
    overlapping_displays = []
    for s in screen_reports:
        ax_x, ax_y, ax_w, ax_h = s["ax_bounds"]
        x_overlap = max(0.0, min(win_x + win_w, ax_x + ax_w) - max(win_x, ax_x))
        y_overlap = max(0.0, min(win_y + win_h, ax_y + ax_h) - max(win_y, ax_y))
        if x_overlap > 0.0 and y_overlap > 0.0:
            is_on_screen = True
            overlapping_displays.append(s["index"])

    if is_on_screen:
        disp_str = ", ".join(f"#{idx}" for idx in overlapping_displays)
        print(f"• Window Visibility: ON-SCREEN (overlaps display {disp_str})")
    else:
        print("• Window Visibility: ⚠️ OUTSIDE VISIBLE AREA (window sits outside display bounds)")
    print("=" * 70)

    return {
        "on_screen": is_on_screen,
        "window_pos": (win_x, win_y),
        "window_size": (win_w, win_h),
        "screens": screen_reports,
        "overlapping_displays": overlapping_displays,
    }


def build_summary_text(summary: Dict[str, Any]) -> str:
    """Format structure-only summary of the experiment."""
    lines = [
        "=" * 70,
        "WHATSAPP EXPERIMENT STRUCTURE-ONLY SUMMARY",
        f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 70,
        f"Mac Catalyst Flags Needed:         {'YES' if summary.get('catalyst_flags_needed') else 'NO'}",
        f"Search Field Located:              {'YES' if summary.get('search_field_found') else 'NO'}",
        f"Initial Row Wrappers:              {summary.get('initial_wrappers_count', 0)}",
        f"Initial Selectable Buttons:        {summary.get('initial_buttons_count', 0)}",
        f"Row Pitch:                         {summary.get('row_pitch', 0.0):.1f} px",
    ]
    if "window_geom" in summary:
        wg = summary["window_geom"]
        pos = wg.get("window_pos", (0.0, 0.0))
        sz = wg.get("window_size", (0.0, 0.0))
        lines.append(
            f"WhatsApp Window Pos/Size:          pos=({pos[0]:.1f}, {pos[1]:.1f}) size=({sz[0]:.1f}, {sz[1]:.1f})"
        )
        lines.append(
            f"Window Visible On-Screen:          {'YES' if wg.get('on_screen') else 'NO (OUTSIDE VISIBLE AREA)'}"
        )
        for s in wg.get("screens", []):
            cf = s["cocoa_frame"]
            lines.append(
                f"Display #{s['index']} Frame:                  "
                f"origin=({cf[0]:.1f}, {cf[1]:.1f}) size=({cf[2]:.1f}, {cf[3]:.1f})"
            )
    if "step_a" in summary:
        s_a = summary["step_a"]
        lines.append(
            f"Step A (AXPress Search Focus):    {'PASS' if s_a.get('search_field_focused') else 'FAIL'} | Role: {s_a.get('focused_role')} | Subrole: {s_a.get('focused_subrole')} | Cursor in box: {'YES' if s_a.get('cursor_in_search_box') else 'NO'}"
        )
    if "step_c" in summary:
        s_c = summary["step_c"]
        lines.append(
            f"Step C (Pre-Paste Guard & Paste): {'PASS' if s_c.get('guard_passed') else 'BLOCKED'} | 'Alex Test' in box: {'YES' if s_c.get('alex_test_in_search_box') else 'NO'} | Chat filtered: {'YES' if s_c.get('chat_list_filtered') else 'NO'} | Rows: {s_c.get('filtered_buttons_count', 0)}"
        )
    if "step_d" in summary:
        s_d = summary["step_d"]
        lines.append(
            f"Step D (AXCancel Clear Search):   {'PASS' if s_d.get('returned_full_list') else 'FAIL'} | Box emptied & returned: {'YES' if s_d.get('search_box_emptied_and_list_returned') else 'NO'} | Restored Rows: {s_d.get('rows_after_cancel')}/{s_d.get('baseline_rows')}"
        )
    if "step_b" in summary:
        s_b = summary["step_b"]
        status = s_b.get("status", "UNKNOWN")
        if status == "inconclusive":
            lines.append(f"Step B (Cmd+F from Unfocused):    INCONCLUSIVE ({s_b.get('reason')})")
        else:
            lines.append(
                f"Step B (Cmd+F from Unfocused):    {status} | Role: {s_b.get('focused_role')} | Subrole: {s_b.get('focused_subrole')} | Cursor in box: {'YES' if s_b.get('cursor_in_search_box') else 'NO'}"
            )
    if "step_e" in summary:
        s_e = summary["step_e"]
        if s_e.get("skipped"):
            lines.append(f"Step E (Geometry Row Selection):  SKIPPED ({s_e.get('reason')})")
        else:
            lines.append(
                f"Step E (Geometry Row Selection):  {'PASS' if s_e.get('chat_opened') else 'FAIL'} | Row #{s_e.get('row_index')} | Focused: {s_e.get('focused_role')}/{s_e.get('focused_subrole')}"
            )
    if "step_f" in summary:
        s_f = summary["step_f"]
        lines.append(
            f"Step F (Post-Open Focus Role):    Role: {s_f.get('focused_role')} | Subrole: {s_f.get('focused_subrole')}"
        )
    if "dual_check_records" in summary and summary["dual_check_records"]:
        lines.append("-" * 70)
        lines.append("DUAL FRONTMOST CHECK AUDIT LOG (NSWorkspace vs lsappinfo):")
        for rec in summary["dual_check_records"]:
            ctx = rec.get("context", "Check")
            note = rec.get("disagreement_note", "")
            lines.append(f"  • [{ctx}] {note}")
        disagreements = [r for r in summary["dual_check_records"] if r.get("status") == "DISAGREE"]
        if disagreements:
            lines.append(f"⚠️ TOTAL DISAGREEMENTS: {len(disagreements)}")
            for d in disagreements:
                lines.append(
                    f"   - {d.get('context')}: unreliable={d.get('unreliable_check')} ({d.get('disagreement_note')})"
                )
        else:
            lines.append("✅ Zero disagreements observed between checks.")
    lines.append("=" * 70)
    return "\n".join(lines)


def main() -> int:
    print("\n" + "=" * 70)
    print("WHATSAPP CONTROLLED INTERACTIVE EXPERIMENT (GEOMETRY & SAFETY EDITION)")
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

    # 3. Enable Mac Catalyst assistive flags
    print("• Setting Mac Catalyst assistive flags (AXManualAccessibility & AXEnhancedUserInterface)...")
    enable_catalyst_accessibility(app_el)
    time.sleep(1.0)

    # 4. Find Main Window and Search Field
    main_win = get_main_window(app_el)
    win_geom = report_window_and_screen_visibility(main_win)
    if not main_win:
        print("❌ Could not locate WhatsApp main window.")
        return 1

    search_field, sf_rect = find_search_field(main_win, max_depth=15)
    catalyst_needed = False
    if not search_field:
        print("• Search field not found on initial pass. Retrying after delay...")
        enable_catalyst_accessibility(app_el)
        time.sleep(1.0)
        search_field, sf_rect = find_search_field(main_win, max_depth=15)
        if search_field:
            catalyst_needed = True

    if not search_field:
        print("❌ Could not locate search field in WhatsApp window tree.")
        return 1

    sf_x, sf_y, sf_w, sf_h = sf_rect
    print(f"• Located Search Field: pos=({sf_x:.1f}, {sf_y:.1f}) size=({sf_w:.1f}, {sf_h:.1f})")

    # 5. Baseline Geometry Inspection for Chat Rows
    base_geo = find_chat_rows_geometry(main_win, sf_rect)
    print("\n[Baseline Chat List Geometry]")
    print(f"• AXGroup Wrappers Found:      {base_geo['wrappers_count']}")
    print(f"• AXButton Row Children Found: {base_geo['buttons_count']}")
    print(f"• Left Sidebar X-Range:         [{base_geo['sidebar_x_range'][0]:.1f}, {base_geo['sidebar_x_range'][1]:.1f}]")
    print(f"• Row Pitch:                   {base_geo['row_pitch']:.1f} px")

    summary: Dict[str, Any] = {
        "catalyst_flags_needed": catalyst_needed,
        "search_field_found": True,
        "initial_wrappers_count": base_geo["wrappers_count"],
        "initial_buttons_count": base_geo["buttons_count"],
        "row_pitch": base_geo["row_pitch"],
        "window_geom": win_geom,
    }

    summary_file = PROJECT_ROOT / "whatsapp_experiment_summary.txt"

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

        if not activate_whatsapp_and_verify(wa_app, 3.0, step_name="Step A", summary=summary):
            print("❌ Aborting Step A: WhatsApp could not be brought frontmost.")
            summary["step_a"] = {"search_field_focused": False, "error": "Activation failed"}
            return 1

        err_a = ApplicationServices.AXUIElementPerformAction(search_field, "AXPress")
        time.sleep(0.2)
        is_foc_a, role_a, subrole_a = check_is_focused(search_field, app_el)
        handback_focus(terminal_app)

        print(f"\n[Step A Result]")
        print(f"• Search Field Focused:      {is_foc_a}")
        print(f"• Focused Element Role:      {role_a}")
        print(f"• Focused Element Subrole:   {subrole_a}")
        print(f"• Matches 'AXSearchField':   {subrole_a == 'AXSearchField'}")

        ans_a = ask_yes_no("👀 Look at WhatsApp: Is the cursor in the search box? [y/n]: ", terminal_app)
        summary["step_a"] = {
            "action": "AXPress on search field",
            "search_field_focused": is_foc_a,
            "focused_role": role_a,
            "focused_subrole": subrole_a,
            "matches_guard": subrole_a == "AXSearchField",
            "cursor_in_search_box": ans_a,
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

        if not activate_whatsapp_and_verify(wa_app, 3.0, step_name="Step C", summary=summary):
            print("❌ Aborting Step C: WhatsApp could not be brought frontmost.")
            summary["step_c"] = {"guard_passed": False, "pasted": False, "error": "Activation failed"}
            return 1

        is_foc_c, role_c, subrole_c = check_is_focused(search_field, app_el)
        guard_passed = (subrole_c == "AXSearchField")

        if not guard_passed:
            handback_focus(terminal_app)
            print(f"\n❌ FOCUS GUARD BLOCKED PASTE: Focused subrole is '{subrole_c}' != 'AXSearchField'.")
            summary["step_c"] = {"guard_passed": False, "pasted": False}
        else:
            geo_pre = find_chat_rows_geometry(main_win, sf_rect)

            pb = NSPasteboard.generalPasteboard()
            orig_text = pb.stringForType_(NSPasteboardTypeString)
            try:
                pb.clearContents()
                pb.setString_forType_("Alex Test", NSPasteboardTypeString)
                try:
                    pb.setString_forType_("1", "org.nspasteboard.TransientType")
                except Exception:
                    pass

                post_whitelisted_key(
                    9,
                    Quartz.kCGEventFlagMaskCommand,
                    summary=summary,
                    context="Step C Cmd+V",
                )
                time.sleep(0.4)  # Allow search filter to populate

                geo_post = find_chat_rows_geometry(main_win, sf_rect)
            finally:
                pb.clearContents()
                if orig_text is not None:
                    pb.setString_forType_(orig_text, NSPasteboardTypeString)

            handback_focus(terminal_app)

            print(f"\n[Step C Result]")
            print(f"• Focus Guard Passed:        {guard_passed}")
            print(f"• Pre-Paste Row Buttons:     {geo_pre['buttons_count']}")
            print(f"• Filtered Row Buttons:      {geo_post['buttons_count']}")

            ans_c1 = ask_yes_no("👀 Look at WhatsApp: Did 'Alex Test' appear in the search box? [y/n]: ", terminal_app)
            ans_c2 = ask_yes_no("👀 Look at WhatsApp: Did the chat list filter? [y/n]: ", terminal_app)
            summary["step_c"] = {
                "guard_passed": True,
                "pasted": True,
                "pre_buttons_count": geo_pre["buttons_count"],
                "filtered_buttons_count": geo_post["buttons_count"],
                "alex_test_in_search_box": ans_c1,
                "chat_list_filtered": ans_c2,
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

        if not activate_whatsapp_and_verify(wa_app, 3.0, step_name="Step D", summary=summary):
            print("❌ Aborting Step D: WhatsApp could not be brought frontmost.")
            summary["step_d"] = {"returned_full_list": False, "error": "Activation failed"}
            return 1

        err_d = ApplicationServices.AXUIElementPerformAction(search_field, "AXCancel")
        time.sleep(0.4)
        geo_cancel = find_chat_rows_geometry(main_win, sf_rect)
        is_foc_after_d, _, _ = check_is_focused(search_field, app_el)
        handback_focus(terminal_app)

        returned_full = (geo_cancel["buttons_count"] == base_geo["buttons_count"])
        print(f"\n[Step D Result]")
        print(f"• AXCancel Call Return:      {'SUCCESS (0)' if err_d == 0 else f'ERROR ({err_d})'}")
        print(f"• Rows After AXCancel:       {geo_cancel['buttons_count']} (Baseline was {base_geo['buttons_count']})")
        print(f"• Restored Full Chat List:   {'YES' if returned_full else 'NO'}")
        print(f"• Search Field Still Focused:{is_foc_after_d}")

        ans_d = ask_yes_no("👀 Look at WhatsApp: Did the search box empty and the full list return? [y/n]: ", terminal_app)
        summary["step_d"] = {
            "action": "AXCancel on search field",
            "returned_full_list": returned_full,
            "rows_after_cancel": geo_cancel["buttons_count"],
            "baseline_rows": base_geo["buttons_count"],
            "search_field_still_focused": is_foc_after_d,
            "search_box_emptied_and_list_returned": ans_d,
        }

        # -------------------------------------------------------------
        # STEP B: Focus Search Field via Cmd+F (From Verified Unfocused State)
        # -------------------------------------------------------------
        print("\n" + "=" * 70)
        print("STEP B: Focus Search Field via Cmd+F (From Verified Unfocused State)")
        print("=" * 70)
        print_safety_warning()

        is_foc_before_b, _, _ = check_is_focused(search_field, app_el)
        print(f"• Search field AX-focused after AXCancel: {is_foc_before_b}")

        proceed_with_cmdf = True
        if is_foc_before_b:
            print("• Search field is still AX-focused after AXCancel.")
            print("👉 Click the Chats tab in WhatsApp with your mouse, then click back on Terminal and press Enter.")
            safe_input("Press [Enter] after clicking Chats tab in WhatsApp...", terminal_app)
            is_foc_recheck, _, _ = check_is_focused(search_field, app_el)
            print(f"• Re-checked focus: search field focused = {is_foc_recheck}")
            if is_foc_recheck:
                print("⚠️ Search field is still focused after manual click. Marking Step B INCONCLUSIVE.")
                summary["step_b"] = {
                    "status": "inconclusive",
                    "reason": "Search field remained focused after AXCancel and manual click",
                }
                proceed_with_cmdf = False

        if proceed_with_cmdf:
            print("• Action: Post Cmd+F (keycode 3 with Command modifier) from confirmed unfocused state.")
            print("• Safety: Whitelisted keycode only; no Return.")
            safe_input("👉 Press [Enter] to execute Step B (Cmd+F from unfocused state)...", terminal_app)

            if not activate_whatsapp_and_verify(wa_app, 3.0, step_name="Step B", summary=summary):
                print("❌ Aborting Step B: WhatsApp could not be brought frontmost.")
                summary["step_b"] = {"status": "FAIL", "error": "Activation failed"}
                return 1

            post_whitelisted_key(
                3,
                Quartz.kCGEventFlagMaskCommand,
                summary=summary,
                context="Step B Cmd+F",
            )
            time.sleep(0.25)
            is_foc_b, role_b, subrole_b = check_is_focused(search_field, app_el)
            handback_focus(terminal_app)

            print(f"\n[Step B Result]")
            print(f"• Search Field Focused:      {is_foc_b}")
            print(f"• Focused Element Role:      {role_b}")
            print(f"• Focused Element Subrole:   {subrole_b}")
            print(f"• Matches 'AXSearchField':   {subrole_b == 'AXSearchField'}")

            ans_b = ask_yes_no("👀 Look at WhatsApp: Did Cmd+F show the cursor in the search box? [y/n]: ", terminal_app)
            summary["step_b"] = {
                "status": "PASS" if (is_foc_b and ans_b) else "FAIL",
                "search_field_focused": is_foc_b,
                "focused_role": role_b,
                "focused_subrole": subrole_b,
                "matches_guard": subrole_b == "AXSearchField",
                "cursor_in_search_box": ans_b,
            }

            # Dismiss search again with AXCancel to leave clean state for Step E
            if activate_whatsapp_and_verify(wa_app, 3.0, step_name="Step B Cleanup", summary=summary):
                ApplicationServices.AXUIElementPerformAction(search_field, "AXCancel")
                time.sleep(0.2)
            handback_focus(terminal_app)

        # -------------------------------------------------------------
        # STEP E: Select Row N via AXPress on Child AXButton (Geometry-Based)
        # -------------------------------------------------------------
        print("\n" + "=" * 70)
        print("STEP E: Select Row N via Geometry (AXPress on Child AXButton)")
        print("=" * 70)
        print_safety_warning()

        geo_e = find_chat_rows_geometry(main_win, sf_rect)
        print(f"• AXGroup Wrappers Found:      {geo_e['wrappers_count']}")
        print(f"• AXButton Row Children Found: {geo_e['buttons_count']}")
        print(f"• Sidebar X-Range:             [{geo_e['sidebar_x_range'][0]:.1f}, {geo_e['sidebar_x_range'][1]:.1f}]")
        print(f"• Row Pitch:                   {geo_e['row_pitch']:.1f} px")

        if geo_e["buttons_count"] == 0:
            print("⚠️ SKIPPING Step E: 0 selectable chat rows detected via geometry.")
            summary["step_e"] = {
                "skipped": True,
                "reason": "0 selectable chat rows detected via geometry",
            }
        else:
            rows = geo_e["rows"]
            row_choice = safe_input(
                f"Enter row index to open (1 to {len(rows)}) [default 1]: ",
                terminal_app,
            ).strip()
            row_idx = int(row_choice) if row_choice.isdigit() and 1 <= int(row_choice) <= len(rows) else 1
            target_row = rows[row_idx - 1]
            target_button = target_row["button"]

            print(f"• Target: Row #{row_idx} (child AXButton inside AXGroup wrapper)")

            # SAFETY GUARD: Verify target button lies strictly inside sidebar and below search field
            sidebar_x_min, sidebar_x_max = geo_e["sidebar_x_range"]
            err_pos, pos_val = ApplicationServices.AXUIElementCopyAttributeValue(
                target_button, ApplicationServices.kAXPositionAttribute, None
            )
            btn_x, btn_y = extract_point(pos_val) if err_pos == 0 else (target_row["x"], target_row["y"])

            err_sz, sz_val = ApplicationServices.AXUIElementCopyAttributeValue(
                target_button, ApplicationServices.kAXSizeAttribute, None
            )
            btn_w, btn_h = extract_size(sz_val) if err_sz == 0 else (target_row["w"], target_row["h"])

            err_r, r = ApplicationServices.AXUIElementCopyAttributeValue(
                target_button, ApplicationServices.kAXRoleAttribute, None
            )
            role_str = str(r) if err_r == 0 and r else ""

            is_in_sidebar_x = (sidebar_x_min - 20.0 <= btn_x <= sidebar_x_max)
            is_below_sf = (btn_y >= sf_y + sf_h - 10.0) if sf_h > 0 else True
            is_not_conv_pane = (btn_x < sidebar_x_max)
            is_button = (role_str == "AXButton")

            if not (is_in_sidebar_x and is_not_conv_pane and is_below_sf and is_button):
                print("\n❌ STEP E SAFETY GUARD VIOLATION: Refusing to press element!")
                print(f"   Target button pos=({btn_x:.1f}, {btn_y:.1f}) size=({btn_w:.1f}, {btn_h:.1f}) role={role_str}")
                print(f"   Sidebar X-range: [{sidebar_x_min:.1f}, {sidebar_x_max:.1f}], Search field bottom: {sf_y + sf_h:.1f}")
                if not is_not_conv_pane:
                    print("   DANGER: Target element position is inside conversation pane!")
                summary["step_e"] = {
                    "skipped": True,
                    "reason": f"Safety guard aborted: button pos=({btn_x:.1f}, {btn_y:.1f}) outside sidebar bounds",
                }
                return 1

            safe_input(f"👉 Press [Enter] to execute AXPress on Row #{row_idx}...", terminal_app)

            if not activate_whatsapp_and_verify(wa_app, 3.0, step_name="Step E", summary=summary):
                print("❌ Aborting Step E: WhatsApp could not be brought frontmost.")
                summary["step_e"] = {"skipped": False, "chat_opened": False, "error": "Activation failed"}
                return 1

            err_e = ApplicationServices.AXUIElementPerformAction(target_button, "AXPress")
            time.sleep(0.35)

            # Inspect focused element role after selection
            _, f_role_e, f_subrole_e = check_is_focused(target_button, app_el)
            handback_focus(terminal_app)

            print(f"\n[Step E Result]")
            print(f"• AXPress Call Return:       {'SUCCESS (0)' if err_e == 0 else f'ERROR ({err_e})'}")
            print(f"• Post-Press Focused Role:   {f_role_e}")
            print(f"• Post-Press Focused Subrole:{f_subrole_e}")

            confirmed = ask_yes_no("👀 Look at WhatsApp on screen: Did the chat open? [y/n]: ", terminal_app)
            print(f"• User Confirmed Opened:     {'YES' if confirmed else 'NO'}")
            summary["step_e"] = {
                "skipped": False,
                "row_index": row_idx,
                "axpress_success": err_e == 0,
                "chat_opened": confirmed,
                "focused_role": f_role_e,
                "focused_subrole": f_subrole_e,
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

        if activate_whatsapp_and_verify(wa_app, 3.0, step_name="Step F", summary=summary):
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
        else:
            f_role_f = "Unknown"
            f_subrole_f = "Unknown"

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

        try:
            summary_file.write_text(summary_text, encoding="utf-8")
            print(f"Summary written to: {summary_file.name} (gitignored)")
        except Exception as e:
            print(f"Could not write summary file: {e}")


if __name__ == "__main__":
    sys.exit(main())
