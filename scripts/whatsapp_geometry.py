#!/usr/bin/env python3
"""
Shared WhatsApp Desktop Geometry and Structure Module.
Read-only and structure-only geometry helpers for finding the search field,
sidebar boundaries, and chat rows (AXGroup wrappers with AXButton children).
Never queries or stores content attributes (no values, descriptions, help, labels, or text).
"""

from collections import deque
from typing import Any, Dict, List, Optional, Tuple

try:
    import ApplicationServices
except ImportError:
    pass


def extract_point(ax_value: Any) -> Tuple[float, float]:
    """Safely unpack CGPoint from an AXValueRef."""
    if not ax_value:
        return 0.0, 0.0
    try:
        ok, pt = ApplicationServices.AXValueGetValue(
            ax_value, ApplicationServices.kAXValueTypeCGPoint, None
        )
        if ok and pt:
            return float(pt.x), float(pt.y)
    except Exception:
        pass
    return 0.0, 0.0


def extract_size(ax_value: Any) -> Tuple[float, float]:
    """Safely unpack CGSize from an AXValueRef."""
    if not ax_value:
        return 0.0, 0.0
    try:
        ok, sz = ApplicationServices.AXValueGetValue(
            ax_value, ApplicationServices.kAXValueTypeCGSize, None
        )
        if ok and sz:
            return float(sz.width), float(sz.height)
    except Exception:
        pass
    return 0.0, 0.0


def enable_catalyst_accessibility(app_element: Any) -> None:
    """Set assistive flags on WhatsApp to populate UIKit accessibility tree."""
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


def get_main_window(app_element: Any) -> Optional[Any]:
    """Find the WhatsApp main window element."""
    err_m, main_win = ApplicationServices.AXUIElementCopyAttributeValue(
        app_element, ApplicationServices.kAXMainWindowAttribute, None
    )
    if err_m == 0 and main_win:
        return main_win

    err_f, foc_win = ApplicationServices.AXUIElementCopyAttributeValue(
        app_element, ApplicationServices.kAXFocusedWindowAttribute, None
    )
    if err_f == 0 and foc_win:
        return foc_win

    err_w, windows = ApplicationServices.AXUIElementCopyAttributeValue(
        app_element, ApplicationServices.kAXWindowsAttribute, None
    )
    if err_w == 0 and windows:
        for w in windows:
            err_min, is_min = ApplicationServices.AXUIElementCopyAttributeValue(
                w, ApplicationServices.kAXMinimizedAttribute, None
            )
            if not (err_min == 0 and is_min):
                return w
        return windows[0]

    return None


def find_search_field(
    main_window: Any, max_depth: int = 15
) -> Tuple[Optional[Any], Tuple[float, float, float, float]]:
    """
    Breadth-first search for the search field in the main window.
    Returns: (element, (x, y, w, h))
    """
    queue = deque([(main_window, 1)])
    while queue:
        el, depth = queue.popleft()

        err_r, role = ApplicationServices.AXUIElementCopyAttributeValue(
            el, ApplicationServices.kAXRoleAttribute, None
        )
        err_s, subrole = ApplicationServices.AXUIElementCopyAttributeValue(
            el, ApplicationServices.kAXSubroleAttribute, None
        )
        role_str = str(role) if (err_r == 0 and role) else ""
        subrole_str = str(subrole) if (err_s == 0 and subrole) else ""

        is_subrole_match = (
            (subrole_str == "AXSearchField" or "SearchField" in subrole_str)
            and role_str in ("AXStaticText", "AXTextField", "AXSearchField")
        )
        is_role_match = (role_str == "AXSearchField")

        if is_subrole_match or is_role_match:
            err_p, pos_val = ApplicationServices.AXUIElementCopyAttributeValue(
                el, ApplicationServices.kAXPositionAttribute, None
            )
            err_sz, sz_val = ApplicationServices.AXUIElementCopyAttributeValue(
                el, ApplicationServices.kAXSizeAttribute, None
            )
            x, y = extract_point(pos_val) if err_p == 0 else (0.0, 0.0)
            w, h = extract_size(sz_val) if err_sz == 0 else (0.0, 0.0)
            return el, (x, y, w, h)

        if depth < max_depth:
            err_c, children = ApplicationServices.AXUIElementCopyAttributeValue(
                el, ApplicationServices.kAXChildrenAttribute, None
            )
            if err_c == 0 and children:
                for ch in children:
                    queue.append((ch, depth + 1))

    return None, (0.0, 0.0, 0.0, 0.0)


