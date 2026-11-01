"""
Privacy and Safety Audit Tests for WhatsApp Probe, Experiment, and Geometry Scripts.
Verifies that scripts NEVER query content attributes (values, descriptions, help, labels, text),
allow ONLY structural attributes (role, subrole, children, position, size, enabled, focused, windows, action names),
never post non-whitelisted keycodes (especially Return keycode 36), and output files are gitignored.
"""

import ast
from pathlib import Path
import re
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROBE_PATH = PROJECT_ROOT / "scripts" / "whatsapp_probe.py"
EXPERIMENT_PATH = PROJECT_ROOT / "scripts" / "whatsapp_experiment.py"
LAYOUT_PROBE_PATH = PROJECT_ROOT / "scripts" / "whatsapp_layout_probe.py"
GEOMETRY_PATH = PROJECT_ROOT / "scripts" / "whatsapp_geometry.py"
GITIGNORE_PATH = PROJECT_ROOT / ".gitignore"

FORBIDDEN_CONTENT_ATTRIBUTES = [
    "kAXValueAttribute",
    "kAXDescriptionAttribute",
    "AXDescription",
    "kAXHelpAttribute",
    "AXHelp",
    "kAXLabelAttribute",
    "AXLabel",
    "kAXSelectedTextAttribute",
    "AXSelectedText",
]

ALLOWED_STRUCTURAL_ATTRIBUTE_NAMES = {
    "kAXRoleAttribute",
    "AXRole",
    "kAXSubroleAttribute",
    "AXSubrole",
    "kAXChildrenAttribute",
    "AXChildren",
    "kAXPositionAttribute",
    "AXPosition",
    "kAXSizeAttribute",
    "AXSize",
    "kAXEnabledAttribute",
    "AXEnabled",
    "kAXFocusedAttribute",
    "AXFocused",
    "kAXFocusedUIElementAttribute",
    "AXFocusedUIElement",
    "kAXWindowsAttribute",
    "AXWindows",
    "kAXMainWindowAttribute",
    "AXMainWindow",
    "kAXFocusedWindowAttribute",
    "AXFocusedWindow",
    "kAXMainAttribute",
    "AXMain",
    "kAXMinimizedAttribute",
    "AXMinimized",
    # Mac Catalyst flags
    "AXManualAccessibility",
    "AXEnhancedUserInterface",
    # Menu inspection in whatsapp_probe.py
    "kAXTitleAttribute",
    "AXMenuItemCmdChar",
    "AXMenuItemCmdModifiers",
}


def _extract_queried_attributes(filepath: Path) -> list[str]:
    """Parse AST to extract attribute names passed into AXUIElementCopyAttributeValue calls."""
    tree = ast.parse(filepath.read_text(encoding="utf-8"), filename=str(filepath))
    queried = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            func_name = getattr(func, "id", None) or getattr(func, "attr", None)
            if func_name == "AXUIElementCopyAttributeValue" and len(node.args) >= 2:
                arg = node.args[1]
                if isinstance(arg, ast.Constant):  # string literal
                    queried.append(arg.value)
                elif isinstance(arg, ast.Attribute):  # ApplicationServices.kAX...
                    queried.append(arg.attr)
                elif isinstance(arg, ast.Name):
                    queried.append(arg.id)
    return queried


def test_whatsapp_probe_source_privacy():
    """Verify whatsapp_probe.py does not query forbidden content attributes."""
    assert PROBE_PATH.is_file(), f"Missing probe script at {PROBE_PATH}"
    source = PROBE_PATH.read_text(encoding="utf-8")

    for attr in FORBIDDEN_CONTENT_ATTRIBUTES:
        assert attr not in source, (
            f"PRIVACY VIOLATION in whatsapp_probe.py: Found reference to content attribute '{attr}'"
        )


def test_whatsapp_experiment_source_privacy():
    """Verify whatsapp_experiment.py does not query forbidden content attributes."""
    assert EXPERIMENT_PATH.is_file(), f"Missing experiment script at {EXPERIMENT_PATH}"
    source = EXPERIMENT_PATH.read_text(encoding="utf-8")

    for attr in FORBIDDEN_CONTENT_ATTRIBUTES:
        assert attr not in source, (
            f"PRIVACY VIOLATION in whatsapp_experiment.py: Found reference to content attribute '{attr}'"
        )
    # Ensure no window/chat title querying
    assert "kAXTitleAttribute" not in source


def test_whatsapp_layout_probe_source_privacy():
    """Verify whatsapp_layout_probe.py does not query forbidden content attributes."""
    assert LAYOUT_PROBE_PATH.is_file(), f"Missing layout probe script at {LAYOUT_PROBE_PATH}"
    source = LAYOUT_PROBE_PATH.read_text(encoding="utf-8")

    for attr in FORBIDDEN_CONTENT_ATTRIBUTES:
        assert attr not in source, (
            f"PRIVACY VIOLATION in whatsapp_layout_probe.py: Found reference to content attribute '{attr}'"
        )
    assert "kAXTitleAttribute" not in source


