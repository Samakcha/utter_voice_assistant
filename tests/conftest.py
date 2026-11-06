"""
Pytest Safety Net Configuration for Voice Assistant.
Ensures that across the ENTIRE test suite:
1. Quartz.CGEventPost is blocked and raises if any test attempts to post real system key events.
2. AXUIElementPerformAction is blocked and raises if any test attempts real AX actions.
3. AXUIElementSetAttributeValue is blocked and raises if any test attempts real AX attribute modifications.
4. subprocess calls to `open` (launching apps) or `lsappinfo` are intercepted and raise.
5. get_general_pasteboard() is redirected to an isolated in-memory test pasteboard
   so the developer's real clipboard is NEVER read or modified by any test.
6. No test may write recordings or note backups into the real user data folder
   (~/Library/Application Support/VoiceAssistant).
7. No test reads the user's real personal word list: any word-list path inside the real
   data folder is redirected to an empty per-test file location.
"""

from pathlib import Path
import subprocess
import pytest
import AppKit
import ApplicationServices
import Quartz

_REAL_SUBPROCESS_RUN = subprocess.run
_REAL_SUBPROCESS_POPEN = subprocess.Popen
_REAL_SUBPROCESS_CHECK_OUTPUT = subprocess.check_output
_REAL_SUBPROCESS_CALL = subprocess.call


def _check_forbidden_subprocess_command(args):
    cmd_str = ""
    if isinstance(args, (list, tuple)) and args:
        cmd_str = str(args[0])
    elif isinstance(args, str):
        cmd_str = args
    cmd_lower = cmd_str.lower()
    full_str = str(args).lower()

    if cmd_lower in ("open", "lsappinfo") or "net.whatsapp.whatsapp" in full_str:
        raise RuntimeError(
            f"SAFETY NET BLOCKED: Real subprocess call to '{args}' in test! "
            "Tests must mock launcher and lsappinfo calls."
        )


@pytest.fixture(autouse=True)
def global_safety_net(monkeypatch):
    """Safety net applied to all tests preventing any real OS/app interaction."""

    # 1. Block Quartz key events
    def _blocked_cg_event_post(*args, **kwargs):
        raise RuntimeError(
            "SAFETY NET BLOCKED: Attempted to post real Quartz key event via CGEventPost! "
            "Tests must mock key events."
        )
    monkeypatch.setattr("Quartz.CGEventPost", _blocked_cg_event_post)

    # 2. Block Accessibility actions
    def _blocked_ax_perform_action(*args, **kwargs):
        raise RuntimeError(
            "SAFETY NET BLOCKED: Attempted real AXUIElementPerformAction! "
            "Tests must mock Accessibility actions."
        )
    monkeypatch.setattr("ApplicationServices.AXUIElementPerformAction", _blocked_ax_perform_action)

    # 3. Block Accessibility attribute modifications
    def _blocked_ax_set_attribute_value(*args, **kwargs):
        raise RuntimeError(
            "SAFETY NET BLOCKED: Attempted real AXUIElementSetAttributeValue! "
            "Tests must mock Accessibility mutations."
        )
    monkeypatch.setattr("ApplicationServices.AXUIElementSetAttributeValue", _blocked_ax_set_attribute_value)

    # 4. Block subprocess calls to `open` or `lsappinfo`
    def _safe_subprocess_run(args, *pargs, **kwargs):
        _check_forbidden_subprocess_command(args)
        return _REAL_SUBPROCESS_RUN(args, *pargs, **kwargs)

    def _safe_subprocess_check_output(args, *pargs, **kwargs):
        _check_forbidden_subprocess_command(args)
        return _REAL_SUBPROCESS_CHECK_OUTPUT(args, *pargs, **kwargs)

    def _safe_subprocess_popen(args, *pargs, **kwargs):
        _check_forbidden_subprocess_command(args)
        return _REAL_SUBPROCESS_POPEN(args, *pargs, **kwargs)

    def _safe_subprocess_call(args, *pargs, **kwargs):
        _check_forbidden_subprocess_command(args)
        return _REAL_SUBPROCESS_CALL(args, *pargs, **kwargs)

    monkeypatch.setattr(subprocess, "run", _safe_subprocess_run)
    monkeypatch.setattr(subprocess, "check_output", _safe_subprocess_check_output)
    monkeypatch.setattr(subprocess, "Popen", _safe_subprocess_popen)
    monkeypatch.setattr(subprocess, "call", _safe_subprocess_call)

    # 5. Isolate pasteboard: redirect get_general_pasteboard across modules to a dedicated test pasteboard
    # so NO test touches the developer's real macOS clipboard
    test_pasteboard = AppKit.NSPasteboard.pasteboardWithName_("VoiceAssistantGlobalIsolatedTestPasteboard")
    test_pasteboard.clearContents()
    monkeypatch.setattr("interface.clipboard.get_general_pasteboard", lambda: test_pasteboard)
    monkeypatch.setattr("interface.whatsapp_control.get_general_pasteboard", lambda: test_pasteboard)
    monkeypatch.setattr("interface.app.get_general_pasteboard", lambda: test_pasteboard)


# 7. Real personal word list: tests must not depend on (or see) the user's own names.
@pytest.fixture(autouse=True)
def isolate_real_word_list(monkeypatch, tmp_path):
    from engine.config import DEFAULT_DATA_DIR
    import engine.vocabulary as vocabulary

    real_root = DEFAULT_DATA_DIR.expanduser().resolve()
    original_init = vocabulary.VocabularyStore.__init__

    def _isolated_init(self, path):
        if path is not None:
            resolved = Path(path).expanduser().resolve()
            if resolved == real_root or real_root in resolved.parents:
                path = tmp_path / "isolated_word_list" / "vocabulary.txt"
        original_init(self, path)

    monkeypatch.setattr(vocabulary.VocabularyStore, "__init__", _isolated_init)


# 6. Real user data folder: no test may write recordings or note backups into it.
def _snapshot_real_data_dir():
    from engine.config import DEFAULT_DATA_DIR
    root = DEFAULT_DATA_DIR.expanduser()
    if not root.exists():
        return root, set()
    return root, {p for p in root.rglob("*") if p.is_file()}


@pytest.fixture(autouse=True)
def real_data_dir_guard():
    root, before = _snapshot_real_data_dir()
    yield
    _, after = _snapshot_real_data_dir()
    created = after - before
    if created:
        pytest.fail(
            f"SAFETY NET: test wrote into the real user data folder {root}: "
            f"{sorted(str(p) for p in created)}. Point the test's storage paths at tmp_path."
        )
