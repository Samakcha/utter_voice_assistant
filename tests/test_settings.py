"""Stage 4: the Settings window. Saving keeps the file's explanations; changes apply at once."""

import dataclasses
from pathlib import Path
import tomllib

import pytest

from engine.config import DEFAULT_CONFIG_PATH, VocabularyConfig, load_config
from engine.settings_file import SettingsSaveError, save_settings, toml_value, update_settings_text
from interface import settings_model as sm
from interface.key_events import FLAG_COMMAND, FLAG_CONTROL, FLAG_OPTION, FLAG_SHIFT, KEYCODES


@pytest.fixture
def settings(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    return path


# ---- Writing the file ---------------------------------------------------------------------

def test_values_are_written_the_toml_way():
    assert toml_value(True) == "true" and toml_value(8.0) == "8.0" and toml_value(3) == "3"
    assert toml_value('My "Notes"') == '"My \\"Notes\\""'
    assert toml_value(["stop note", "end it"]) == '["stop note", "end it"]'


def test_saving_changes_only_that_value_and_keeps_every_explanation(settings):
    before = settings.read_text(encoding="utf-8")
    config = save_settings(settings, {"hotkeys": {"command_mode": "<ctrl>+<alt>+k"},
                                      "timeouts": {"dictation_silence_sec": 15.0}})
    after = settings.read_text(encoding="utf-8")
    assert config.hotkeys.command_mode == "<ctrl>+<alt>+k"
    assert config.timeouts.dictation_silence_sec == 15.0
    changed = [(a, b) for a, b in zip(before.splitlines(), after.splitlines()) if a != b]
    assert changed == [('command_mode = "<ctrl>+<alt>+m"', 'command_mode = "<ctrl>+<alt>+k"'),
                       ("dictation_silence_sec = 8.0", "dictation_silence_sec = 15.0")]
    assert len(before.splitlines()) == len(after.splitlines())


def test_a_comment_after_a_value_and_a_list_over_several_lines_are_kept():
    text = '[dictation]\nstop_phrases = [\n  "stop dictation",\n  "end dictation",\n]\ntrailing_space = false  # mine\n'
    new = update_settings_text(text, {"dictation": {"stop_phrases": ["halt now"], "trailing_space": True}})
    assert new == '[dictation]\nstop_phrases = ["halt now"]\ntrailing_space = true  # mine\n'


def test_a_setting_or_section_not_in_the_file_yet_is_added():
    new = update_settings_text("[hotkeys]\ncommand_mode = \"x\"\n",
                               {"hotkeys": {"hold_to_talk": False}, "cleanup": {"fillers": False}})
    assert tomllib.loads(new) == {"hotkeys": {"command_mode": "x", "hold_to_talk": False},
                                  "cleanup": {"fillers": False}}


def test_app_names_replace_the_whole_table_and_keep_its_comments(settings):
    config = save_settings(settings, {}, {"apps.aliases": {"browser": "Safari", "my mail": "Mail"}})
    assert config.apps.aliases == {"browser": "Safari", "my mail": "Mail"}
    text = settings.read_text(encoding="utf-8")
    assert '# cloud = "Claude"' in text and '"my mail" = "Mail"' in text
    config = save_settings(settings, {}, {"apps.aliases": {"web": "Safari"}})
    assert config.apps.aliases == {"web": "Safari"}


def test_a_change_that_would_break_the_file_is_not_saved(settings):
    before = settings.read_text(encoding="utf-8")
    with pytest.raises(SettingsSaveError):
        save_settings(settings, {"timeouts": {"dictation_silence_sec": "not a number"}})
    assert settings.read_text(encoding="utf-8") == before
    assert not [p for p in settings.parent.iterdir() if p.name.startswith(".config-")]


def test_a_missing_settings_file_starts_from_the_shipped_one(tmp_path):
    path = tmp_path / "Utter" / "config.toml"
    config = save_settings(path, {"cleanup": {"repeats": False}}, default_config=DEFAULT_CONFIG_PATH)
    assert path.is_file() and config.cleanup.repeats is False
    assert "# Tidies dictation" in path.read_text(encoding="utf-8")


# ---- Shortcuts ------------------------------------------------------------------------------

def test_pressed_keys_become_a_shortcut():
    assert sm.combo_from_key(KEYCODES["k"], FLAG_CONTROL | FLAG_OPTION) == "<ctrl>+<alt>+k"
    assert sm.combo_from_key(KEYCODES["space"], FLAG_COMMAND | FLAG_SHIFT | FLAG_OPTION) == "<alt>+<shift>+<cmd>+<space>"
    assert sm.combo_from_key(999, FLAG_CONTROL) is None


def test_shortcuts_that_would_cause_trouble_are_refused(settings):
    config = load_config(settings)
    check = lambda combo: sm.check_shortcut("command_mode", combo, config)
    assert check("<ctrl>+<alt>+k") is None
    assert "normal typing" in check("<shift>+k")
    assert "other apps" in check("<cmd>+c")
    assert "already the shortcut for dictation" in check("<ctrl>+<alt>+d")
    assert "Esc" in check("<ctrl>+<esc>")
    assert "can't be used" in check(None)


# ---- Text settings ---------------------------------------------------------------------------

def test_stop_phrases_need_two_words():
    assert sm.parse_phrases(" stop dictation,end  dictation, Stop Dictation ") == ["stop dictation", "end dictation"]
    assert sm.check_phrases(["stop dictation"]) is None
    assert "too short" in sm.check_phrases(["okay"])
    assert "too short" in sm.check_phrases(["stop"])


def test_note_titles_only_use_date_and_time():
    assert sm.check_note_title("Voice note {date} {time}") is None
    assert "only {date} and {time}" in sm.check_note_title("Note {day}")
    assert "empty" in sm.check_note_title("  ")


def test_app_names_are_read_one_per_line():
    assert sm.parse_aliases("browser = Safari\n\n# a comment\nMy Mail= Mail") == (
        {"browser": "Safari", "my mail": "Mail"}, None)
    assert "Line 1" in sm.parse_aliases("browser Safari")[1]


def test_the_word_list_keeps_its_explanation(tmp_path):
    old = "# Personal word list\n# one per line\n\nRahul\nPRD\n"
    assert sm.word_list_file_text(old, "Rahul\nNeha\n\n") == "# Personal word list\n# one per line\nRahul\nNeha\n"
    assert sm.word_list_file_text(None, "Neha").startswith("# Personal word list")


def test_choices_include_a_value_set_by_hand(settings):
    config = dataclasses.replace(load_config(settings))
    choice = next(c for c in sm.CHOICES if c.key == "dictation_silence_sec")
    assert sm.choice_options(choice, config)[sm.choice_index(choice, config)][1] == 8.0
    config = save_settings(settings, {"timeouts": {"dictation_silence_sec": 12.0}})
    options = sm.choice_options(choice, config)
    assert ("12 seconds", 12.0) in options and options[sm.choice_index(choice, config)][1] == 12.0


def test_only_installed_speech_models_are_offered(tmp_path):
    (tmp_path / "whisper-small-mlx").mkdir()
    assert [m for m, _ in sm.installed_models(tmp_path, "small")] == ["small"]
    assert [m for m, _ in sm.installed_models(tmp_path, "large-v3-turbo")] == ["large-v3-turbo", "small"]


# ---- The model -------------------------------------------------------------------------------

def _model(settings, saved=None):
    return sm.SettingsModel(config=load_config(settings), settings_file=settings,
                            on_saved=lambda old, new: (saved if saved is not None else []).append((old, new)))


def test_a_change_is_saved_and_handed_to_the_app(settings):
    saved = []
    model = _model(settings, saved)
    toggle = next(t for t in sm.TOGGLES if (t.section, t.key) == ("cleanup", "fillers"))
    assert model.set_toggle(toggle, False) is None
    assert load_config(settings).cleanup.fillers is False
    assert saved[-1][0].cleanup.fillers is True and saved[-1][1].cleanup.fillers is False
    assert model.set_shortcut("dictation_mode", "<ctrl>+<alt>+j") is None
    assert load_config(settings).hotkeys.dictation_mode == "<ctrl>+<alt>+j"


def test_a_refused_change_is_not_saved(settings):
    saved = []
    model = _model(settings, saved)
    before = settings.read_text(encoding="utf-8")
    assert "too short" in model.set_phrases("dictation", "okay")
    assert "already the shortcut" in model.set_shortcut("dictation_mode", "<ctrl>+<alt>+m")
    assert "at least one" in model.set_phrases("notes", " , ")
    assert settings.read_text(encoding="utf-8") == before and saved == []


def test_turning_the_word_list_off_and_on(settings, tmp_path):
    model = _model(settings)
    toggle = next(t for t in sm.TOGGLES if (t.section, t.key) == ("vocabulary", "enabled"))
    model.set_toggle(toggle, False)
    assert model.config.vocabulary.file is None and not sm.toggle_value(model.config, "vocabulary", "enabled")
    assert "Turn on" in model.save_word_list("Rahul")
    model.set_toggle(toggle, True)
    assert sm.toggle_value(model.config, "vocabulary", "enabled")


# ---- The app uses the new settings at once ------------------------------------------------------

def _app(tmp_path, settings):
    import interface.app as app_mod
    from engine.session import SessionManager
    config = dataclasses.replace(load_config(settings), vocabulary=VocabularyConfig(file=None))
    app = app_mod.VoiceAssistantApp(config=config, session_manager=SessionManager(config=config))
    app.overlay = None
    app.settings_file = settings
    app.setup_record = tmp_path / "setup.json"
    return app


def test_new_shortcuts_restart_the_hotkeys(monkeypatch, tmp_path, settings):
    app = _app(tmp_path, settings)
    calls = []
    monkeypatch.setattr(app.hotkey_manager, "stop", lambda: calls.append("stop"))
    monkeypatch.setattr(app.hotkey_manager, "start", lambda: calls.append("start"))
    new = save_settings(settings, {"hotkeys": {"command_mode": "<ctrl>+<alt>+k", "hold_to_talk": False}})
    app.apply_settings(app.config, new)
    assert app.config is new and app.session_manager.config is new
    assert app.hotkey_manager._command_hotkey_str == "<ctrl>+<alt>+k"
    assert app.hotkey_manager.hold_to_talk is False and calls == ["stop", "start"]
    calls.clear()
    app.apply_settings(new, save_settings(settings, {"cleanup": {"fillers": False}}))
    assert calls == []  # nothing about the keys changed
    app.menu_icon.stop()


def test_recording_a_shortcut_pauses_the_hotkeys_until_done(monkeypatch, tmp_path, settings):
    app = _app(tmp_path, settings)
    calls = []
    monkeypatch.setattr(app.hotkey_manager, "stop", lambda: calls.append("stop"))
    monkeypatch.setattr(app.hotkey_manager, "start", lambda: calls.append("start"))
    app.pause_hotkeys()
    app.apply_settings(app.config, save_settings(settings, {"hotkeys": {"command_mode": "<ctrl>+<alt>+k"}}))
    assert calls == ["stop"]  # not started again while still recording
    app.resume_hotkeys()
    app.resume_hotkeys()
    assert calls == ["stop", "stop", "start"]  # resumed once (restart = stop + start)
    app.menu_icon.stop()


def test_app_names_and_open_apps_apply_at_once(monkeypatch, tmp_path, settings):
    import interface.app as app_mod
    app = _app(tmp_path, settings)
    monkeypatch.setattr(app_mod.threading, "Thread", lambda **k: type("T", (), {"start": lambda self: None})())
    new = save_settings(settings, {}, {"apps.aliases": {"web": "Safari"}})
    app.apply_settings(app.config, new)
    assert app.app_inventory.aliases == {"web": "Safari"}
    off = save_settings(settings, {"apps": {"enabled": False}})
    app.apply_settings(new, off)
    assert app.app_inventory is None
    app.apply_settings(off, save_settings(settings, {"apps": {"enabled": True}}))
    assert app.app_inventory is not None and app.app_inventory.aliases == {"web": "Safari"}
    app.menu_icon.stop()


def test_settings_window_opens_from_the_menu_and_saves(monkeypatch, tmp_path, settings):
    import interface.settings_window as sw
    app = _app(tmp_path, settings)
    monkeypatch.setattr(sw.SettingsWindow, "show", lambda self, page=None: None)
    app.show_settings()
    window = app.settings_window
    assert window is not None and window.page == "general"
    assert "Utter account" not in [str(v.stringValue()) for v in _labels(window)]  # accounts off in tests
    window.show_page("dictation")
    switch = window.toggles[("cleanup", "fillers")]
    switch.setState_(0)
    window._toggled(next(t for t in sm.TOGGLES if (t.section, t.key) == ("cleanup", "fillers")))
    assert app.config.cleanup.fillers is False and load_config(settings).cleanup.fillers is False
    assert window.message == "Saved."
    window.close()
    assert app.settings_window is None
    app.menu_icon.stop()


def _labels(window):
    found, stack = [], [window._doc]
    while stack:
        view = stack.pop()
        if view.isKindOfClass_(sm_text_field()):
            found.append(view)
        stack.extend(view.subviews())
    return found


def sm_text_field():
    import AppKit
    return AppKit.NSTextField


# ---- The window ----------------------------------------------------------------------------------

def _window(settings, **kwargs):
    from interface.settings_window import SettingsWindow
    events = []
    model = sm.SettingsModel(config=load_config(settings), settings_file=settings,
                             on_saved=lambda old, new: events.append("saved"))
    defaults = dict(pause_hotkeys=lambda: events.append("pause"), resume_hotkeys=lambda: events.append("resume"))
    defaults.update(kwargs)
    return SettingsWindow(model, **defaults), events


def test_every_page_draws(settings):
    window, _ = _window(settings, account_email=lambda: "a@b.co", login_status=lambda: "on")
    for page, _, _ in sm.PAGES:
        window.show_page(page)
        assert window.page == page
    window.show_page("general")
    assert "sign_out" in window.buttons and ("general", "start_at_login") in window.toggles
    window.close()


def test_recording_a_shortcut_in_the_window(settings):
    window, events = _window(settings)
    window.show_page("shortcuts")
    window.record_shortcut("command_mode")
    assert window.recording == "command_mode" and events == ["pause"]
    assert window.shortcut_buttons["command_mode"].title() == "Press keys…"
    window._key_pressed(KEYCODES["d"], FLAG_CONTROL | FLAG_OPTION)  # dictation's: refused, keep listening
    assert window.recording == "command_mode" and window.message_is_error
    window._key_pressed(KEYCODES["k"], FLAG_CONTROL | FLAG_OPTION)
    assert window.recording is None and events == ["pause", "saved", "resume"]
    assert window.shortcut_buttons["command_mode"].title() == "⌃⌥K"
    assert load_config(settings).hotkeys.command_mode == "<ctrl>+<alt>+k"
    window.record_shortcut("dictation_mode")
    window._key_pressed(KEYCODES["esc"], 0)  # Esc: keep the old one
    assert window.recording is None and load_config(settings).hotkeys.dictation_mode == "<ctrl>+<alt>+d"
    window.close()


def test_switches_that_depend_on_another_are_greyed_out(settings):
    window, _ = _window(settings)
    window.show_page("dictation")
    toggle = next(t for t in sm.TOGGLES if (t.section, t.key) == ("cleanup", "enabled"))
    window.toggles[("cleanup", "enabled")].setState_(0)
    window._toggled(toggle)
    assert not window.toggles[("cleanup", "fillers")].isEnabled()
    window.close()


def test_a_refused_text_shows_why_and_is_not_saved(settings):
    window, events = _window(settings)
    window.show_page("dictation")
    field = window.text_fields["dictation_stop"]
    field.setStringValue_("ok")
    window._text_committed("dictation_stop", lambda text: window.model.set_phrases("dictation", text))
    assert window.message_is_error and "too short" in window.message and "saved" not in events
    window.close()


def test_the_word_list_is_saved_when_the_window_closes(settings, tmp_path):
    words = tmp_path / "vocabulary.txt"
    words.write_text("# my list\nRahul\n", encoding="utf-8")
    text = settings.read_text(encoding="utf-8").replace(
        'file = "~/Library/Application Support/Utter/vocabulary.txt"', f'file = "{words}"')
    settings.write_text(text, encoding="utf-8")
    window, _ = _window(settings)
    window.show_page("words")
    view, _ = window.text_areas["words"]
    assert str(view.string()) == "Rahul"
    view.setString_("Rahul\nNeha")
    window.close()
    assert words.read_text(encoding="utf-8") == "# my list\nRahul\nNeha\n"


def test_switching_speech_model_offers_a_restart(settings):
    restarts = []
    window, _ = _window(settings, restart=lambda: restarts.append(1), active_model=lambda: "large-v3-turbo",
                        models=lambda: list(sm.MODELS))
    window.show_page("advanced")
    assert "restart" not in window.buttons
    window.choices[("whisper", "model_size")].selectItemAtIndex_(1)
    window._model_chosen(["large-v3-turbo", "small"])
    assert load_config(settings).whisper.model_size == "small" and "restart" in window.buttons
    window._restart_now()
    assert restarts == [1]
    window.close()


def test_a_text_field_being_edited_is_saved_when_switching_pages(settings):
    window, events = _window(settings)
    window.show_page("notes")
    window.window.makeKeyAndOrderFront_(None)
    field = window.text_fields["note_title"]
    window.window.makeFirstResponder_(field)
    field.currentEditor().setString_("Note {date}")
    window.show_page("general")
    assert load_config(settings).notes.title_template == "Note {date}"
    window.close()
