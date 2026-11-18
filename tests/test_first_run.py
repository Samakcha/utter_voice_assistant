"""Stage 3: the first-run welcome window, live permission ticks and start at login. macOS faked."""

import dataclasses
import sys
import types

import pytest

from interface import login_item, setup_flow
from interface import permissions as perms
from interface.setup_flow import SetupFlow


# ---- Words and records ---------------------------------------------------------------------

def test_hotkeys_are_written_the_mac_way():
    assert setup_flow.format_hotkey("<ctrl>+<alt>+d") == "⌃⌥D"
    assert setup_flow.format_hotkey("<cmd>+<shift>+space") == "⌘⇧SPACE"
    assert setup_flow.format_hotkey("<esc>") == "Esc"


def test_reset_command_names_utter_and_the_right_list():
    assert setup_flow.reset_command("accessibility") == "tccutil reset Accessibility com.utter.mac"
    assert setup_flow.reset_command("input_monitoring") == "tccutil reset ListenEvent com.utter.mac"
    assert setup_flow.reset_command("microphone") == "tccutil reset Microphone com.utter.mac"


def test_setup_shows_once_and_remembers_the_choice(tmp_path):
    record = tmp_path / "data" / setup_flow.SETUP_FILE_NAME
    assert setup_flow.setup_needed(record)
    setup_flow.mark_setup_done(record, start_at_login=True)
    assert not setup_flow.setup_needed(record)
    assert setup_flow.read_record(record)["start_at_login"] is True


def test_a_broken_setup_file_shows_the_welcome_again(tmp_path):
    record = tmp_path / setup_flow.SETUP_FILE_NAME
    record.write_text("{not json", encoding="utf-8")
    assert setup_flow.setup_needed(record)
    record.write_text("[1, 2]", encoding="utf-8")
    assert setup_flow.setup_needed(record)


def test_restart_in_the_middle_resumes_on_the_try_step(tmp_path):
    record = tmp_path / setup_flow.SETUP_FILE_NAME
    assert setup_flow.resume_step(record) == 0
    setup_flow.write_record(record, done=False, resume="try")
    assert setup_flow.setup_needed(record) and setup_flow.resume_step(record) == 2
    setup_flow.mark_setup_done(record)
    assert setup_flow.resume_step(record) == 0


# ---- The steps -----------------------------------------------------------------------------

def test_four_steps_with_clear_buttons():
    flow = SetupFlow()
    assert flow.step_name == "welcome" and flow.continue_title() == "Get Started"
    assert flow.next() == "permissions" and flow.continue_title() == "Skip for Now"
    flow.update_permissions({"microphone": True, "accessibility": True, "input_monitoring": True})
    assert flow.continue_title() == "Continue"
    assert flow.next(now=5.0) == "try" and flow.continue_title() == "Skip"
    assert flow.try_started_at == 5.0
    assert flow.next() == "finish" and flow.is_last and flow.continue_title() == "Start Using Utter"
    assert flow.next() == "finish"  # never past the end
    assert flow.back() == "try"


def test_permission_rows_tick_by_themselves():
    flow = SetupFlow()
    assert flow.next_permission() == "microphone"
    assert flow.update_permissions({"microphone": True}) == ["microphone"]
    assert flow.update_permissions({"microphone": True}) == []  # only reported once
    assert flow.next_permission() == "accessibility"
    assert flow.missing == ["Accessibility", "Input Monitoring"]


def test_buttons_say_turn_on_first_then_open_settings():
    flow = SetupFlow()
    row = flow.rows["accessibility"]
    assert row.button_title(asked_before=False) == "Turn On"
    assert row.button_title(asked_before=True) == "Open Settings"
    flow.mark_asked("accessibility", now=1.0)
    assert row.button_title(asked_before=False) == "Open Settings"
    row.granted = True
    assert row.button_title(asked_before=True) == "On"


def test_a_stuck_permission_shows_the_fix_after_a_while():
    flow = SetupFlow()
    row = flow.rows["input_monitoring"]
    assert not row.stuck(now=100.0)  # never pressed
    flow.mark_asked("input_monitoring", now=100.0)
    assert not row.stuck(now=100.0 + setup_flow.STUCK_AFTER_SEC - 1)
    assert row.stuck(now=100.0 + setup_flow.STUCK_AFTER_SEC)
    assert "remove it with −" in row.stuck_hint() and "tccutil reset ListenEvent" in row.stuck_hint()
    flow.update_permissions({"input_monitoring": True})
    assert not row.stuck(now=1000.0)


