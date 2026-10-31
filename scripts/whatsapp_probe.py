#!/usr/bin/env python3
"""
WhatsApp Desktop Read-Only and Privacy-Safe Probe.
Inspects WhatsApp installation, menu shortcuts, and Accessibility UI roles and counts.

PRIVACY GUARANTEE:
- NEVER queries or prints chat names, contact names, message text, phone numbers,
  or element values/titles/descriptions from windows or conversations.
- Operates strictly in READ-ONLY mode (no keystrokes, no clicks, no state changes).
"""

import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

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
    from AppKit import NSRunningApplication, NSWorkspace
except ImportError as e:
    print(f"Error: Required macOS frameworks (PyObjC) not available: {e}")
    sys.exit(1)


WHATSAPP_BUNDLE_ID = "net.whatsapp.WhatsApp"


def get_whatsapp_bundle_info() -> Dict[str, Any]:
    """Retrieve WhatsApp application bundle metadata."""
    ws = NSWorkspace.sharedWorkspace()
    app_url = ws.URLForApplicationWithBundleIdentifier_(WHATSAPP_BUNDLE_ID)
    
    app_path: Optional[Path] = None
    if app_url is not None:
        app_path = Path(app_url.path())
    elif Path("/Applications/WhatsApp.app").is_dir():
        app_path = Path("/Applications/WhatsApp.app")

    if not app_path or not app_path.is_dir():
        return {
            "installed": False,
            "path": None,
            "version": None,
            "bundle_id": WHATSAPP_BUNDLE_ID,
        }

    info_plist = app_path / "Contents" / "Info.plist"
    version = "Unknown"
    bundle_id = WHATSAPP_BUNDLE_ID

    if info_plist.is_file():
        try:
            with open(info_plist, "rb") as f:
                plist_data = plistlib.load(f)
                version = plist_data.get("CFBundleShortVersionString", "Unknown")
                bundle_id = plist_data.get("CFBundleIdentifier", WHATSAPP_BUNDLE_ID)
        except Exception as e:
            version = f"Error reading plist: {e}"

    return {
        "installed": True,
        "path": str(app_path),
        "version": version,
        "bundle_id": bundle_id,
    }


def get_running_whatsapp() -> Optional[NSRunningApplication]:
    """Check if WhatsApp is currently running."""
    ws = NSWorkspace.sharedWorkspace()
    running_apps = ws.runningApplications()
    for app in running_apps:
        if app.bundleIdentifier() == WHATSAPP_BUNDLE_ID:
            return app
    return None


def format_modifiers(mod_mask: int) -> str:
    """Format AXMenuItemCmdModifiers integer into human-readable symbols."""
    # Standard macOS Accessibility modifier masks
    # bit 0: Shift (⇧), bit 1: Option (⌥), bit 2: Control (⌃), bit 3: No Command (rare)
    # By default, menu shortcuts on macOS imply Command (⌘) unless overridden.
    parts = []
    if mod_mask & 4:  # Control
        parts.append("⌃")
    if mod_mask & 2:  # Option
        parts.append("⌥")
    if mod_mask & 1:  # Shift
        parts.append("⇧")
    if not (mod_mask & 8):  # Default Command is present
        parts.append("⌘")
    return "".join(parts)


def probe_menu_bar(app_element: Any) -> List[Dict[str, Any]]:
    """
    Extract menu bar item TITLES and shortcuts ONLY.
    Never inspects or touches chat content.
    """
    err, menu_bar = ApplicationServices.AXUIElementCopyAttributeValue(
        app_element, ApplicationServices.kAXMenuBarAttribute, None
    )
    if err != 0 or not menu_bar:
        return []

    err, top_menus = ApplicationServices.AXUIElementCopyAttributeValue(
        menu_bar, ApplicationServices.kAXChildrenAttribute, None
    )
    if err != 0 or not top_menus:
        return []

    menu_tree = []

    def _walk_menu(menu_item_el: Any, depth: int = 0) -> Dict[str, Any]:
        err, title = ApplicationServices.AXUIElementCopyAttributeValue(
            menu_item_el, ApplicationServices.kAXTitleAttribute, None
        )
        title_str = str(title).strip() if (err == 0 and title) else ""

        err, cmd_char = ApplicationServices.AXUIElementCopyAttributeValue(
            menu_item_el, "AXMenuItemCmdChar", None
        )
        err, cmd_mod = ApplicationServices.AXUIElementCopyAttributeValue(
            menu_item_el, "AXMenuItemCmdModifiers", None
        )

        shortcut_str = ""
        if cmd_char:
            mod_str = format_modifiers(cmd_mod if cmd_mod is not None else 0)
            shortcut_str = f"{mod_str}{cmd_char}"

        err, children = ApplicationServices.AXUIElementCopyAttributeValue(
            menu_item_el, ApplicationServices.kAXChildrenAttribute, None
        )

        sub_items = []
        if err == 0 and children:
            for child in children:
                # If child is an AXMenu, get its children (the actual menu items)
                err_role, role = ApplicationServices.AXUIElementCopyAttributeValue(
                    child, ApplicationServices.kAXRoleAttribute, None
                )
                if role == "AXMenu":
                    err_sub, sub_children = ApplicationServices.AXUIElementCopyAttributeValue(
                        child, ApplicationServices.kAXChildrenAttribute, None
                    )
                    if err_sub == 0 and sub_children:
                        for sc in sub_children:
                            sub_items.append(_walk_menu(sc, depth + 1))
                else:
                    sub_items.append(_walk_menu(child, depth + 1))

        return {
            "title": title_str,
            "shortcut": shortcut_str,
            "children": sub_items,
        }

    for top_menu in top_menus:
        err, m_title = ApplicationServices.AXUIElementCopyAttributeValue(
            top_menu, ApplicationServices.kAXTitleAttribute, None
        )
        # Skip system Apple menu to avoid listing OS-level Recent Items
        if str(m_title).strip() == "Apple":
            continue
        menu_tree.append(_walk_menu(top_menu))

    return menu_tree


