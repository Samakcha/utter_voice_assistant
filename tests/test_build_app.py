"""Tests for building Utter.app (scripts/build_app.py) and running inside it. No full build here."""

import plistlib
from pathlib import Path
import subprocess

import pytest

import engine.config as engine_config
from scripts import build_app


def test_info_plist_makes_a_menu_bar_app_that_can_ask_for_permissions():
    info = build_app.INFO_PLIST
    assert info["CFBundleName"] == info["CFBundleExecutable"] == "Utter"
    assert info["CFBundleIdentifier"] == build_app.BUNDLE_ID
    assert info["LSUIElement"] is True  # menu bar only, no Dock icon
    # Without these, macOS stops the app the moment it asks for the microphone / Notes
    assert "Audio never leaves" in info["NSMicrophoneUsageDescription"]
    assert "Notes" in info["NSAppleEventsUsageDescription"]
    assert info["LSArchitecturePriority"] == ["arm64"]
    plistlib.dumps(info)  # valid plist


def test_app_carries_the_code_and_shipped_settings_but_never_personal_data():
    assert set(build_app.APP_CODE) == {"main.py", "config.toml", "engine", "interface", "assets"}
    for personal in ("models", "recordings", "note_backups", "tests", ".venv"):
        assert personal not in build_app.APP_CODE
    assert build_app.ICON_SOURCE.is_file()
    assert build_app.LAUNCHER_SOURCE.is_file()


def test_dev_only_packages_are_left_out():
    for name in ("pip", "_pytest", "pytest", "PyObjCTest"):
        assert name in build_app.SITE_EXCLUDE
    for name in ("test", "idlelib", "tkinter", "ensurepip"):
        assert name in build_app.STDLIB_EXCLUDE


def test_launcher_runs_python_inside_utter_safely():
    source = build_app.LAUNCHER_SOURCE.read_text(encoding="utf-8")
    assert "Py_InitializeFromConfig" in source and "Py_RunMain" in source  # one process: "Utter"
    assert "config.write_bytecode = 0" in source  # never writes into the signed app
    assert "config.use_environment = 0" in source and "config.user_site_directory = 0" in source
    assert "preconfig.utf8_mode = 1" in source  # apps get no LANG from macOS
    assert "Resources/app/main.py" in source and "Resources/python" in source


SECURITY_OUTPUT = """
Policy: Code Signing
  Matching identities
  1) 3F2A9C0D1E2B3C4D5E6F708192A3B4C5D6E7F809 "Utter Developer" (CSSMERR_TP_NOT_TRUSTED)
     1 identities found

  Valid identities only
     0 valid identities found
"""


def test_signing_uses_the_utter_developer_certificate_when_present(monkeypatch):
    calls = []

    def fake_run(args, *a, **k):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=SECURITY_OUTPUT, stderr="")
    monkeypatch.setattr(build_app.subprocess, "run", fake_run)
    assert build_app.find_identity() == "3F2A9C0D1E2B3C4D5E6F708192A3B4C5D6E7F809"
    assert calls[0][:3] == ["security", "find-identity", "-p"]


def test_without_a_certificate_the_app_is_signed_ad_hoc(monkeypatch):
    monkeypatch.setattr(build_app.subprocess, "run",
                        lambda args, *a, **k: subprocess.CompletedProcess(args, 0, stdout="0 identities found", stderr=""))
    assert build_app.find_identity() is None


def test_install_refuses_while_utter_runs_from_applications(tmp_path, monkeypatch):
    app = tmp_path / "dist" / "Utter.app"
    (app / "Contents").mkdir(parents=True)
    apps = tmp_path / "Applications"
    (apps / "Utter.app" / "Contents").mkdir(parents=True)
    monkeypatch.setattr(build_app.subprocess, "run",
                        lambda args, *a, **k: subprocess.CompletedProcess(args, 0 if args[0] == "pgrep" else 0))
    with pytest.raises(SystemExit, match="Quit it first"):
        build_app.install(app, applications=apps)
    assert (apps / "Utter.app").is_dir()  # the running copy was not touched


def test_running_as_app_is_detected_from_the_code_location(tmp_path):
    inside = tmp_path / "Utter.app" / "Contents" / "Resources" / "app"
    assert engine_config.running_as_app(inside) is True
    assert engine_config.running_as_app(tmp_path / "voice-assistant") is False
    assert engine_config.running_as_app() is False  # the tests run from the project folder


def test_only_one_utter_at_a_time(tmp_path):
    from main import acquire_single_instance_lock
    first = acquire_single_instance_lock(tmp_path)
    assert first is not None
    assert acquire_single_instance_lock(tmp_path) is None  # a second Utter is refused
    first.close()
    again = acquire_single_instance_lock(tmp_path)  # released when the first one quits
    assert again is not None
    again.close()


def test_permission_report_names_utter_when_running_as_the_app(monkeypatch):
    from interface import permissions
    missing = {"accessibility": permissions.PermissionItem(
        name="Accessibility", granted=False, settings_path="x", purpose="y", deep_link_url="z")}
    monkeypatch.setattr(engine_config, "running_as_app", lambda: True)
    report = permissions.format_permissions_report(missing)
    assert "Running as: Utter.app" in report and 'for "Utter"' in report
    monkeypatch.setattr(engine_config, "running_as_app", lambda: False)
    assert "terminal application" in permissions.format_permissions_report(missing)
