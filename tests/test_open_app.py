"""Open any installed app by voice: inventory, name matching, command rule, launch, app wiring."""

import plistlib
import threading
import time
from pathlib import Path
from typing import List

import pytest

from engine import actions
from engine.apps import (
    LEVEL_ALIAS, LEVEL_COMPACT, LEVEL_EXACT, LEVEL_FUZZY, LEVEL_PARTIAL,
    STATUS_AMBIGUOUS, STATUS_FOUND, STATUS_UNKNOWN,
    AppEntry, AppInventory, clean_spoken_app_name, resolve_app_name, scan_applications,
)
from engine.commands import allows_early_finish, match_command
from engine.models import SessionMode, TranscriptionResult


def app(name: str, *others: str, folder: str = "/Applications") -> AppEntry:
    return AppEntry(name=name, path=Path(folder) / f"{name}.app", bundle_id=f"test.{name}", other_names=others)


APPS = [
    app("Safari"), app("Calculator"), app("System Settings"), app("FaceTime"), app("Xcode"),
    app("Google Chrome"), app("Google Drive"), app("Visual Studio Code", "Code"),
    app("Image Capture"), app("Image Playground"), app("Antigravity"), app("Antigravity IDE"),
    app("Spotify"), app("VoiceMemos", "Voice Memos"), app("Activity Monitor"), app("Finder"),
    app("Notes"), app("WhatsApp"), app("QuickTime Player"), app("Music"), app("TV"),
]


def resolve(spoken, aliases=None, apps=APPS):
    return resolve_app_name(spoken, apps, aliases)


# ---- Matching levels ---------------------------------------------------------------------

@pytest.mark.parametrize("spoken, expected, level", [
    ("Safari", "Safari", LEVEL_EXACT),
    ("calculator", "Calculator", LEVEL_EXACT),
    ("System Settings.", "System Settings", LEVEL_EXACT),
    ("the Spotify app", "Spotify", LEVEL_EXACT),
    ("my music", "Music", LEVEL_EXACT),
    ("TV", "TV", LEVEL_EXACT),
    ("code", "Visual Studio Code", LEVEL_EXACT),            # Info.plist name
    ("voice memos", "VoiceMemos", LEVEL_EXACT),             # Info.plist display name
    ("face time", "FaceTime", LEVEL_COMPACT),
    ("x code", "Xcode", LEVEL_COMPACT),
    ("chrome", "Google Chrome", LEVEL_ALIAS),                # built-in alias
    ("settings", "System Settings", LEVEL_ALIAS),
    ("system preferences", "System Settings", LEVEL_ALIAS),
    ("quick time", "QuickTime Player", LEVEL_ALIAS),         # alias, spaces ignored
    ("activity", "Activity Monitor", LEVEL_PARTIAL),
    ("studio code", "Visual Studio Code", LEVEL_PARTIAL),
    ("spotifi", "Spotify", LEVEL_FUZZY),
    ("calculater", "Calculator", LEVEL_FUZZY),
])
def test_names_resolve_to_one_app(spoken, expected, level):
    r = resolve(spoken)
    assert r.status == STATUS_FOUND and r.app.name == expected and r.level == level, r


@pytest.mark.parametrize("spoken, candidates", [
    ("google", ["Google Chrome", "Google Drive"]),
    ("image", ["Image Capture", "Image Playground"]),
])
def test_ambiguous_names_open_nothing_and_list_the_choices(spoken, candidates):
    r = resolve(spoken)
    assert r.status == STATUS_AMBIGUOUS and r.app is None
    assert [c.name for c in r.candidates] == candidates


@pytest.mark.parametrize("spoken", [
    "door", "the door", "app", "the app", "pro", "", "a", "cloud", "xyz", "safarix browser",
    "mac", "player",
])
def test_unknown_or_generic_names_open_nothing(spoken):
    assert resolve(spoken).status == STATUS_UNKNOWN


def test_exact_name_beats_a_longer_app_with_the_same_start():
    assert resolve("antigravity").app.name == "Antigravity"
    assert resolve("antigravity ide").app.name == "Antigravity IDE"


def test_fuzzy_needs_a_long_enough_name_and_one_clear_winner():
    assert resolve("musi").status == STATUS_UNKNOWN          # too short to guess
    twins = [app("Notion"), app("Motion")]
    assert resolve("lotion", apps=twins).status == STATUS_AMBIGUOUS


def test_user_aliases_come_first_and_need_the_app_installed():
    assert resolve("cloud", aliases={"cloud": "Claude"}).status == STATUS_UNKNOWN  # not installed
    r = resolve("cloud", aliases={"cloud": "Safari"})
    assert r.found and r.app.name == "Safari" and r.level == LEVEL_ALIAS
    # a built-in alias whose app isn't installed falls through to the other levels
    assert resolve("word").status == STATUS_UNKNOWN


def test_clean_spoken_name():
    assert clean_spoken_app_name("The Google Chrome app.") == "google chrome"
    assert clean_spoken_app_name("my App Store") == "app store"


# ---- Inventory ---------------------------------------------------------------------------

