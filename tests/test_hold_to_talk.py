"""Tests for hold-to-talk dictation: key routing, swallowing, watchdog, and app behavior."""

import dataclasses
import time

import pytest

from engine.config import load_config
from engine.models import EndReason, SessionMode
from engine.session import SessionManager
from interface.app import VoiceAssistantApp
from interface.hotkeys import HotkeyManager, HotkeyRegistrationError
from interface.key_events import (
    ACTION_CANCEL,
    ACTION_COMMAND,
    ACTION_DICTATION_PRESS,
    ACTION_DICTATION_RELEASE,
    EVENT_FLAGS_CHANGED,
    EVENT_KEY_DOWN,
    EVENT_KEY_UP,
    FLAG_COMMAND,
    FLAG_CONTROL,
    FLAG_OPTION,
    FLAG_SHIFT,
    KEYCODES,
    KeyCombo,
    KeyEventRouter,
    is_hold,
    parse_key_combo,
)
from tests.test_dictation_flow import MockAudioCapture, MockTranscriber, MockVAD

D = KEYCODES["d"]
M = KEYCODES["m"]
ESC = KEYCODES["esc"]
CTRL_OPT = FLAG_CONTROL | FLAG_OPTION
NON_COALESCED = 0x100  # a device-dependent flag bit macOS sets; must be ignored


def make_router() -> KeyEventRouter:
    return KeyEventRouter(
        parse_key_combo("<ctrl>+<alt>+m"),
        parse_key_combo("<ctrl>+<alt>+d"),
        parse_key_combo("<esc>"),
    )


def names(actions):
    return [a[0] for a in actions]


# ---- Parsing -------------------------------------------------------------------------

def test_parse_key_combo_uses_physical_key_codes():
    assert parse_key_combo("<ctrl>+<alt>+d") == KeyCombo(keycode=2, modifiers=CTRL_OPT)
    assert parse_key_combo("<ctrl>+<alt>+m") == KeyCombo(keycode=46, modifiers=CTRL_OPT)
    assert parse_key_combo("<esc>") == KeyCombo(keycode=53, modifiers=0)
    assert parse_key_combo("<cmd>+<shift>+<f5>") == KeyCombo(keycode=96, modifiers=FLAG_COMMAND | FLAG_SHIFT)


@pytest.mark.parametrize("bad", ["", "<ctrl>+<alt>", "<ctrl>+d+m", "<ctrl>+<nonexistent>", "<ctrl>+é"])
def test_parse_key_combo_rejects_bad_hotkeys(bad):
    with pytest.raises(ValueError):
        parse_key_combo(bad)


def test_is_hold_threshold():
    assert is_hold(0.4, 0.4) and is_hold(2.0, 0.4)
    assert not is_hold(0.15, 0.4)


# ---- Router: dictation key ------------------------------------------------------------

def test_hold_press_and_release_reports_held_time_and_swallows_the_key():
    r = make_router()
    swallow, actions = r.handle(EVENT_KEY_DOWN, D, CTRL_OPT | NON_COALESCED, False, now=10.0)
    assert swallow is True
    assert actions == [(ACTION_DICTATION_PRESS, 10.0)]
    assert r.dictation_held

    # Auto-repeat while held: swallowed, no new press
    for t in (10.5, 10.6, 10.7):
        swallow, actions = r.handle(EVENT_KEY_DOWN, D, CTRL_OPT, True, now=t)
        assert swallow is True and actions == []

    swallow, actions = r.handle(EVENT_KEY_UP, D, CTRL_OPT, False, now=11.25)
    assert swallow is True
    assert actions == [(ACTION_DICTATION_RELEASE, pytest.approx(1.25))]
    assert not r.dictation_held

    # Letting go of the modifiers afterwards does nothing more
    swallow, actions = r.handle(EVENT_FLAGS_CHANGED, 58, FLAG_CONTROL, False, now=11.3)
    assert swallow is False and actions == []


