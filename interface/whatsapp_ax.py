"""
WhatsApp Desktop Accessibility (AX) Structural Helper Module.
Provides structural Accessibility navigation for net.whatsapp.WhatsApp:
- Sets AXUIElementSetMessagingTimeout (~1.0s) so an unresponsive app never freezes the UI.
- Locates main window and search field (matching subrole AXSearchField).
- Checks structural focus without querying content attributes.
- Allows AXPress and AXCancel strictly on the search field element.
Strictly structure-only: never reads values, descriptions, help, labels, or content text.
"""

from collections import deque
import logging
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import ApplicationServices
except ImportError:
    pass

WHATSAPP_BUNDLE_ID = "net.whatsapp.WhatsApp"
DEFAULT_AX_TIMEOUT_SEC = 1.0


def set_ax_timeout(element: Any, timeout_sec: float = DEFAULT_AX_TIMEOUT_SEC) -> None:
    """Set messaging timeout on an AXUIElement to avoid freezing when app is unresponsive."""
    if not element:
        return
    try:
        ApplicationServices.AXUIElementSetMessagingTimeout(element, float(timeout_sec))
    except Exception as e:
        logger.debug("Failed to set AX messaging timeout: %s", e)


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
    """Set assistive flags on WhatsApp to populate Mac Catalyst accessibility tree (fallback only)."""
    if not app_element:
        return
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
    """Find WhatsApp main window element."""
    if not app_element:
        return None

    set_ax_timeout(app_element, DEFAULT_AX_TIMEOUT_SEC)

    err_m, main_win = ApplicationServices.AXUIElementCopyAttributeValue(
        app_element, ApplicationServices.kAXMainWindowAttribute, None
    )
    if err_m == 0 and main_win:
        set_ax_timeout(main_win, DEFAULT_AX_TIMEOUT_SEC)
        return main_win

    err_f, foc_win = ApplicationServices.AXUIElementCopyAttributeValue(
        app_element, ApplicationServices.kAXFocusedWindowAttribute, None
    )
    if err_f == 0 and foc_win:
        set_ax_timeout(foc_win, DEFAULT_AX_TIMEOUT_SEC)
        return foc_win

    err_w, windows = ApplicationServices.AXUIElementCopyAttributeValue(
        app_element, ApplicationServices.kAXWindowsAttribute, None
    )
    if err_w == 0 and windows:
        for w in windows:
            set_ax_timeout(w, DEFAULT_AX_TIMEOUT_SEC)
            err_min, is_min = ApplicationServices.AXUIElementCopyAttributeValue(
                w, ApplicationServices.kAXMinimizedAttribute, None
            )
            if not (err_min == 0 and is_min):
                return w
        if windows:
            set_ax_timeout(windows[0], DEFAULT_AX_TIMEOUT_SEC)
            return windows[0]

    return None


def find_search_field(
    main_window: Any, max_depth: int = 15
) -> Tuple[Optional[Any], Tuple[float, float, float, float]]:
    """
    Breadth-first search for the search field in WhatsApp's window hierarchy.
    Matches primarily on subrole AXSearchField.
    Returns: (element, (x, y, w, h))
    """
    if not main_window:
        return None, (0.0, 0.0, 0.0, 0.0)

    set_ax_timeout(main_window, DEFAULT_AX_TIMEOUT_SEC)
    queue = deque([(main_window, 1)])

    while queue:
        el, depth = queue.popleft()
        set_ax_timeout(el, DEFAULT_AX_TIMEOUT_SEC)

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


def check_is_focused(element: Any, app_element: Any) -> Tuple[bool, str, str]:
    """
    Check if the element is focused and return focused element's structural role and subrole.
    Queries only kAXFocusedAttribute, kAXFocusedUIElementAttribute, kAXRoleAttribute, kAXSubroleAttribute.
    """
    if not element or not app_element:
        return False, "None", "None"

    set_ax_timeout(element, DEFAULT_AX_TIMEOUT_SEC)
    set_ax_timeout(app_element, DEFAULT_AX_TIMEOUT_SEC)

    err, is_focused = ApplicationServices.AXUIElementCopyAttributeValue(
        element, ApplicationServices.kAXFocusedAttribute, None
    )
    focused_bool = bool(err == 0 and is_focused)

    err_f, focused_el = ApplicationServices.AXUIElementCopyAttributeValue(
        app_element, ApplicationServices.kAXFocusedUIElementAttribute, None
    )
    f_role = "None"
    f_subrole = "None"
    if err_f == 0 and focused_el:
        set_ax_timeout(focused_el, DEFAULT_AX_TIMEOUT_SEC)
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