def test_can_finish_early_only_for_a_sure_complete_name():
    inv = AppInventory(scanner=lambda: list(APPS))
    assert inv.can_finish_early("Calculator")
    assert inv.can_finish_early("face time")
    assert not inv.can_finish_early("Antigravity")   # "IDE" may follow
    assert not inv.can_finish_early("google")        # ambiguous
    assert not inv.can_finish_early("spotifi")       # fuzzy: wait for the full sentence
    assert not inv.can_finish_early("door")


def test_unknown_name_triggers_one_rescan_for_newly_installed_apps():
    installed = [app("Safari")]
    calls = []

    def scanner():
        calls.append(1)
        return list(installed)

    inv = AppInventory(scanner=scanner)
    assert inv.resolve("Safari").found and len(calls) == 1
    installed.append(app("Figma"))
    inv._scanned_at -= 10  # pretend the last scan was a while ago
    assert inv.resolve_or_refresh("Figma").found and len(calls) == 2
    assert inv.resolve_or_refresh("Nothing Here").status == STATUS_UNKNOWN
    assert len(calls) == 2  # not rescanned again right away


def test_scanner_failure_means_no_apps_not_a_crash():
    def broken():
        raise OSError("disk gone")

    assert AppInventory(scanner=broken).resolve("Safari").status == STATUS_UNKNOWN