def test_letting_go_of_a_modifier_first_releases_and_ctrl_d_never_reaches_the_app():
    r = make_router()
    r.handle(EVENT_KEY_DOWN, D, CTRL_OPT, False, now=1.0)

    # Option released first while Control and D are still down
    swallow, actions = r.handle(EVENT_FLAGS_CHANGED, 58, FLAG_CONTROL, False, now=2.0)
    assert swallow is False  # modifier changes always pass through
    assert actions == [(ACTION_DICTATION_RELEASE, pytest.approx(1.0))]

    # D keeps auto-repeating with only Control held: would be Ctrl+D (delete forward)
    swallow, actions = r.handle(EVENT_KEY_DOWN, D, FLAG_CONTROL, True, now=2.1)
    assert swallow is True and actions == []

    # Its key-up is swallowed too, and there is no second release
    swallow, actions = r.handle(EVENT_KEY_UP, D, FLAG_CONTROL, False, now=2.2)
    assert swallow is True and actions == []


def test_other_key_combinations_pass_through_untouched():
    r = make_router()
    for keycode, flags in (
        (D, 0),                         # plain "d"
        (D, FLAG_OPTION),               # Option+D types "∂"
        (D, FLAG_CONTROL),              # Ctrl+D
        (D, CTRL_OPT | FLAG_SHIFT),     # extra modifier: not our hotkey
        (D, CTRL_OPT | FLAG_COMMAND),
        (KEYCODES["a"], CTRL_OPT),
    ):
        for event_type in (EVENT_KEY_DOWN, EVENT_KEY_UP):
            swallow, actions = r.handle(event_type, keycode, flags, False, now=1.0)
            assert swallow is False and actions == [], (keycode, flags, event_type)
    assert not r.dictation_held


def test_command_hotkey_is_swallowed_and_never_starts_a_hold():
    r = make_router()
    swallow, actions = r.handle(EVENT_KEY_DOWN, M, CTRL_OPT, False, now=1.0)
    assert swallow is True and names(actions) == [ACTION_COMMAND]
    swallow, actions = r.handle(EVENT_KEY_DOWN, M, CTRL_OPT, True, now=1.5)
    assert swallow is True and actions == []
    swallow, actions = r.handle(EVENT_KEY_UP, M, CTRL_OPT, False, now=2.0)
    assert swallow is True and actions == []
    assert not r.dictation_held


def test_esc_is_reported_but_always_reaches_the_app():
    r = make_router()
    swallow, actions = r.handle(EVENT_KEY_DOWN, ESC, 0, False, now=1.0)
    assert swallow is False and names(actions) == [ACTION_CANCEL]
    swallow, actions = r.handle(EVENT_KEY_DOWN, ESC, 0, True, now=1.1)
    assert swallow is False and actions == []
    swallow, actions = r.handle(EVENT_KEY_UP, ESC, 0, False, now=1.2)
    assert swallow is False and actions == []


def test_disabled_router_passes_everything_and_does_nothing():
    r = make_router()
    r.enabled = False
    for keycode in (D, M, ESC):
        swallow, actions = r.handle(EVENT_KEY_DOWN, keycode, CTRL_OPT if keycode != ESC else 0, False, now=1.0)
        assert swallow is False and actions == []
        swallow, actions = r.handle(EVENT_KEY_UP, keycode, CTRL_OPT, False, now=1.1)
        assert swallow is False and actions == []


# ---- Router: watchdog -----------------------------------------------------------------

def test_watchdog_releases_after_two_checks_with_the_keys_up():
    r = make_router()
    r.handle(EVENT_KEY_DOWN, D, CTRL_OPT, False, now=1.0)

    assert r.check_watchdog(True, CTRL_OPT, now=1.25) == []    # still held
    assert r.check_watchdog(False, CTRL_OPT, now=1.5) == []    # one miss: could be a race
    actions = r.check_watchdog(False, 0, now=1.75)
    assert actions == [(ACTION_DICTATION_RELEASE, pytest.approx(0.75))]
    assert not r.dictation_held

    # The real key-up arriving late must not release a second time
    swallow, actions = r.handle(EVENT_KEY_UP, D, 0, False, now=1.8)
    assert actions == []