def get_sidebar_x_range(
    main_window: Any, sf_rect: Tuple[float, float, float, float]
) -> Tuple[float, float]:
    """Calculate the horizontal range of the left sidebar based on window and search field."""
    err_p, win_pos_val = ApplicationServices.AXUIElementCopyAttributeValue(
        main_window, ApplicationServices.kAXPositionAttribute, None
    )
    win_x, _ = extract_point(win_pos_val) if err_p == 0 else (0.0, 0.0)

    sf_x, _, sf_w, _ = sf_rect
    if sf_w > 0:
        sidebar_x_min = win_x
        # In Catalyst, the wrapper is ~550 wide, so sidebar boundary extends to at least wrapper width
        sidebar_x_max = max(sf_x + sf_w + 30.0, win_x + 580.0)
    else:
        sidebar_x_min = win_x
        sidebar_x_max = win_x + 580.0

    return sidebar_x_min, sidebar_x_max


def find_press_button_in_wrapper(wrapper: Any) -> Optional[Any]:
    """
    Search inside an AXGroup row wrapper for the AXButton child supporting AXPress.
    Checks direct children first, then one level deeper.
    """
    err_c, children = ApplicationServices.AXUIElementCopyAttributeValue(
        wrapper, ApplicationServices.kAXChildrenAttribute, None
    )
    if err_c != 0 or not children:
        return None

    for ch in children:
        err_r, r = ApplicationServices.AXUIElementCopyAttributeValue(
            ch, ApplicationServices.kAXRoleAttribute, None
        )
        err_act, acts = ApplicationServices.AXUIElementCopyActionNames(ch, None)
        actions_list = list(acts) if (err_act == 0 and acts) else []
        if str(r) == "AXButton" and "AXPress" in actions_list:
            return ch

    # Check 1 level deeper
    for ch in children:
        err_sub, sub_children = ApplicationServices.AXUIElementCopyAttributeValue(
            ch, ApplicationServices.kAXChildrenAttribute, None
        )
        if err_sub == 0 and sub_children:
            for sub_ch in sub_children:
                err_r, r = ApplicationServices.AXUIElementCopyAttributeValue(
                    sub_ch, ApplicationServices.kAXRoleAttribute, None
                )
                err_act, acts = ApplicationServices.AXUIElementCopyActionNames(sub_ch, None)
                actions_list = list(acts) if (err_act == 0 and acts) else []
                if str(r) == "AXButton" and "AXPress" in actions_list:
                    return sub_ch

    return None