def _make_app(folder: Path, name: str, **info) -> Path:
    bundle = folder / f"{name}.app"
    (bundle / "Contents").mkdir(parents=True)
    with open(bundle / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleIdentifier": f"test.{name.replace(' ', '')}", **info}, f)
    return bundle


def test_scan_finds_apps_in_folders_and_one_subfolder_level(tmp_path):
    apps_dir = tmp_path / "Applications"
    apps_dir.mkdir()
    _make_app(apps_dir, "Alpha")
    _make_app(apps_dir, "Beta Tool", CFBundleDisplayName="Beta")
    _make_app(apps_dir / "Suite", "Gamma")                       # one level down
    _make_app(apps_dir, "Helper", LSBackgroundOnly=True)          # background only: skipped
    _make_app(apps_dir, ".Hidden")                                # hidden: skipped
    _make_app(apps_dir / "Alpha.app" / "Contents", "Inner")       # inside a bundle: not scanned
    other = tmp_path / "Other"
    other.mkdir()
    _make_app(other, "Alpha Copy", CFBundleIdentifier="test.Alpha")  # same bundle id: skipped

    found = scan_applications([apps_dir, other], extra_apps=[])
    names = sorted(a.name for a in found)
    assert names == ["Alpha", "Beta Tool", "Gamma"]
    beta = next(a for a in found if a.name == "Beta Tool")
    assert beta.other_names == ("Beta",)
    assert resolve_app_name("beta", found).app.name == "Beta Tool"


def test_scan_survives_missing_folders_and_broken_plists(tmp_path):
    broken = tmp_path / "Apps" / "Broken.app" / "Contents"
    broken.mkdir(parents=True)
    (broken / "Info.plist").write_bytes(b"not a plist")
    found = scan_applications([tmp_path / "Apps", tmp_path / "missing"], extra_apps=[])
    assert [a.name for a in found] == ["Broken"]


# ---- Command rule ------------------------------------------------------------------------

@pytest.mark.parametrize("text, name", [
    ("Open Safari.", "Safari"),
    ("launch Calculator please", "Calculator"),
    ("switch to Finder", "Finder"),
    ("Bring up Activity Monitor.", "Activity Monitor"),
    ("Can you open Google Chrome for me?", "Google Chrome"),
    ("Open up System Settings", "System Settings"),
    ("open the Spotify app", "the Spotify app"),
])
def test_open_app_rule_extracts_the_spoken_name(text, name):
    m = match_command(text)
    assert m is not None and m.action_id == "open_app" and m.extracted_name == name


@pytest.mark.parametrize("text, action", [
    ("open notes", "open_notes"),
    ("Open the Notes app.", "open_notes"),
    ("open WhatsApp", "open_whatsapp"),
    ("launch WhatsApp", "open_whatsapp"),
    ("take a note", "take_note"),
])
def test_notes_and_whatsapp_keep_their_own_commands(text, action):
    assert match_command(text).action_id == action


@pytest.mark.parametrize("text", [
    "open a note", "open notes folder", "open WhatsApp and search Alex Test",
    "open Safari and search for flights", "open it later", "I want to open Safari",
    "open", "open up", "please open", "launch a new note",
    "open one two three four five six",   # too long to be an app name
])
def test_open_app_rule_rejects_sentences(text):
    m = match_command(text)
    assert m is None or m.action_id != "open_app", m


def test_open_app_never_finishes_early_on_the_rule_alone():
    assert not allows_early_finish(match_command("open Calculator"))


# ---- Launch ------------------------------------------------------------------------------

def test_launch_app_runs_open_with_the_app_path(tmp_path, monkeypatch):
    bundle = tmp_path / "Calculator.app"
    bundle.mkdir()
    seen = []

    class Done:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(actions.subprocess, "run", lambda args, **kw: seen.append((args, kw)) or Done())
    res = actions.launch_app(bundle, "Calculator")
    assert res.success and res.message == "Opening Calculator"
    args, kw = seen[0]
    assert args == ["open", str(bundle)] and not kw.get("shell")


def test_launch_app_refuses_anything_but_a_real_app_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(actions.subprocess, "run", lambda *a, **k: pytest.fail("must not run"))
    (tmp_path / "script.sh").write_text("echo hi")
    assert not actions.launch_app(tmp_path / "script.sh", "x").success
    assert not actions.launch_app(tmp_path / "Missing.app", "x").success


def test_launch_app_reports_failure(tmp_path, monkeypatch):
    bundle = tmp_path / "Broken.app"
    bundle.mkdir()

    class Failed:
        returncode = 1
        stderr = "LSOpenURLsWithRole() failed"

    monkeypatch.setattr(actions.subprocess, "run", lambda *a, **k: Failed())
    res = actions.launch_app(bundle, "Broken")
    assert not res.success and "LSOpen" in res.error


# ---- App wiring --------------------------------------------------------------------------

class FakeOverlay:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a, **k: self.calls.append((name, a))


def _voice_app(monkeypatch, apps: List[AppEntry]):
    import interface.app as app_module
    from engine.config import load_config
    from engine.session import SessionManager
    from tests.test_command_early_finish import MockAudioCapture, MockVAD

    config = load_config()
    mgr = SessionManager(config=config, vad=MockVAD(), audio_capture=MockAudioCapture())
    va = app_module.VoiceAssistantApp(config=config, session_manager=mgr, transcriber=None)
    va.overlay = FakeOverlay()
    va.app_inventory = AppInventory(scanner=lambda: list(apps))
    monkeypatch.setattr(app_module, "run_on_main_thread", lambda fn, *a: fn(*a))
    monkeypatch.setattr(app_module.rumps, "notification", lambda **kw: None)
    launched = []

    def fake_launch(path, name, timeout_sec=5.0):
        launched.append((path, name))
        return actions.ActionResult(success=True, action_id="open_app", message=f"Opening {name}")

    monkeypatch.setattr(app_module, "launch_app", fake_launch)
    done = threading.Event()
    real_handle = va._handle_action_result

    def handle(*a, **k):
        real_handle(*a, **k)
        done.set()

    va._handle_action_result = handle
    return va, launched, done


def _say(va, text):
    from engine.models import EndReason, SessionResult
    tx = TranscriptionResult(text=text, language="en", audio_duration_sec=1.0, processing_time_sec=0.3)
    result = SessionResult(
        mode=SessionMode.COMMAND, end_reason=EndReason.SILENCE, duration_sec=1.0, audio_path=None,
        speech_detected=True, sample_rate=16000, total_samples=16000, session_id="s1",
    )
    va._on_transcription_success(tx, result)


def test_spoken_app_is_opened(monkeypatch):
    va, launched, done = _voice_app(monkeypatch, [app("Calculator")])
    _say(va, "Open Calculator.")
    assert done.wait(2.0)
    assert launched == [(Path("/Applications/Calculator.app"), "Calculator")]
    assert ("show_action_success", ("Open Calculator.", "Opening Calculator")) in va.overlay.calls


@pytest.mark.parametrize("text, shown", [
    ("Open the door.", 'No app called "door"'),
    ("Open Google.", "Which app: Google Chrome or Google Drive? Say the full name."),
])
def test_unknown_or_ambiguous_app_opens_nothing_and_says_why(monkeypatch, text, shown):
    va, launched, done = _voice_app(monkeypatch, [app("Google Chrome"), app("Google Drive")])
    _say(va, text)
    assert done.wait(2.0)
    assert launched == []
    assert ("show_not_run", (shown,)) in va.overlay.calls


def test_notes_command_still_goes_to_notes(monkeypatch):
    va, launched, done = _voice_app(monkeypatch, [app("Notes")])
    import interface.app as app_module
    ran = []
    monkeypatch.setattr(app_module, "execute_action_async", lambda action_id, callback, timeout_sec: ran.append(action_id))
    _say(va, "Open notes.")
    time.sleep(0.05)
    assert ran == ["open_notes"] and launched == []


def test_app_early_finish_check(monkeypatch):
    va, _launched, _done = _voice_app(monkeypatch, [app("Calculator"), app("Antigravity"), app("Antigravity IDE")])
    assert va._app_name_is_complete(match_command("Open Calculator."))
    assert not va._app_name_is_complete(match_command("Open Antigravity."))
    assert not va._app_name_is_complete(match_command("Open notes."))
    va.app_inventory = None  # [apps] enabled = false
    assert not va._app_name_is_complete(match_command("Open Calculator."))


def test_disabled_apps_open_nothing(monkeypatch):
    va, launched, done = _voice_app(monkeypatch, [app("Calculator")])
    va.app_inventory = None
    _say(va, "Open Calculator.")
    assert done.wait(2.0)
    assert launched == []