def test_hotkeys_restart_once_when_both_keyboard_permissions_are_on():
    flow = SetupFlow()
    flow.update_permissions({"accessibility": True})
    assert not flow.hotkeys_became_ready()
    flow.update_permissions({"input_monitoring": True})
    assert flow.hotkeys_became_ready()
    assert not flow.hotkeys_became_ready()  # not again on every check


def test_try_it_passes_when_dictation_types_into_the_box():
    flow = SetupFlow()
    flow.go_to("try", now=10.0)
    flow.update_permissions({"microphone": True, "accessibility": True, "input_monitoring": True})
    assert flow.try_status() == "Waiting for you to speak…"
    assert not flow.try_needs_help(now=10.0 + setup_flow.TRY_HELP_AFTER_SEC - 1)
    assert flow.try_needs_help(now=10.0 + setup_flow.TRY_HELP_AFTER_SEC)
    assert flow.set_try_text("  ") is False
    assert flow.set_try_text("Hello Utter, this is my first dictation. ") is True
    assert flow.set_try_text("Hello Utter, this is my first dictation. Again") is False  # already passed
    assert flow.try_status().startswith("✓") and not flow.try_needs_help(now=1000.0)


def test_try_it_says_which_permission_is_still_off():
    flow = SetupFlow()
    flow.update_permissions({"microphone": True, "accessibility": False, "input_monitoring": True})
    assert "Accessibility" in flow.try_status()


def test_start_at_login_is_on_by_default():
    assert setup_flow.START_AT_LOGIN_DEFAULT is True
    assert SetupFlow().start_at_login is True


# ---- Start at login (Login Items) ----------------------------------------------------------

class FakeService:
    def __init__(self, status=0, tuple_result=False):
        self.code = status
        self.calls = []
        self.tuple_result = tuple_result

    def status(self):
        return self.code

    def _result(self, ok=True):
        return (ok, None) if self.tuple_result else ok

    def registerAndReturnError_(self, error):
        self.calls.append("register")
        self.code = 1
        return self._result()

    def unregisterAndReturnError_(self, error):
        self.calls.append("unregister")
        self.code = 0
        return self._result()


@pytest.mark.parametrize("tuple_result", [False, True])
def test_login_item_turns_on_and_off(tuple_result):
    service = FakeService(tuple_result=tuple_result)
    item = login_item.LoginItem(lambda: service)
    assert item.available and item.status() == login_item.OFF
    assert item.set_enabled(True) == login_item.ON
    assert item.set_enabled(True) == login_item.ON and service.calls == ["register"]  # no double add
    assert item.set_enabled(False) == login_item.OFF and service.calls == ["register", "unregister"]


def test_login_item_waiting_for_approval_is_reported():
    item = login_item.LoginItem(lambda: FakeService(status=2))
    assert item.status() == login_item.NEEDS_APPROVAL
    assert login_item.LoginItem(lambda: FakeService(status=3)).status() == login_item.OFF  # not found yet


def test_login_item_is_unavailable_outside_the_app():
    item = login_item.LoginItem()  # the tests run from the project folder, not Utter.app
    assert not item.available
    assert item.status() == login_item.UNAVAILABLE
    assert item.set_enabled(True) == login_item.UNAVAILABLE


def test_login_item_survives_macos_errors():
    def broken():
        raise RuntimeError("no ServiceManagement")
    assert login_item.LoginItem(broken).status() == login_item.UNAVAILABLE

    class Failing(FakeService):
        def registerAndReturnError_(self, error):
            raise RuntimeError("denied")
    assert login_item.LoginItem(lambda: Failing()).set_enabled(True) == login_item.OFF


def test_login_items_framework_is_found_on_this_mac():
    assert login_item._sm_app_service_class() is not None  # macOS 13+: no extra package needed


# ---- One permission button -----------------------------------------------------------------

def _perms(mic=False, ax=False, listen=False):
    def item(name, granted, url):
        return perms.PermissionItem(name=name, granted=granted, settings_path="", deep_link_url=url, purpose="")
    return {"microphone": item("Microphone", mic, "mic-url"), "accessibility": item("Accessibility", ax, "ax-url"),
            "input_monitoring": item("Input Monitoring", listen, "im-url")}


@pytest.fixture
def fake_requests(monkeypatch):
    events = []

    def fake_request(permissions):
        missing = [i.name for i in permissions.values() if not i.granted]
        if missing:
            events.append(("ask", missing))
        return missing
    monkeypatch.setattr(perms, "request_missing_permissions", fake_request)
    monkeypatch.setattr(perms.subprocess, "run", lambda args, **k: events.append(("open", args[1])))
    monkeypatch.setattr(perms, "microphone_not_asked_yet", lambda: False)
    return events


