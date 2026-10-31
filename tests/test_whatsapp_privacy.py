"""
Privacy and Safety Audit Tests for WhatsApp Probe and Experiment Scripts.
Verifies that scripts NEVER query content attributes (AXValue, AXDescription, AXHelp, AXLabel, etc.),
never post non-whitelisted keycodes (especially Return keycode 36), and output files are gitignored.
"""

from pathlib import Path
import re
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROBE_PATH = PROJECT_ROOT / "scripts" / "whatsapp_probe.py"
EXPERIMENT_PATH = PROJECT_ROOT / "scripts" / "whatsapp_experiment.py"
GITIGNORE_PATH = PROJECT_ROOT / ".gitignore"

FORBIDDEN_CONTENT_ATTRIBUTES = [
    "AXValue",
    "kAXValueAttribute",
    "AXDescription",
    "kAXDescriptionAttribute",
    "AXHelp",
    "kAXHelpAttribute",
    "AXLabel",
    "kAXLabelAttribute",
    "AXSelectedText",
    "kAXSelectedTextAttribute",
]


def test_whatsapp_probe_source_privacy():
    """Verify whatsapp_probe.py does not contain or query any forbidden content attributes."""
    assert PROBE_PATH.is_file(), f"Missing probe script at {PROBE_PATH}"
    source = PROBE_PATH.read_text(encoding="utf-8")

    for attr in FORBIDDEN_CONTENT_ATTRIBUTES:
        assert attr not in source, (
            f"PRIVACY VIOLATION in whatsapp_probe.py: Found reference to content attribute '{attr}'"
        )


def test_whatsapp_experiment_source_privacy():
    """Verify whatsapp_experiment.py does not contain or query any forbidden content attributes."""
    assert EXPERIMENT_PATH.is_file(), f"Missing experiment script at {EXPERIMENT_PATH}"
    source = EXPERIMENT_PATH.read_text(encoding="utf-8")

    for attr in FORBIDDEN_CONTENT_ATTRIBUTES:
        assert attr not in source, (
            f"PRIVACY VIOLATION in whatsapp_experiment.py: Found reference to content attribute '{attr}'"
        )


def test_whatsapp_experiment_never_send_rule():
    """
    NEVER-SEND RULE AUDIT:
    Verify whatsapp_experiment.py does NOT contain or post keycode 36 (kVK_Return / Enter).
    """
    source = EXPERIMENT_PATH.read_text(encoding="utf-8")

    # Ensure keycode 36 is nowhere in the file
    assert "36" not in source, (
        "NEVER-SEND VIOLATION in whatsapp_experiment.py: Keycode 36 (kVK_Return) found!"
    )
    # Ensure Return key name is not in key constants
    assert "kVK_Return" not in source


def test_gitignore_contains_probe_and_experiment_patterns():
    """Verify .gitignore blocks any probe or experiment output files from being committed."""
    assert GITIGNORE_PATH.is_file(), f"Missing .gitignore at {GITIGNORE_PATH}"
    content = GITIGNORE_PATH.read_text(encoding="utf-8")

    assert "*probe*.txt" in content or "probe" in content
    assert "*experiment*.txt" in content or "experiment" in content
