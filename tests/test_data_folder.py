"""Tests for Utter's data folder setup: rename, settings copy, model move, log file, menu items."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys

import pytest

import engine.config as engine_config
from engine.config import DataPaths, load_config, load_user_config
from engine.data_folder import prepare_data_folder


def _paths(tmp_path: Path) -> DataPaths:
    return DataPaths(
        data_dir=tmp_path / "Application Support" / "Utter",
        legacy_data_dir=tmp_path / "Application Support" / "VoiceAssistant",
        log_dir=tmp_path / "Logs" / "Utter",
        legacy_models_dir=tmp_path / "project" / "models",
        default_config=engine_config.DEFAULT_CONFIG_PATH,
    )


def _old_folder(paths: DataPaths) -> None:
    old = paths.legacy_data_dir
    (old / "accuracy" / "recordings").mkdir(parents=True)
    (old / "accuracy" / "recordings" / "c01.wav").write_bytes(b"RIFF....")
    (old / "vocabulary.txt").write_text("Rahul\nSaanvi\n", encoding="utf-8")
    (old / "note_backups").mkdir()
    (old / "note_backups" / "note.txt").write_text("buy milk", encoding="utf-8")


def _model(folder: Path, name: str) -> None:
    (folder / name).mkdir(parents=True)
    (folder / name / "weights.npz").write_bytes(b"x" * 1000)
    (folder / name / "config.json").write_text("{}", encoding="utf-8")


def _files(root: Path):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


# ---- 1. Rename VoiceAssistant -> Utter ----------------------------------------------------

def test_old_folder_is_renamed_with_everything_in_it(tmp_path):
    paths = _paths(tmp_path)
    _old_folder(paths)
    before = _files(paths.legacy_data_dir)

    done = prepare_data_folder(paths)

    assert not paths.legacy_data_dir.exists()
    assert set(before) <= set(_files(paths.data_dir))  # nothing lost
    assert (paths.data_dir / "vocabulary.txt").read_text(encoding="utf-8") == "Rahul\nSaanvi\n"
    assert any("Renamed your data folder" in m for m in done)


def test_an_empty_new_folder_does_not_block_the_rename(tmp_path):
    paths = _paths(tmp_path)
    _old_folder(paths)
    paths.data_dir.mkdir(parents=True)
    (paths.data_dir / ".DS_Store").write_bytes(b"finder")
    prepare_data_folder(paths)
    assert (paths.data_dir / "vocabulary.txt").is_file() and not paths.legacy_data_dir.exists()


def test_when_both_folders_have_data_nothing_is_merged_or_deleted(tmp_path):
    paths = _paths(tmp_path)
    _old_folder(paths)
    paths.data_dir.mkdir(parents=True)
    (paths.data_dir / "vocabulary.txt").write_text("Ananya\n", encoding="utf-8")
    old_files = _files(paths.legacy_data_dir)

    done = prepare_data_folder(paths)

    assert _files(paths.legacy_data_dir) == old_files
    assert (paths.data_dir / "vocabulary.txt").read_text(encoding="utf-8") == "Ananya\n"
    assert any("leaving the old folder untouched" in m for m in done)


def test_fresh_mac_just_creates_the_folder(tmp_path):
    paths = _paths(tmp_path)
    done = prepare_data_folder(paths)
    assert paths.data_dir.is_dir() and not paths.legacy_data_dir.exists()
    assert all("Renamed" not in m for m in done)


# ---- 2. Settings ----------------------------------------------------------------------------

def test_default_settings_are_copied_once_and_then_left_alone(tmp_path):
    paths = _paths(tmp_path)
    done = prepare_data_folder(paths)
    assert paths.user_config.read_text(encoding="utf-8") == paths.default_config.read_text(encoding="utf-8")
    assert any("Copied the default settings" in m for m in done)

    edited = paths.user_config.read_text(encoding="utf-8").replace("repeats = true", "repeats = false")
    paths.user_config.write_text(edited, encoding="utf-8")
    assert not any("settings" in m for m in prepare_data_folder(paths))  # second start: nothing to do
    assert paths.user_config.read_text(encoding="utf-8") == edited
    assert load_user_config(paths).cleanup.repeats is False


def test_old_folder_paths_in_a_settings_file_are_updated_after_the_rename(tmp_path):
    paths = _paths(tmp_path)
    paths.data_dir.mkdir(parents=True)
    text = paths.default_config.read_text(encoding="utf-8").replace(
        "Application Support/Utter/vocabulary.txt", "Application Support/VoiceAssistant/vocabulary.txt")
    paths.user_config.write_text(text, encoding="utf-8")
    prepare_data_folder(paths)
    assert "VoiceAssistant" not in paths.user_config.read_text(encoding="utf-8")


def test_without_a_settings_file_the_shipped_defaults_are_used(tmp_path):
    paths = _paths(tmp_path)
    assert load_user_config(paths) == load_config()


def test_shipped_settings_point_into_the_utter_folder():
    config = load_config()
    utter = str(engine_config.DEFAULT_DATA_DIR.expanduser())
    for path in (config.storage.recordings_dir, config.storage.note_backups_dir,
                 config.vocabulary.file, config.whisper.models_dir):
        assert str(path).startswith(utter), path
    assert "VoiceAssistant" not in engine_config.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")


# ---- 3. Models ------------------------------------------------------------------------------

def test_models_move_out_of_the_project_folder(tmp_path):
    paths = _paths(tmp_path)
    _model(paths.legacy_models_dir, "whisper-large-v3-turbo-mlx")
    _model(paths.legacy_models_dir, "whisper-small-mlx")

    done = prepare_data_folder(paths)

    for name in ("whisper-large-v3-turbo-mlx", "whisper-small-mlx"):
        assert (paths.models_dir / name / "weights.npz").stat().st_size == 1000
        assert not (paths.legacy_models_dir / name).exists()
    assert not list(paths.models_dir.glob("*.moving"))
    assert any("Moved Whisper model(s)" in m for m in done)
    assert prepare_data_folder(paths) == []  # second start: nothing left to do


def test_a_model_already_in_the_data_folder_is_never_overwritten(tmp_path):
    paths = _paths(tmp_path)
    _model(paths.legacy_models_dir, "whisper-small-mlx")
    _model(paths.models_dir, "whisper-small-mlx")
    (paths.models_dir / "whisper-small-mlx" / "weights.npz").write_bytes(b"newer")

    done = prepare_data_folder(paths)

    assert (paths.models_dir / "whisper-small-mlx" / "weights.npz").read_bytes() == b"newer"
    assert (paths.legacy_models_dir / "whisper-small-mlx").is_dir()
    assert any("a copy is already in" in m for m in done)


def test_an_interrupted_move_is_redone_from_the_intact_original(tmp_path):
    paths = _paths(tmp_path)
    _model(paths.legacy_models_dir, "whisper-small-mlx")
    (paths.models_dir / "whisper-small-mlx.moving").mkdir(parents=True)  # half-finished copy
    (paths.models_dir / "whisper-small-mlx.moving" / "weights.npz").write_bytes(b"half")

    prepare_data_folder(paths)

    assert (paths.models_dir / "whisper-small-mlx" / "weights.npz").stat().st_size == 1000
    assert not (paths.models_dir / "whisper-small-mlx.moving").exists()


def test_the_app_finds_models_in_either_place(tmp_path, monkeypatch):
    from engine.transcriber import resolve_local_model
    old, new = tmp_path / "project-models", tmp_path / "data-models"
    monkeypatch.setattr(engine_config, "LEGACY_MODELS_DIR", old)
    _model(old, "whisper-small-mlx")
    path, size = resolve_local_model("small", new)
    assert path == str(old / "whisper-small-mlx") and size == "small"  # not moved yet
    _model(new, "whisper-small-mlx")
    assert resolve_local_model("small", new)[0] == str(new / "whisper-small-mlx")  # data folder first


def test_a_failing_step_never_stops_utter_from_starting(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    _model(paths.legacy_models_dir, "whisper-small-mlx")
    import engine.data_folder as data_folder

    def broken_move(*_args):
        raise OSError("disk full")
    monkeypatch.setattr(data_folder.shutil, "move", broken_move)

    done = prepare_data_folder(paths)

    assert any("failed: disk full" in m for m in done)
    assert (paths.legacy_models_dir / "whisper-small-mlx" / "weights.npz").is_file()  # original intact
    assert paths.user_config.is_file()  # the other steps still ran


# ---- Log file and startup -------------------------------------------------------------------

@pytest.fixture
def clean_root_logger():
    root = logging.getLogger()
    before, level = list(root.handlers), root.level
    yield
    root.setLevel(level)
    for handler in list(root.handlers):
        if handler not in before:
            root.removeHandler(handler)
            handler.close()


def test_log_file_is_created_rotates_and_is_set_up_once(tmp_path, clean_root_logger):
    from main import LOG_BACKUPS, LOG_MAX_BYTES, setup_file_logging
    log_dir = tmp_path / "Logs" / "Utter"
    path = setup_file_logging(log_dir)
    assert path == log_dir / "utter.log"
    assert setup_file_logging(log_dir) == path
    handlers = [h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)
                and Path(h.baseFilename) == path]
    assert len(handlers) == 1
    assert handlers[0].maxBytes == LOG_MAX_BYTES and handlers[0].backupCount == LOG_BACKUPS

    logging.getLogger("interface.app").info("[Command: open_notes] OK | Total: 0.912s")
    handlers[0].flush()
    assert "Total: 0.912s" in path.read_text(encoding="utf-8")


def test_startup_sets_up_the_data_folder_settings_and_log(monkeypatch, isolate_data_paths, clean_root_logger):
    from main import main
    monkeypatch.setattr(sys, "argv", ["main.py", "--check"])
    assert main(run_loop=False) is None
    paths = isolate_data_paths
    assert paths.user_config.is_file()
    assert (paths.log_dir / "utter.log").is_file()
    assert "Settings: " in (paths.log_dir / "utter.log").read_text(encoding="utf-8")


def test_menu_opens_the_settings_file_and_the_log(tmp_path, monkeypatch):
    import dataclasses
    import subprocess
    import interface.app as app_mod
    from engine.config import VocabularyConfig
    from engine.session import SessionManager
    from interface.app import VoiceAssistantApp

    config = dataclasses.replace(load_config(), vocabulary=VocabularyConfig(file=None))
    app = VoiceAssistantApp(config=config, session_manager=SessionManager(config=config))
    app.overlay = None
    titles = [item for item in app.menu]
    assert "Settings…" in titles  # the settings file and the log open from the Settings window

    calls, alerts = [], []
    monkeypatch.setattr(subprocess, "run", lambda args, *a, **k: calls.append(args))
    monkeypatch.setattr(app_mod.rumps, "alert", lambda *a, **k: alerts.append(a))

    app.open_log()
    assert calls == [] and alerts  # no log file: explained, nothing opened

    app.settings_file = tmp_path / "config.toml"
    app.settings_file.write_text("", encoding="utf-8")
    app.log_file = tmp_path / "utter.log"
    app.log_file.write_text("", encoding="utf-8")
    app.open_settings_file()
    app.open_log()
    assert calls == [["open", "-t", str(app.settings_file)], ["open", str(app.log_file)]]
    app.menu_icon.stop()