def test_watchdog_miss_counter_resets_when_keys_are_seen_down_again():
    r = make_router()
    r.handle(EVENT_KEY_DOWN, D, CTRL_OPT, False, now=1.0)
    assert r.check_watchdog(False, CTRL_OPT, now=1.25) == []
    assert r.check_watchdog(True, CTRL_OPT, now=1.5) == []
    assert r.check_watchdog(False, CTRL_OPT, now=1.75) == []
    assert r.dictation_held


def test_watchdog_does_nothing_when_not_held():
    r = make_router()
    assert r.check_watchdog(False, 0, now=1.0) == []
    assert r.check_watchdog(False, 0, now=2.0) == []


# ---- HotkeyManager --------------------------------------------------------------------

def make_manager(**kwargs):
    calls = []
    active = {"value": False}
    key_state = {"down": True, "flags": CTRL_OPT}
    mgr = HotkeyManager(
        command_hotkey_str="<ctrl>+<alt>+m",
        dictation_hotkey_str="<ctrl>+<alt>+d",
        cancel_hotkey_str="<esc>",
        on_command=lambda: calls.append("command"),
        on_dictation=lambda: calls.append("dictation"),
        on_cancel=lambda: calls.append("cancel"),
        is_session_active=lambda: active["value"],
        hold_to_talk=True,
        on_dictation_release=lambda held: calls.append(("release", round(held, 2))),
        key_state=lambda keycode: (key_state["down"], key_state["flags"]),
        **kwargs,
    )
    mgr._router = mgr.build_router()
    return mgr, calls, active, key_state


def drain(mgr):
    while not mgr._actions.empty():
        mgr.run_action(mgr._actions.get_nowait())


def test_manager_routes_hold_to_press_and_release_callbacks():
    mgr, calls, active, _ = make_manager()
    mgr.route_event(EVENT_KEY_DOWN, D, CTRL_OPT, False, now=5.0)
    mgr.route_event(EVENT_KEY_DOWN, D, CTRL_OPT, True, now=5.5)
    mgr.route_event(EVENT_KEY_UP, D, CTRL_OPT, False, now=6.5)
    drain(mgr)
    assert calls == ["dictation", ("release", 1.5)]


def test_manager_esc_only_cancels_while_a_session_is_active():
    mgr, calls, active, _ = make_manager()
    mgr.route_event(EVENT_KEY_DOWN, ESC, 0, False, now=1.0)
    drain(mgr)
    assert calls == []
    active["value"] = True
    mgr.route_event(EVENT_KEY_DOWN, ESC, 0, False, now=2.0)
    drain(mgr)
    assert calls == ["cancel"]


def test_manager_disabled_ignores_hotkeys():
    mgr, calls, _, _ = make_manager()
    mgr.enabled = False
    swallow, _ = mgr.route_event(EVENT_KEY_DOWN, D, CTRL_OPT, False, now=1.0)
    assert swallow is False
    drain(mgr)
    assert calls == []


def test_manager_watchdog_releases_a_missed_key_up():
    mgr, calls, _, key_state = make_manager()
    mgr.route_event(EVENT_KEY_DOWN, D, CTRL_OPT, False, now=1.0)
    drain(mgr)
    key_state["down"] = False
    mgr.poll_watchdog(now=1.3)
    mgr.poll_watchdog(now=1.6)
    assert calls == ["dictation", ("release", 0.6)]