def test_whatsapp_geometry_source_privacy():
    """Verify whatsapp_geometry.py does not query forbidden content attributes."""
    assert GEOMETRY_PATH.is_file(), f"Missing geometry script at {GEOMETRY_PATH}"
    source = GEOMETRY_PATH.read_text(encoding="utf-8")

    for attr in FORBIDDEN_CONTENT_ATTRIBUTES:
        assert attr not in source, (
            f"PRIVACY VIOLATION in whatsapp_geometry.py: Found reference to content attribute '{attr}'"
        )
    assert "kAXTitleAttribute" not in source


def test_only_structural_attributes_queried():
    """Verify that ONLY allowed structural attributes are queried in experiment, layout, and geometry scripts."""
    for script_path in [EXPERIMENT_PATH, LAYOUT_PROBE_PATH, GEOMETRY_PATH]:
        queried = _extract_queried_attributes(script_path)
        assert queried, f"No AX attributes queried in {script_path.name}"
        for attr in queried:
            assert attr in ALLOWED_STRUCTURAL_ATTRIBUTE_NAMES, (
                f"NON-STRUCTURAL ATTRIBUTE VIOLATION in {script_path.name}: "
                f"Attribute '{attr}' is not an approved structural attribute!"
            )


def test_never_send_rule_across_whatsapp_scripts():
    """
    NEVER-SEND RULE AUDIT:
    Verify scripts do NOT contain or post keycode 36 (kVK_Return / Enter).
    """
    for script_path in [EXPERIMENT_PATH, LAYOUT_PROBE_PATH, GEOMETRY_PATH]:
        source = script_path.read_text(encoding="utf-8")
        assert "36" not in source, (
            f"NEVER-SEND VIOLATION in {script_path.name}: Keycode 36 (kVK_Return) found!"
        )
        assert "kVK_Return" not in source, (
            f"NEVER-SEND VIOLATION in {script_path.name}: kVK_Return found!"
        )


