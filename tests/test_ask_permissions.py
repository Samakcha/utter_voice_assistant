"""Utter asks macOS for missing permissions (so it appears in System Settings). All system calls faked."""

import sys
import types

import pytest

from interface import permissions as perms


def _perms(mic=False, ax=False, listen=False):
    def item(name, granted):
        return perms.PermissionItem(name=name, granted=granted, settings_path="", deep_link_url="", purpose="")
    return {"microphone": item("Microphone", mic), "accessibility": item("Accessibility", ax),
            "input_monitoring": item("Input Monitoring", listen)}


@pytest.fixture
def fake_macos(monkeypatch):
    calls = []
    av = types.SimpleNamespace(
        AVMediaTypeAudio="soun", status=0,
        AVCaptureDevice=None,
    )

    class Device:
        @staticmethod
        def authorizationStatusForMediaType_(media):
            return av.status

        @staticmethod
        def requestAccessForMediaType_completionHandler_(media, handler):
            calls.append(("microphone", media))
    av.AVCaptureDevice = Device
    app_services = types.SimpleNamespace(
        kAXTrustedCheckOptionPrompt="AXTrustedCheckOptionPrompt",
        AXIsProcessTrustedWithOptions=lambda options: calls.append(("accessibility", options)) or False,
    )
    quartz = types.SimpleNamespace(CGRequestListenEventAccess=lambda: calls.append(("input_monitoring",)) or False)
    monkeypatch.setitem(sys.modules, "AVFoundation", av)
    monkeypatch.setitem(sys.modules, "ApplicationServices", app_services)
    monkeypatch.setitem(sys.modules, "Quartz", quartz)
    return calls, av


def test_asks_for_every_missing_permission_with_the_system_prompt(fake_macos):
    calls, _ = fake_macos
    asked = perms.request_missing_permissions(_perms())
    assert asked == ["Microphone", "Accessibility", "Input Monitoring"]
    assert ("microphone", "soun") in calls
    assert ("accessibility", {"AXTrustedCheckOptionPrompt": True}) in calls  # prompt + listed in Settings
    assert ("input_monitoring",) in calls


def test_granted_permissions_are_not_asked_again(fake_macos):
    calls, _ = fake_macos
    assert perms.request_missing_permissions(_perms(mic=True, ax=True, listen=True)) == []
    assert calls == []
    assert perms.request_missing_permissions(_perms(mic=True, ax=False, listen=True)) == ["Accessibility"]


def test_a_refused_microphone_is_not_asked_again(fake_macos):
    calls, av = fake_macos
    av.status = 2  # the user clicked Don't Allow before: macOS will not show the prompt again
    assert "Microphone" not in perms.request_missing_permissions(_perms(ax=True, listen=True))
    assert not any(c[0] == "microphone" for c in calls)


def test_check_permissions_dialog_asks_then_opens_the_right_pane(monkeypatch):
    import dataclasses
    import interface.app as app_mod
    from engine.config import VocabularyConfig, load_config
    from engine.session import SessionManager
    config = dataclasses.replace(load_config(), vocabulary=VocabularyConfig(file=None))
    app = app_mod.VoiceAssistantApp(config=config, session_manager=SessionManager(config=config))
    app.overlay = None
    events = []
    monkeypatch.setattr(app_mod, "get_all_permissions", lambda: _perms(mic=True, ax=True, listen=False))
    monkeypatch.setattr(app_mod.rumps, "alert", lambda *a, **k: 1)  # "Open System Settings"
    monkeypatch.setattr(app_mod, "request_missing_permissions", lambda p: events.append("asked"))
    monkeypatch.setattr(app_mod, "open_settings_pane", lambda name: events.append(name))
    app.show_permissions_dialog()
    assert events == ["asked", "input_monitoring"]
    app.menu_icon.stop()


def test_startup_asks_only_when_running_as_the_app(monkeypatch, isolate_data_paths):
    import main as main_mod
    import engine.config as engine_config
    asked = []
    monkeypatch.setattr(main_mod, "ask_missing_once", lambda p, record: asked.append(record.name))
    monkeypatch.setattr(main_mod, "get_all_permissions", lambda: _perms())
    monkeypatch.setattr(sys, "argv", ["main.py"])
    monkeypatch.setattr(main_mod, "_instance_lock", None)
    monkeypatch.setattr(engine_config, "running_as_app", lambda: False)
    monkeypatch.setattr(main_mod, "MLXWhisperTranscriber", lambda **k: types.SimpleNamespace(on_ready=None))
    app = main_mod.main(run_loop=False)
    assert asked == []  # terminal version: the terminal's permissions are used
    app.hotkey_manager.stop()
    app.menu_icon.stop()
    main_mod._instance_lock.close()
    monkeypatch.setattr(main_mod, "_instance_lock", None)
    monkeypatch.setattr(engine_config, "running_as_app", lambda: True)
    app = main_mod.main(run_loop=False)
    assert asked == ["permissions_asked.json"]  # the app asks macOS itself (once ever)
    app.hotkey_manager.stop()
    app.menu_icon.stop()
    main_mod._instance_lock.close()


def test_macos_window_appears_only_on_the_first_start(fake_macos, tmp_path):
    calls, _ = fake_macos
    record = tmp_path / "data" / perms.ASKED_FILE_NAME
    first = perms.ask_missing_once(_perms(mic=True, listen=True), record)
    assert first == ["Accessibility"] and record.is_file()
    calls.clear()
    # Second, third... start: still reported missing by macOS, but no window again
    for _ in range(3):
        assert perms.ask_missing_once(_perms(mic=True, listen=True), record) == []
    assert calls == []
    # A permission never asked before is still asked once
    assert perms.ask_missing_once(_perms(mic=True, listen=False), record) == ["Input Monitoring"]


def test_a_broken_record_file_just_means_asking_again(fake_macos, tmp_path):
    record = tmp_path / perms.ASKED_FILE_NAME
    record.write_text("not json", encoding="utf-8")
    assert perms.ask_missing_once(_perms(mic=True, listen=True), record) == ["Accessibility"]


def test_menu_says_quietly_which_permission_is_missing(monkeypatch):
    import dataclasses
    import interface.app as app_mod
    from engine.config import VocabularyConfig, load_config
    from engine.session import SessionManager
    config = dataclasses.replace(load_config(), vocabulary=VocabularyConfig(file=None))
    app = app_mod.VoiceAssistantApp(config=config, session_manager=SessionManager(config=config))
    app.overlay = None
    try:
        assert app._ready_status_text() == "Status: Ready (Mic closed)"
        app.missing_permissions = ["Accessibility"]
        assert "Needs permission: Accessibility" in app._ready_status_text()
        # After granting and clicking Check Permissions, the hint goes away
        monkeypatch.setattr(app_mod, "get_all_permissions", lambda: _perms(mic=True, ax=True, listen=True))
        monkeypatch.setattr(app_mod.rumps, "alert", lambda *a, **k: 0)
        app.show_permissions_dialog()
        assert app.missing_permissions == [] and app.status_item.title == "Status: Ready (Mic closed)"
    finally:
        app.menu_icon.stop()
