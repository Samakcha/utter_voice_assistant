"""Startup smoke test exercising main.py initialization path."""

import sys
from main import main


def test_startup_smoke_test(monkeypatch):
    """
    Exercises the full startup path in main.py up to (but not including)
    the blocking menu bar run loop. Ensures all imports, configurations,
    audio device lookup, and component wiring succeed without error.
    """
    # Ensure sys.argv does not contain --check or --permissions so full setup runs
    monkeypatch.setattr(sys, "argv", ["main.py"])

    app = main(run_loop=False)
    assert app is not None
    assert app.is_running is True
    assert app.hotkey_manager is not None

    # Wait for transcriber warmup to finish cleanly so Metal GPU context is not interrupted
    if app.transcriber is not None:
        app.transcriber.wait_until_ready(timeout=15.0)
        app.transcriber.close()

    if app.overlay is not None:
        app.overlay.hide()

    app.hotkey_manager.stop()


def test_startup_check_flag(monkeypatch):
    """Verify that main.py --check completes without raising errors."""
    monkeypatch.setattr(sys, "argv", ["main.py", "--check"])
    result = main(run_loop=False)
    assert result is None
