#!/usr/bin/env python3
"""
WhatsApp Desktop Layout Probe (Read-Only & Structure-Only).
Performs a breadth-first traversal over the WhatsApp main window to depth 15.
Extracts structural & geometric properties (role, subrole, actions, position, size, enabled, focused)
without reading any content attributes (no titles, values, descriptions, help, labels, or text).
Saves details to whatsapp_layout.txt and prints shared geometry-based candidate analysis.
"""

from collections import deque
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

# Include scripts directory in sys.path for shared modules
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

try:
    import ApplicationServices
    from AppKit import NSWorkspace
except ImportError as e:
    print(f"Error: Required macOS frameworks (PyObjC / ApplicationServices) not available: {e}")
    sys.exit(1)

from whatsapp_geometry import (
    enable_catalyst_accessibility,
    extract_point,
    extract_size,
    find_chat_rows_geometry,
    get_main_window,
    get_sidebar_x_range,
)

WHATSAPP_BUNDLE_ID = "net.whatsapp.WhatsApp"


def main() -> int:
    print("\n" + "=" * 70)
    print("WHATSAPP LAYOUT PROBE (READ-ONLY & STRUCTURE-ONLY)")
    print("=" * 70)

    # 1. Verify accessibility permission
    is_trusted = bool(ApplicationServices.AXIsProcessTrusted())
    print(f"• Accessibility Permission Trusted: {is_trusted}")
    if not is_trusted:
        print("  ⚠️ WARNING: Process lacks Accessibility permission. Enable in System Settings.")

    # 2. Check WhatsApp process
    ws = NSWorkspace.sharedWorkspace()
    wa_app = None
    for app in ws.runningApplications():
        if app.bundleIdentifier() == WHATSAPP_BUNDLE_ID:
            wa_app = app
            break

    if not wa_app:
        print("❌ WhatsApp is not running. Please launch WhatsApp first: open -a WhatsApp")
        return 1

    print(f"• Found WhatsApp process (PID: {wa_app.processIdentifier()})")
    app_el = ApplicationServices.AXUIElementCreateApplication(wa_app.processIdentifier())

    # 3. Ensure Mac Catalyst tree is fully exposed
    print("• Setting Mac Catalyst assistive flags (AXManualAccessibility & AXEnhancedUserInterface)...")
    enable_catalyst_accessibility(app_el)
    time.sleep(1.0)

    # 4. Find main window
    main_win = get_main_window(app_el)
    if not main_win:
        print("❌ Could not locate WhatsApp main window.")
        return 1

    err_w_pos, win_pos_val = ApplicationServices.AXUIElementCopyAttributeValue(
        main_win, ApplicationServices.kAXPositionAttribute, None
    )
    win_x, win_y = extract_point(win_pos_val) if err_w_pos == 0 else (0.0, 0.0)

    err_w_sz, win_sz_val = ApplicationServices.AXUIElementCopyAttributeValue(
        main_win, ApplicationServices.kAXSizeAttribute, None
    )
    win_w, win_h = extract_size(win_sz_val) if err_w_sz == 0 else (0.0, 0.0)

    print(f"• Located Main Window: pos=({win_x:.1f}, {win_y:.1f}) size=({win_w:.1f}, {win_h:.1f})")

    # 5. Breadth-First Search (BFS) to depth 15
    print("• Traversing main window tree (BFS up to depth 15)...")
    elements: List[Dict[str, Any]] = []
    queue = deque([(main_win, 1, 0)])  # (element, depth, parent_index)

    search_field_info: Optional[Dict[str, Any]] = None

    while queue:
        el, depth, parent_idx = queue.popleft()
        current_idx = len(elements) + 1

        err_r, role = ApplicationServices.AXUIElementCopyAttributeValue(
            el, ApplicationServices.kAXRoleAttribute, None
        )
        role_str = str(role) if (err_r == 0 and role) else "Unknown"

        err_s, subrole = ApplicationServices.AXUIElementCopyAttributeValue(
            el, ApplicationServices.kAXSubroleAttribute, None
        )
        subrole_str = str(subrole) if (err_s == 0 and subrole) else "None"

        err_act, actions = ApplicationServices.AXUIElementCopyActionNames(el, None)
        actions_list = list(actions) if (err_act == 0 and actions) else []

        err_pos, pos_val = ApplicationServices.AXUIElementCopyAttributeValue(
            el, ApplicationServices.kAXPositionAttribute, None
        )
        x, y = extract_point(pos_val) if err_pos == 0 else (0.0, 0.0)

        err_sz, sz_val = ApplicationServices.AXUIElementCopyAttributeValue(
            el, ApplicationServices.kAXSizeAttribute, None
        )
        w, h = extract_size(sz_val) if err_sz == 0 else (0.0, 0.0)

        err_en, enabled = ApplicationServices.AXUIElementCopyAttributeValue(
            el, ApplicationServices.kAXEnabledAttribute, None
        )
        is_enabled = bool(err_en == 0 and enabled)

        err_foc, focused = ApplicationServices.AXUIElementCopyAttributeValue(
            el, ApplicationServices.kAXFocusedAttribute, None
        )
        is_focused = bool(err_foc == 0 and focused)

        err_c, children = ApplicationServices.AXUIElementCopyAttributeValue(
            el, ApplicationServices.kAXChildrenAttribute, None
        )
        children_list = list(children) if (err_c == 0 and children) else []
        child_count = len(children_list)

        info = {
            "index": current_idx,
            "depth": depth,
            "parent_index": parent_idx,
            "role": role_str,
            "subrole": subrole_str,
            "actions": actions_list,
            "x": x,
            "y": y,
            "w": w,
            "h": h,
            "child_count": child_count,
            "enabled": is_enabled,
            "focused": is_focused,
            "element": el,
        }
        elements.append(info)

        # Detect search field
        if search_field_info is None:
            if "SearchField" in subrole_str or role_str == "AXSearchField":
                search_field_info = info

        if depth < 15 and children_list:
            for ch in children_list:
                queue.append((ch, depth + 1, current_idx))

    total_count = len(elements)
    press_count = sum(1 for e in elements if "AXPress" in e["actions"])

    # 6. Determine left sidebar x-range
    if search_field_info:
        sf_rect = (
            search_field_info["x"],
            search_field_info["y"],
            search_field_info["w"],
            search_field_info["h"],
        )
    else:
        sf_rect = (win_x + 20.0, win_y + 40.0, 300.0, 30.0)

    sidebar_x_min, sidebar_x_max = get_sidebar_x_range(main_win, sf_rect)

    # 7. Write layout dump to whatsapp_layout.txt
    layout_file = PROJECT_ROOT / "whatsapp_layout.txt"
    lines = [
        "=" * 100,
        "WHATSAPP ACCESSIBILITY LAYOUT DUMP (STRUCTURE ONLY - ZERO CONTENT ATTRIBUTES)",
        f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Total Elements: {total_count} | Elements with AXPress: {press_count}",
        f"Main Window: pos=({win_x:.1f}, {win_y:.1f}) size=({win_w:.1f}, {win_h:.1f})",
        f"Sidebar X-Range: [{sidebar_x_min:.1f}, {sidebar_x_max:.1f}]",
        "=" * 100,
    ]

    for e in elements:
        actions_str = ", ".join(e["actions"]) if e["actions"] else "None"
        line = (
            f"#{e['index']:<4d} [d={e['depth']:<2d}, p=#{e['parent_index']:<4d}] "
            f"role={e['role']:<16} subrole={e['subrole']:<16} | "
            f"pos=({e['x']:7.1f}, {e['y']:7.1f}) size=({e['w']:7.1f}, {e['h']:7.1f}) | "
            f"children={e['child_count']:<3d} | "
            f"actions=[{actions_str}] | "
            f"enabled={e['enabled']} focused={e['focused']}"
        )
        lines.append(line)

    lines.append("=" * 100)
    layout_text = "\n".join(lines)
    layout_file.write_text(layout_text, encoding="utf-8")
    print(f"• Layout details written to: {layout_file.name} (gitignored)")

    # 8. Geometry-based candidate finder via shared module
    geo_data = find_chat_rows_geometry(main_win, sf_rect, max_depth=15)

    # 9. Print concise summary
    print("\n" + "=" * 70)
    print("WHATSAPP LAYOUT PROBE SUMMARY (STRUCTURE ONLY)")
    print("=" * 70)
    print(f"• Total Elements Traversed:       {total_count}")
    print(f"• Total Elements with AXPress:    {press_count}")
    if search_field_info:
        print(f"• Search Field Located:           pos=({sf_rect[0]:.1f}, {sf_rect[1]:.1f}) size=({sf_rect[2]:.1f}, {sf_rect[3]:.1f})")
    else:
        print("• Search Field:                   Not detected via AXSearchField")
    print(f"• Left Sidebar X-Range:           [{geo_data['sidebar_x_range'][0]:.1f}, {geo_data['sidebar_x_range'][1]:.1f}]")
    print(f"• AXGroup Row Wrappers Found:     {geo_data['wrappers_count']}")
    print(f"• AXButton Row Children Found:    {geo_data['buttons_count']}")
    print(f"• Row Pitch:                      {geo_data['row_pitch']:.1f} px")

    scroll_containers = geo_data["scroll_containers"]
    if scroll_containers:
        print(f"• Holding Scroll Container(s) ({len(scroll_containers)} found):")
        for sc in scroll_containers:
            err_act, acts = ApplicationServices.AXUIElementCopyActionNames(sc, None)
            acts_str = ", ".join(acts) if (err_act == 0 and acts) else "None"
            err_r, role = ApplicationServices.AXUIElementCopyAttributeValue(
                sc, ApplicationServices.kAXRoleAttribute, None
            )
            err_p, pos_val = ApplicationServices.AXUIElementCopyAttributeValue(
                sc, ApplicationServices.kAXPositionAttribute, None
            )
            err_sz, sz_val = ApplicationServices.AXUIElementCopyAttributeValue(
                sc, ApplicationServices.kAXSizeAttribute, None
            )
            sc_x, sc_y = extract_point(pos_val) if err_p == 0 else (0.0, 0.0)
            sc_w, sc_h = extract_size(sz_val) if err_sz == 0 else (0.0, 0.0)
            print(
                f"    role={role} pos=({sc_x:.1f}, {sc_y:.1f}) size=({sc_w:.1f}, {sc_h:.1f}) | actions=[{acts_str}]"
            )
    else:
        print("• Holding Scroll Container(s):    None directly detected in ancestor chain")

    print(f"• Output File:                    {layout_file.name} (gitignored)")
    print("=" * 70 + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