def print_menu_tree(menu_tree: List[Dict[str, Any]], prefix: str = "") -> None:
    """Print menu hierarchy with titles and shortcuts only."""
    for item in menu_tree:
        title = item["title"] or "[Separator / Unnamed]"
        shortcut = f" [{item['shortcut']}]" if item["shortcut"] else ""
        if title != "[Separator / Unnamed]" or item["children"]:
            print(f"{prefix}• {title}{shortcut}")
        if item["children"]:
            print_menu_tree(item["children"], prefix + "    ")


def probe_accessibility_structure(app_element: Any, max_depth: int = 5) -> None:
    """
    Inspect the window UI tree by ROLE and COUNT ONLY.
    STRICT PRIVACY: NEVER inspects or prints values, titles, labels, or descriptions
    of window contents or chat items.
    """
    print("\n" + "=" * 70)
    print("ACCESSIBILITY UI HIERARCHY (ROLE & COUNT ONLY - PRIVACY SAFE)")
    print("=" * 70)

    err, windows = ApplicationServices.AXUIElementCopyAttributeValue(
        app_element, ApplicationServices.kAXWindowsAttribute, None
    )
    if err != 0 or not windows:
        print("• No accessible windows found for WhatsApp.")
        return

    print(f"• Total Windows Detected: {len(windows)}")

    search_fields_found = []
    lists_or_tables_found = []

    def _walk_ui(element: Any, depth: int, path_roles: List[str]):
        if depth > max_depth:
            return

        err, role = ApplicationServices.AXUIElementCopyAttributeValue(
            element, ApplicationServices.kAXRoleAttribute, None
        )
        role_str = str(role) if err == 0 and role else "UnknownRole"

        err, subrole = ApplicationServices.AXUIElementCopyAttributeValue(
            element, ApplicationServices.kAXSubroleAttribute, None
        )
        subrole_str = f" (subrole: {subrole})" if err == 0 and subrole else ""

        err, is_focused = ApplicationServices.AXUIElementCopyAttributeValue(
            element, ApplicationServices.kAXFocusedAttribute, None
        )
        focused_str = " [FOCUSED]" if (err == 0 and is_focused) else ""

        err, actions = ApplicationServices.AXUIElementCopyActionNames(element, None)
        actions_list = list(actions) if (err == 0 and actions) else []
        actions_str = f" | Actions: {actions_list}" if actions_list else ""

        err, children = ApplicationServices.AXUIElementCopyAttributeValue(
            element, ApplicationServices.kAXChildrenAttribute, None
        )
        children_count = len(children) if (err == 0 and children) else 0

        # Privacy verification: structure and roles only; no content attributes queried.
        indent = "  " * depth
        print(f"{indent}[L{depth}] {role_str}{subrole_str}{focused_str} | Children: {children_count}{actions_str}")

        current_path = path_roles + [role_str]

        # Track search fields
        if "SearchField" in role_str or "SearchField" in subrole_str or role_str == "AXTextField":
            search_fields_found.append({
                "depth": depth,
                "role": role_str,
                "subrole": subrole_str,
                "path": " > ".join(current_path),
                "actions": actions_list,
                "focused": bool(is_focused),
            })

        # Track lists/tables/scroll areas (including Mac Catalyst scroll containers)
        if role_str in ("AXList", "AXTable", "AXOutline", "AXScrollArea") or (
            "AXScrollDownByPage" in actions_list and children_count > 0
        ):
            lists_or_tables_found.append({
                "depth": depth,
                "role": role_str,
                "subrole": subrole_str,
                "path": " > ".join(current_path),
                "children": children_count,
                "actions": actions_list,
            })

        if children:
            # If this is a list or table, summarize row count without dumping all individual rows
            if role_str in ("AXList", "AXTable", "AXOutline"):
                print(f"{indent}  └─ [Container: {children_count} items/rows - child content omitted for privacy]")
                # Probe only the first item structure to check available actions (e.g. AXPress, AXShowMenu)
                if children_count > 0:
                    first_child = children[0]
                    err_c_role, c_role = ApplicationServices.AXUIElementCopyAttributeValue(
                        first_child, ApplicationServices.kAXRoleAttribute, None
                    )
                    err_c_act, c_act = ApplicationServices.AXUIElementCopyActionNames(first_child, None)
                    print(f"{indent}     └─ Row 1 archetype: {c_role} | Actions: {list(c_act) if c_act else []}")
            else:
                for child in children:
                    _walk_ui(child, depth + 1, current_path)

    for i, window in enumerate(windows):
        err, main_check = ApplicationServices.AXUIElementCopyAttributeValue(
            window, ApplicationServices.kAXMainAttribute, None
        )
        is_main = " [MAIN]" if (err == 0 and main_check) else ""
        print(f"\n--- Window {i + 1}{is_main} ---")
        _walk_ui(window, 1, ["AXWindow"])

    print("\n" + "=" * 70)
    print("ACCESSIBILITY SUMMARY FOR AUTOMATION")
    print("=" * 70)
    print(f"• Candidate Search Fields Found: {len(search_fields_found)}")
    for sf in search_fields_found:
        print(f"    - {sf['role']}{sf['subrole']} at depth {sf['depth']} | Actions: {sf['actions']} | Focused: {sf['focused']}")

    print(f"• Candidate Lists / Containers Found: {len(lists_or_tables_found)}")
    for lst in lists_or_tables_found:
        print(f"    - {lst['role']}{lst['subrole']} at depth {lst['depth']} | Children/Rows: {lst['children']} | Actions: {lst['actions']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe WhatsApp Desktop installation, menus, and Accessibility safely.")
    parser.add_argument("--depth", type=int, default=4, help="Maximum depth for UI tree inspection. Default: 4.")
    args = parser.parse_args()

    print("\n" + "=" * 70)
    print("WHATSAPP DESKTOP INVESTIGATION PROBE (PHASE 0)")
    print("=" * 70)

    # 1. Bundle and Installation Status
    info = get_whatsapp_bundle_info()
    print(f"• Bundle ID:          {info['bundle_id']}")
    print(f"• Installed:          {'YES' if info['installed'] else 'NO'}")
    print(f"• Path:               {info['path'] or 'None'}")
    print(f"• Version:            {info['version'] or 'None'}")

    if not info["installed"]:
        print("\n❌ WhatsApp is not installed at /Applications/WhatsApp.app or registered with macOS.")
        return 1

    # 2. Running Application Status
    app = get_running_whatsapp()
    is_running = app is not None
    print(f"• Currently Running:  {'YES (PID: ' + str(app.processIdentifier()) + ')' if is_running else 'NO'}")

    # 3. Accessibility Permissions Check
    trusted = ApplicationServices.AXIsProcessTrusted()
    print(f"• AX Trusted Access:  {'GRANTED' if trusted else 'MISSING'}")

    if not trusted:
        print("\n⚠️  WARNING: Terminal/Python does not have macOS Accessibility permission.")
        print("   Please grant Accessibility in System Settings > Privacy & Security > Accessibility.")
        print("   Without this, menu and window elements cannot be inspected.")

    if not is_running:
        print("\n" + "-" * 70)
        print("WhatsApp is currently closed.")
        print("To inspect the live menus and Accessibility structure:")
        print("  1. Launch WhatsApp:  open -a WhatsApp")
        print("  2. Re-run this script:  ./.venv/bin/python scripts/whatsapp_probe.py")
        print("-" * 70 + "\n")
        return 0

    # 4. Process Accessibility Inspection
    pid = app.processIdentifier()
    app_el = ApplicationServices.AXUIElementCreateApplication(pid)

    # 5. Menu Bar Probe
    print("\n" + "=" * 70)
    print("WHATSAPP MENU BAR STRUCTURE & SHORTCUTS")
    print("=" * 70)
    menus = probe_menu_bar(app_el)
    if menus:
        print_menu_tree(menus)
    else:
        print("• Could not retrieve menu bar items (menu bar empty or not yet loaded).")

    # 6. Accessibility Window Structure Probe (Privacy-Safe)
    probe_accessibility_structure(app_el, max_depth=args.depth)

    # 7. Experiment Guide
    print("\n" + "=" * 70)
    print("CONTROLLED EXPERIMENTS GUIDE (RUN BY HAND IN WHATSAPP)")
    print("=" * 70)
    print("Please perform these 4 quick checks with WhatsApp focused:")
    print("  1. FOCUS SEARCH:")
    print("     • Press Cmd+F (or menu shortcut above). Does it focus the chat search field?")
    print("  2. SELECT NTH CHAT / RESULT:")
    print("     • Type a name in search.")
    print("     • Press Down Arrow (↓) once. Does it highlight the 1st search result?")
    print("     • Press Down Arrow (↓) twice. Does it highlight the 2nd result?")
    print("     • Press Return on a highlighted result. Does it open that chat?")
    print("  3. FOCUS AFTER CHAT OPENS:")
    print("     • Once the chat is open, where is keyboard focus? (Is the message box blinking?)")
    print("  4. NEVER-SEND VERIFICATION:")
    print("     • In the search box, does pressing Return ever send a message? (Expected: No).")
    print("     • When navigating the list with arrow keys, does Return only select/open the chat?")
    print("=" * 70 + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