def test_first_press_asks_macos_then_opens_settings(fake_requests, tmp_path):
    record = tmp_path / perms.ASKED_FILE_NAME
    assert perms.turn_on_permission("accessibility", record, _perms()) == "asked"
    assert fake_requests == [("ask", ["Accessibility"])]  # only this one, not all three
    assert perms.was_asked("Accessibility", record)
    assert perms.turn_on_permission("accessibility", record, _perms()) == "opened_settings"
    assert fake_requests[-1] == ("open", "ax-url")


def test_microphone_asks_only_while_macos_never_asked(fake_requests, monkeypatch, tmp_path):
    monkeypatch.setattr(perms, "microphone_not_asked_yet", lambda: True)
    assert perms.turn_on_permission("microphone", None, _perms()) == "asked"
    monkeypatch.setattr(perms, "microphone_not_asked_yet", lambda: False)  # refused before
    assert perms.turn_on_permission("microphone", None, _perms()) == "opened_settings"
    assert fake_requests[-1] == ("open", "mic-url")


def test_a_granted_permission_button_does_nothing(fake_requests):
    assert perms.turn_on_permission("input_monitoring", None, _perms(listen=True)) == "granted"
    assert fake_requests == []


def test_the_startup_ask_and_the_welcome_share_one_record(fake_requests, tmp_path):
    record = tmp_path / perms.ASKED_FILE_NAME
    perms.turn_on_permission("input_monitoring", record, _perms())
    assert perms.ask_missing_once(_perms(mic=True, ax=True), record) == []  # not asked twice


# ---- The window ----------------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _window(granted, **kwargs):
    from interface.welcome_window import WelcomeWindow
    events = []
    clock = Clock()
    window = WelcomeWindow(
        command_hotkey="<ctrl>+<alt>+m", dictation_hotkey="<ctrl>+<alt>+d",
        permissions=lambda: dict(granted),
        turn_on=lambda key: events.append(("turn_on", key)),
        hotkeys_ready=lambda: events.append("hotkeys_ready"),
        restart=lambda: events.append("restart"),
        finish=lambda on: events.append(("finish", on)),
        closed=lambda: events.append("closed"),
        clock=clock, **kwargs)
    return window, events, clock


def test_window_ticks_each_permission_as_it_turns_on():
    granted = {"microphone": True, "accessibility": False, "input_monitoring": False}
    window, events, clock = _window(granted)
    window.next()
    assert window.flow.step_name == "permissions"
    mark, button, hint = window.rows["microphone"]
    assert mark.stringValue() == "✓" and not button.isEnabled()
    mark, button, hint = window.rows["accessibility"]
    assert mark.stringValue() == "2" and button.title() == "Turn On" and button.isEnabled()
    window.turn_on("accessibility")
    assert events == [("turn_on", "accessibility")] and button.title() == "Open Settings"
    clock.now = setup_flow.STUCK_AFTER_SEC
    window.tick()
    assert not hint.isHidden()  # still off: the fix is shown
    granted.update(accessibility=True, input_monitoring=True)
    window.tick()
    assert window.rows["accessibility"][0].stringValue() == "✓" and hint.isHidden()
    assert events.count("hotkeys_ready") == 1
    window.tick()
    assert events.count("hotkeys_ready") == 1
    assert window.next_button.title() == "Continue"
    window.close()


def test_hotkeys_are_not_restarted_when_already_working():
    window, events, _ = _window({"microphone": True, "accessibility": True, "input_monitoring": True})
    window.tick()
    assert "hotkeys_ready" not in events
    window.close()


def test_window_try_step_and_finish():
    window, events, clock = _window({"microphone": True, "accessibility": True, "input_monitoring": True},
                                    start_step=2)
    assert window.flow.step_name == "try" and not window.pages["try"].isHidden()
    assert window.pages["welcome"].isHidden()
    clock.now = setup_flow.TRY_HELP_AFTER_SEC
    window.tick()
    assert not window.restart_button.isHidden()  # nothing typed: offer the restart
    window.try_field.setStringValue_("Hello Utter, this is my first dictation.")
    window.tick()
    assert window.try_status.stringValue().startswith("✓") and window.restart_button.isHidden()
    window.next()
    assert window.flow.step_name == "finish" and window.login_checkbox.state() == 1  # on by default
    window.login_checkbox.setState_(0)
    window._toggle_login()
    window.next()
    assert ("finish", False) in events and "closed" in events and window.finished