def test_manager_intercept_never_raises_and_passes_unknown_events():
    mgr, calls, _, _ = make_manager()
    sentinel = object()
    # Not a real CGEvent: the Quartz reads fail, and the event must be passed through
    assert mgr._intercept(EVENT_KEY_DOWN, sentinel) is sentinel
    assert mgr._intercept(999, sentinel) is sentinel


def test_manager_intercept_reads_real_quartz_events():
    """Built (never posted) CGEvents go through the real field reads and are swallowed or passed."""
    import Quartz
    mgr, calls, _, _ = make_manager()

    def key_event(keycode, down, flags, repeat=False):
        ev = Quartz.CGEventCreateKeyboardEvent(None, keycode, down)
        Quartz.CGEventSetFlags(ev, flags)
        Quartz.CGEventSetIntegerValueField(ev, Quartz.kCGKeyboardEventAutorepeat, 1 if repeat else 0)
        return ev

    hotkey_down = key_event(D, True, CTRL_OPT)
    assert mgr._intercept(Quartz.kCGEventKeyDown, hotkey_down) is None
    assert mgr._intercept(Quartz.kCGEventKeyDown, key_event(D, True, CTRL_OPT, repeat=True)) is None
    assert mgr._intercept(Quartz.kCGEventKeyUp, key_event(D, False, CTRL_OPT)) is None

    plain_d = key_event(D, True, 0)
    assert mgr._intercept(Quartz.kCGEventKeyDown, plain_d) is plain_d
    esc = key_event(ESC, True, 0)
    assert mgr._intercept(Quartz.kCGEventKeyDown, esc) is esc

    drain(mgr)
    assert calls[0] == "dictation" and calls[1][0] == "release"
    assert len(calls) == 2  # Esc with no active session does nothing


def test_manager_rejects_unsupported_or_duplicate_hotkeys():
    mgr = HotkeyManager("<ctrl>+<alt>+d", "<ctrl>+<alt>+d", "<esc>", hold_to_talk=True)
    with pytest.raises(HotkeyRegistrationError, match="must differ"):
        mgr.build_router()
    mgr = HotkeyManager("<ctrl>+<alt>+é", "<ctrl>+<alt>+d", "<esc>", hold_to_talk=True)
    with pytest.raises(HotkeyRegistrationError, match="command_mode"):
        mgr.build_router()


class _FakeListener:
    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        pass


@pytest.mark.parametrize("has_accessibility", [True, False])
def test_start_falls_back_to_tap_to_toggle_without_accessibility(monkeypatch, has_accessibility):
    import interface.hotkeys as hk
    monkeypatch.setattr(hk, "check_accessibility_permission", lambda: has_accessibility)
    monkeypatch.setattr(hk, "check_input_monitoring_permission", lambda: True)
    monkeypatch.setattr(hk.keyboard, "Listener", _FakeListener)
    monkeypatch.setattr(hk.keyboard, "GlobalHotKeys", _FakeListener)

    mgr = HotkeyManager("<ctrl>+<alt>+m", "<ctrl>+<alt>+d", "<esc>", hold_to_talk=True)
    mgr.start()
    try:
        listener = mgr._hotkey_listener
        assert listener.started
        if has_accessibility:
            assert mgr.hold_to_talk is True
            assert "darwin_intercept" in listener.kwargs  # raw key listener
        else:
            assert mgr.hold_to_talk is False                # app won't arm releases
            assert "<ctrl>+<alt>+d" in listener.args[0]     # old press-only hotkeys
    finally:
        mgr.stop()


def test_hold_to_talk_off_keeps_the_press_only_listener():
    mgr = HotkeyManager("<ctrl>+<alt>+m", "<ctrl>+<alt>+d", "<esc>")
    assert mgr.hold_to_talk is False
    assert "<ctrl>+<alt>+d" in mgr._build_hotkey_map()


# ---- Config ---------------------------------------------------------------------------