def test_axpress_call_sites_strictly_limited():
    """
    Verify AXPress is only ever called from:
    1. Search-field step (target: search_field)
    2. Step E row-press (target: target_button)
    and nowhere else in whatsapp_experiment.py, and NEVER in geometry or probe scripts.
    """
    for script_path in [GEOMETRY_PATH, LAYOUT_PROBE_PATH]:
        tree = ast.parse(script_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func_name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if func_name == "AXUIElementPerformAction":
                    for arg in node.args:
                        assert not (isinstance(arg, ast.Constant) and arg.value == "AXPress"), (
                            f"AXPress cannot be performed in {script_path.name}"
                        )

    exp_tree = ast.parse(EXPERIMENT_PATH.read_text(encoding="utf-8"))
    exp_press_calls = []
    for node in ast.walk(exp_tree):
        if isinstance(node, ast.Call):
            func_name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if func_name == "AXUIElementPerformAction":
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and arg.value == "AXPress":
                        exp_press_calls.append(node)

    assert len(exp_press_calls) == 2, f"Expected exactly 2 AXPress calls in experiment, found {len(exp_press_calls)}"
    targets = [ast.unparse(c.args[0]) for c in exp_press_calls]
    assert "search_field" in targets[0], f"First AXPress call must target search_field, got {targets[0]}"
    assert "target_button" in targets[1], f"Second AXPress call must target target_button, got {targets[1]}"


def test_gitignore_contains_probe_experiment_and_layout_patterns():
    """Verify .gitignore blocks any probe, experiment, or layout output files from being committed."""
    assert GITIGNORE_PATH.is_file(), f"Missing .gitignore at {GITIGNORE_PATH}"
    content = GITIGNORE_PATH.read_text(encoding="utf-8")

    assert "*probe*.txt" in content or "probe" in content
    assert "*experiment*.txt" in content or "experiment" in content
    assert "whatsapp_layout*.txt" in content or "whatsapp_layout" in content


def test_activation_flow_has_launcher_and_manual_mode():
    """Verify activate_whatsapp_and_verify uses open -b launcher, activateWithOptions, and manual countdown."""
    source = EXPERIMENT_PATH.read_text(encoding="utf-8")
    assert 'subprocess.run(["open", "-b", WHATSAPP_BUNDLE_ID]' in source
    assert "activateWithOptions_" in source
    assert "MANUAL FOCUS MODE" in source
    assert "countdown_deadline" in source


def test_window_visibility_reporting_structure_only():
    """Verify report_window_and_screen_visibility exists, handles None, and queries NSScreen frames."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from whatsapp_experiment import report_window_and_screen_visibility

    # Test handling when no window is present
    result = report_window_and_screen_visibility(None)
    assert result["on_screen"] is False
    assert result["window_pos"] == (0.0, 0.0)
    assert result["window_size"] == (0.0, 0.0)
    assert isinstance(result["screens"], list)
    for scr in result["screens"]:
        assert len(scr["cocoa_frame"]) == 4
        assert all(isinstance(v, float) for v in scr["cocoa_frame"])
        assert len(scr["ax_bounds"]) == 4
        assert all(isinstance(v, float) for v in scr["ax_bounds"])


def test_evaluate_dual_check_agree_whatsapp():
    """Verify dual check agrees when both NSWorkspace and lsappinfo report WhatsApp."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from whatsapp_experiment import evaluate_dual_check

    res = evaluate_dual_check("net.whatsapp.WhatsApp", "net.whatsapp.WhatsApp", context="Test Agree")
    assert res["status"] == "AGREE_WHATSAPP"
    assert res["both_whatsapp"] is True
    assert res["both_agree"] is True
    assert res["unreliable_check"] == "None"
    assert "AGREED on WhatsApp" in res["disagreement_note"]


def test_evaluate_dual_check_disagree_nsworkspace_stale():
    """Verify dual check flags NSWorkspace as unreliable when lsappinfo reports WhatsApp."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from whatsapp_experiment import evaluate_dual_check

    res = evaluate_dual_check("com.apple.Terminal", "net.whatsapp.WhatsApp", context="Test Stale NSWS")
    assert res["status"] == "DISAGREE"
    assert res["both_whatsapp"] is False
    assert res["both_agree"] is False
    assert res["unreliable_check"] == "NSWorkspace"
    assert "lsappinfo detected WhatsApp, but NSWorkspace reported 'com.apple.Terminal'" in res["disagreement_note"]


def test_evaluate_dual_check_disagree_lsappinfo_stale():
    """Verify dual check flags lsappinfo as unreliable when NSWorkspace reports WhatsApp."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from whatsapp_experiment import evaluate_dual_check

    res = evaluate_dual_check("net.whatsapp.WhatsApp", "com.apple.Terminal", context="Test Stale LS")
    assert res["status"] == "DISAGREE"
    assert res["both_whatsapp"] is False
    assert res["both_agree"] is False
    assert res["unreliable_check"] == "lsappinfo"
    assert "NSWorkspace detected WhatsApp, but lsappinfo reported 'com.apple.Terminal'" in res["disagreement_note"]


def test_evaluate_dual_check_both_terminal():
    """Verify dual check handles both checks reporting Terminal (AGREE_OTHER)."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from whatsapp_experiment import evaluate_dual_check

    res = evaluate_dual_check("com.apple.Terminal", "com.apple.Terminal", context="Test Both Terminal")
    assert res["status"] == "AGREE_OTHER"
    assert res["both_whatsapp"] is False
    assert res["both_agree"] is True
    assert res["unreliable_check"] == "None"
    assert "AGREED on other app" in res["disagreement_note"]


def test_post_whitelisted_key_safety_rule_with_fake_checks():
    """
    CRITICAL SAFETY RULE TEST:
    Verify post_whitelisted_key:
    - Rejects when checks disagree
    - Rejects when both checks report Terminal
    - Accepts when both checks report WhatsApp
    """
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from unittest.mock import patch
    from whatsapp_experiment import post_whitelisted_key

    summary = {}

    # 1. Fake checks disagree (NSWorkspace=Terminal, lsappinfo=WhatsApp) -> MUST RAISE
    fake_disagree = lambda: ("com.apple.Terminal", "net.whatsapp.WhatsApp", False)
    with pytest.raises(RuntimeError) as exc_info:
        post_whitelisted_key(3, dual_check_fn=fake_disagree, summary=summary, context="Test Disagree")
    assert "DUAL FRONTMOST SAFETY ABORT" in str(exc_info.value)
    assert len(summary["dual_check_records"]) == 1
    assert summary["dual_check_records"][0]["status"] == "DISAGREE"

    # 2. Fake checks both Terminal -> MUST RAISE
    fake_both_term = lambda: ("com.apple.Terminal", "com.apple.Terminal", False)
    with pytest.raises(RuntimeError) as exc_info:
        post_whitelisted_key(9, dual_check_fn=fake_both_term, summary=summary, context="Test Both Term")
    assert "DUAL FRONTMOST SAFETY ABORT" in str(exc_info.value)
    assert len(summary["dual_check_records"]) == 2
    assert summary["dual_check_records"][1]["status"] == "AGREE_OTHER"

    # 3. Fake checks both WhatsApp -> Passes safety rule and proceeds to event post
    fake_agree = lambda: ("net.whatsapp.WhatsApp", "net.whatsapp.WhatsApp", True)
    with patch("Quartz.CGEventPost") as mock_post:
        post_whitelisted_key(3, dual_check_fn=fake_agree, summary=summary, context="Test Agree")
        assert mock_post.called
    assert len(summary["dual_check_records"]) == 3
    assert summary["dual_check_records"][2]["status"] == "AGREE_WHATSAPP"


def test_runloop_pump_in_polling_loops():
    """Verify NSRunLoop runUntilDate is called across polling loops in whatsapp_experiment.py."""
    source = EXPERIMENT_PATH.read_text(encoding="utf-8")
    assert "NSRunLoop.currentRunLoop().runUntilDate_" in source
    assert source.count("NSRunLoop.currentRunLoop().runUntilDate_") >= 4
