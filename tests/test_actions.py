"""Unit tests for the action registry and execution engine (engine/actions.py)."""

import threading
import pytest
from engine.actions import (
    ACTION_REGISTRY,
    ActionRisk,
    ActionResult,
    execute_action,
    execute_action_async,
    get_action,
)


def test_action_registry_contains_open_notes():
    assert "open_notes" in ACTION_REGISTRY
    action = get_action("open_notes")
    assert action is not None
    assert action.id == "open_notes"
    assert action.label == "Open Notes"
    assert action.risk == ActionRisk.LOW


def test_execute_unknown_action():
    result = execute_action("non_existent_action")
    assert isinstance(result, ActionResult)
    assert not result.success
    assert "Unknown action" in result.message


def test_high_risk_action_requires_confirmation(monkeypatch):
    from engine.actions import ActionDefinition

    test_action = ActionDefinition(
        id="send_message",
        label="Send Message",
        risk=ActionRisk.HIGH,
        handler=lambda timeout: ActionResult(success=True, action_id="send_message", message="Sent"),
    )
    monkeypatch.setitem(ACTION_REGISTRY, "send_message", test_action)

    # Without confirmation -> must fail
    res_unconfirmed = execute_action("send_message", confirmed=False)
    assert not res_unconfirmed.success
    assert "Confirmation required" in res_unconfirmed.message

    # With confirmation -> succeeds
    res_confirmed = execute_action("send_message", confirmed=True)
    assert res_confirmed.success
    assert res_confirmed.message == "Sent"


def test_execute_action_async(monkeypatch):
    from unittest.mock import MagicMock
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: MagicMock(returncode=0))
    done_event = threading.Event()
    received_result = None

    def on_done(res: ActionResult):
        nonlocal received_result
        received_result = res
        done_event.set()

    thread = execute_action_async("open_notes", callback=on_done, timeout_sec=5.0)
    assert done_event.wait(timeout=10.0)
    assert received_result is not None
    assert isinstance(received_result, ActionResult)
    assert received_result.action_id == "open_notes"