def test_config_turns_hold_to_talk_on_with_sane_timings():
    config = load_config()
    assert config.hotkeys.hold_to_talk is True
    assert config.hotkeys.hold_min_sec == 0.4
    assert config.hotkeys.hold_release_tail_sec == 0.3


# ---- App behavior ---------------------------------------------------------------------

def make_app(hold_to_talk=True, tail=0.05):
    config = load_config()
    config = dataclasses.replace(
        config,
        hotkeys=dataclasses.replace(
            config.hotkeys, hold_to_talk=hold_to_talk, hold_release_tail_sec=tail
        ),
    )
    sm = SessionManager(config=config, vad=MockVAD(), audio_capture=MockAudioCapture())
    app = VoiceAssistantApp(config=config, session_manager=sm, transcriber=MockTranscriber())
    app.overlay = None
    ends = []
    sm.on_session_end = lambda result: ends.append(result)
    return app, sm, ends


def wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_app_hold_then_release_stops_dictation_after_the_tail():
    app, sm, ends = make_app(tail=0.15)
    app._on_dictation_hotkey()
    assert sm.is_listening and sm.current_mode == SessionMode.DICTATION

    app._on_dictation_release(1.2)
    # Still recording during the release tail, so the last word isn't clipped
    assert sm.is_listening
    assert wait_until(lambda: not sm.is_listening)
    assert len(ends) == 1 and ends[0].end_reason == EndReason.HOTKEY


def test_app_quick_tap_keeps_dictating_until_the_next_press():
    app, sm, ends = make_app()
    app._on_dictation_hotkey()
    app._on_dictation_release(0.12)
    time.sleep(0.15)
    assert sm.is_listening, "a tap must keep toggle behavior"

    # Second press stops it (toggle); its release does nothing more
    app._on_dictation_hotkey()
    assert not sm.is_listening
    app._on_dictation_release(1.0)
    time.sleep(0.1)
    assert len(ends) == 1


def test_app_release_never_stops_a_newer_session():
    app, sm, ends = make_app(tail=0.1)
    app._on_dictation_hotkey()
    first_id = sm.current_session_id
    app._on_dictation_release(1.0)  # stop scheduled for the first session

    # Before the tail runs out, the first session ends some other way and a command starts
    sm.stop_session(EndReason.SILENCE)
    sm.start_session(SessionMode.COMMAND)
    assert sm.current_session_id != first_id

    time.sleep(0.25)
    assert sm.is_listening and sm.current_mode == SessionMode.COMMAND
    sm.stop_session(EndReason.ESC)


def test_app_press_that_stops_a_note_does_not_arm_a_hold():
    app, sm, ends = make_app(tail=0.0)
    sm.start_session(SessionMode.NOTE)
    app._on_dictation_hotkey()          # ends the note
    assert not sm.is_listening
    sm.start_session(SessionMode.COMMAND)
    app._on_dictation_release(2.0)      # must not touch the command session
    assert sm.is_listening and sm.current_mode == SessionMode.COMMAND
    sm.stop_session(EndReason.ESC)


def test_app_with_hold_to_talk_off_ignores_releases():
    app, sm, ends = make_app(hold_to_talk=False, tail=0.0)
    app._on_dictation_hotkey()
    app._on_dictation_release(3.0)
    assert sm.is_listening
    sm.stop_session(EndReason.ESC)


def test_stop_session_guard_checks_id_and_mode():
    sm = SessionManager(config=load_config(), vad=MockVAD(), audio_capture=MockAudioCapture())
    sm.start_session(SessionMode.DICTATION)
    sid = sm.current_session_id
    assert sm.stop_session(EndReason.HOTKEY, only_session_id="other") is None
    assert sm.stop_session(EndReason.HOTKEY, only_mode=SessionMode.COMMAND) is None
    assert sm.is_listening
    assert sm.stop_session(EndReason.HOTKEY, only_session_id=sid, only_mode=SessionMode.DICTATION) is not None
    assert not sm.is_listening