def test_window_without_login_items_cannot_promise_start_at_login():
    window, events, _ = _window({"microphone": True, "accessibility": True, "input_monitoring": True},
                                start_step=3, login_available=False)
    assert not window.login_checkbox.isEnabled() and window.login_checkbox.state() == 0
    window.next()
    assert ("finish", False) in events


def test_window_handles_paste_without_an_edit_menu():
    from interface.welcome_window import UtterSetupWindow, _COMMAND_KEYS
    assert _COMMAND_KEYS["v"] == "paste:"  # dictation types into the Try box with ⌘V
    assert hasattr(UtterSetupWindow, "performKeyEquivalent_")


# ---- In the app ----------------------------------------------------------------------------

def _app(monkeypatch, tmp_path):
    import interface.app as app_mod
    from engine.config import VocabularyConfig, load_config
    from engine.session import SessionManager
    config = dataclasses.replace(load_config(), vocabulary=VocabularyConfig(file=None))
    app = app_mod.VoiceAssistantApp(config=config, session_manager=SessionManager(config=config))
    app.overlay = None
    app.setup_record = tmp_path / "setup.json"
    app.permissions_record = tmp_path / perms.ASKED_FILE_NAME
    return app_mod, app


def test_menu_has_setup_and_start_at_login(monkeypatch, tmp_path):
    _, app = _app(monkeypatch, tmp_path)
    assert "Setup…" in app.menu and "Start at Login" in app.menu
    assert app.login_menu_item.state == 0  # not the app: off
    app.menu_icon.stop()


def test_finishing_setup_adds_the_login_item_and_is_remembered(monkeypatch, tmp_path):
    _, app = _app(monkeypatch, tmp_path)
    service = FakeService()
    app.login_item = login_item.LoginItem(lambda: service)
    app._setup_finished(True)
    assert service.calls == ["register"] and app.login_menu_item.state == 1
    assert not setup_flow.setup_needed(app.setup_record)
    app.toggle_start_at_login()
    assert service.calls == ["register", "unregister"] and app.login_menu_item.state == 0
    assert setup_flow.read_record(app.setup_record)["start_at_login"] is False
    app.menu_icon.stop()


def test_closing_the_welcome_early_never_shows_it_by_itself_again(monkeypatch, tmp_path):
    app_mod, app = _app(monkeypatch, tmp_path)
    monkeypatch.setattr(app_mod, "get_all_permissions", lambda: _perms(mic=True, ax=True, listen=False))
    app._setup_closed()
    assert not setup_flow.setup_needed(app.setup_record)
    assert app.missing_permissions == ["Input Monitoring"]
    app.menu_icon.stop()


def test_start_at_login_outside_the_app_explains_why_not(monkeypatch, tmp_path):
    app_mod, app = _app(monkeypatch, tmp_path)
    alerts = []
    monkeypatch.setattr(app_mod.rumps, "alert", lambda *a, **k: alerts.append(a))
    app.toggle_start_at_login()
    assert alerts and "Utter.app" in alerts[0][1]
    app.menu_icon.stop()


def test_hotkeys_restart_with_the_configured_hold_to_talk(monkeypatch, tmp_path):
    _, app = _app(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(app.hotkey_manager, "stop", lambda: calls.append("stop"))
    monkeypatch.setattr(app.hotkey_manager, "start", lambda: calls.append("start"))
    app.hotkey_manager.hold_to_talk = False  # switched off at start for lack of Accessibility
    app.restart_hotkeys()
    assert calls == ["stop", "start"]
    assert app.hotkey_manager.hold_to_talk == app.config.hotkeys.hold_to_talk
    app.menu_icon.stop()


def test_setup_window_opens_from_the_menu(monkeypatch, tmp_path):
    import interface.welcome_window as ww
    app_mod, app = _app(monkeypatch, tmp_path)
    monkeypatch.setattr(app_mod, "get_all_permissions", lambda: _perms(mic=True, ax=True, listen=True))
    monkeypatch.setattr(ww.WelcomeWindow, "show", lambda self: None)  # no window on screen in tests
    app.show_setup()
    assert app.welcome is not None and app.welcome.flow.step_name == "welcome"
    assert app.welcome.dictation_key == "⌃⌥D"
    assert app.welcome._restart is None  # restarting needs Utter.app
    app.welcome.close()
    app.menu_icon.stop()