def find_chat_rows_geometry(
    main_window: Any,
    sf_rect: Tuple[float, float, float, float],
    max_depth: int = 15,
) -> Dict[str, Any]:
    """
    Locate chat rows using geometry:
    Rows are AXGroup wrappers about 550 wide and 76 tall, stacked vertically in the sidebar,
    with an AXButton child that supports AXPress.
    Returns:
      wrappers_count, buttons_count, sidebar_x_range, row_pitch, rows, scroll_containers.
    """
    sidebar_x_min, sidebar_x_max = get_sidebar_x_range(main_window, sf_rect)
    sf_x, sf_y, sf_w, sf_h = sf_rect

    queue = deque([(main_window, 1, None)])  # (element, depth, parent)
    wrappers_found: List[Dict[str, Any]] = []

    while queue:
        el, depth, parent = queue.popleft()

        err_r, role = ApplicationServices.AXUIElementCopyAttributeValue(
            el, ApplicationServices.kAXRoleAttribute, None
        )
        role_str = str(role) if (err_r == 0 and role) else ""

        err_p, pos_val = ApplicationServices.AXUIElementCopyAttributeValue(
            el, ApplicationServices.kAXPositionAttribute, None
        )
        x, y = extract_point(pos_val) if err_p == 0 else (0.0, 0.0)

        err_sz, sz_val = ApplicationServices.AXUIElementCopyAttributeValue(
            el, ApplicationServices.kAXSizeAttribute, None
        )
        w, h = extract_size(sz_val) if err_sz == 0 else (0.0, 0.0)

        # Check wrapper criteria:
        # AXGroup wrapper ~550 wide (450 to 650) and ~76 tall (55 to 100)
        # Positioned inside sidebar below search field
        is_group = (role_str == "AXGroup")
        in_sidebar = (sidebar_x_min - 30.0 <= x <= sidebar_x_max + 30.0)
        below_sf = (y >= sf_y + sf_h - 10.0) if sf_h > 0 else True
        matches_dims = (450.0 <= w <= 650.0 and 55.0 <= h <= 100.0)

        if is_group and in_sidebar and below_sf and matches_dims:
            button = find_press_button_in_wrapper(el)
            wrappers_found.append({
                "wrapper": el,
                "button": button,
                "parent": parent,
                "x": x,
                "y": y,
                "w": w,
                "h": h,
                "depth": depth,
            })

        if depth < max_depth:
            err_c, children = ApplicationServices.AXUIElementCopyAttributeValue(
                el, ApplicationServices.kAXChildrenAttribute, None
            )
            if err_c == 0 and children:
                for ch in children:
                    queue.append((ch, depth + 1, el))

    # Sort vertically
    wrappers_found.sort(key=lambda item: item["y"])

    # Separate verified rows that have the AXButton child supporting AXPress
    rows: List[Dict[str, Any]] = []
    for idx, item in enumerate(wrappers_found, 1):
        if item["button"] is not None:
            rows.append({
                "row_index": len(rows) + 1,
                "wrapper": item["wrapper"],
                "button": item["button"],
                "parent": item["parent"],
                "x": item["x"],
                "y": item["y"],
                "w": item["w"],
                "h": item["h"],
                "depth": item["depth"],
            })

    # Calculate row pitch
    if len(rows) >= 2:
        pitches = [rows[i + 1]["y"] - rows[i]["y"] for i in range(len(rows) - 1)]
        row_pitch = sum(pitches) / len(pitches)
    elif len(rows) == 1:
        row_pitch = rows[0]["h"]
    else:
        row_pitch = 0.0

    # Trace scroll containers for candidates
    scroll_containers = []
    for r in rows:
        curr = r.get("parent")
        while curr:
            err_act, acts = ApplicationServices.AXUIElementCopyActionNames(curr, None)
            acts_list = list(acts) if (err_act == 0 and acts) else []
            err_r, role = ApplicationServices.AXUIElementCopyAttributeValue(
                curr, ApplicationServices.kAXRoleAttribute, None
            )
            r_str = str(role) if (err_r == 0 and role) else ""
            if "AXScrollDownByPage" in acts_list or r_str in ("AXScrollArea", "AXList", "AXTable"):
                if curr not in scroll_containers:
                    scroll_containers.append(curr)
                break
            err_c, parent = ApplicationServices.AXUIElementCopyAttributeValue(
                curr, ApplicationServices.kAXChildrenAttribute, None
            )
            # Cannot easily walk up without parent pointer, loop terminates
            break

    return {
        "wrappers_count": len(wrappers_found),
        "buttons_count": len(rows),
        "sidebar_x_range": (sidebar_x_min, sidebar_x_max),
        "row_pitch": row_pitch,
        "rows": rows,
        "scroll_containers": scroll_containers,
    }
