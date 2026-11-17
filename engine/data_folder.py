"""
One-time setup of Utter's data folder, run at every start (each step is skipped once done).

  1. ~/Library/Application Support/VoiceAssistant is renamed to .../Utter (nothing is
     copied or deleted; if both folders exist, the old one is left exactly as it is).
  2. The settings that ship with Utter are copied to <data folder>/config.toml once;
     from then on Utter reads that copy, so the user's settings survive updates.
  3. Whisper models in the project's ./models are moved to <data folder>/models
     (a rename on the same disk: instant, no extra space). Each model is moved under a
     temporary name and renamed only when complete, so an interrupted move never leaves
     a half-copied model that looks finished.

Never raises: a step that fails is reported and Utter starts anyway (models are still found
in the old place, and the shipped settings are used).
"""

import logging
import os
from pathlib import Path
import shutil
from typing import List

from .config import DataPaths

logger = logging.getLogger(__name__)

_LEGACY_NAME = "Application Support/VoiceAssistant/"
_NEW_NAME = "Application Support/Utter/"
_PARTIAL_SUFFIX = ".moving"


def prepare_data_folder(paths: DataPaths) -> List[str]:
    """Run the setup steps; returns what was done, in plain words (for the log)."""
    done: List[str] = []
    for step in (_rename_legacy_folder, _copy_default_settings, _move_models):
        try:
            message = step(paths)
        except OSError as e:
            message = f"{step.__name__.strip('_').replace('_', ' ')} failed: {e}"
            logger.error("Data folder setup: %s", message)
        if message:
            done.append(message)
    return done


def _is_empty_dir(path: Path) -> bool:
    return path.is_dir() and not any(p for p in path.iterdir() if p.name != ".DS_Store")


def _rename_legacy_folder(paths: DataPaths) -> str:
    old, new = paths.legacy_data_dir, paths.data_dir
    if not old.is_dir():
        new.mkdir(parents=True, exist_ok=True)
        return ""
    if new.exists():
        if not _is_empty_dir(new):
            return f"Both {old} and {new} exist; using {new} and leaving the old folder untouched."
        stray = new / ".DS_Store"  # an empty folder Finder touched: safe to replace
        if stray.exists():
            stray.unlink()
        new.rmdir()
    new.parent.mkdir(parents=True, exist_ok=True)
    os.rename(old, new)
    return f"Renamed your data folder {old} -> {new}."


def _copy_default_settings(paths: DataPaths) -> str:
    target = paths.user_config
    if target.is_file():
        text = target.read_text(encoding="utf-8")
        if _LEGACY_NAME in text and not paths.legacy_data_dir.exists():
            target.write_text(text.replace(_LEGACY_NAME, _NEW_NAME), encoding="utf-8")
            return f"Updated folder paths in your settings ({target}) to the Utter folder."
        return ""
    if not paths.default_config.is_file():
        return ""
    text = paths.default_config.read_text(encoding="utf-8").replace(_LEGACY_NAME, _NEW_NAME)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + _PARTIAL_SUFFIX)
    partial.write_text(text, encoding="utf-8")
    os.replace(partial, target)
    return f"Copied the default settings to {target} (edit this file to change your settings)."


def _move_models(paths: DataPaths) -> str:
    source, dest = paths.legacy_models_dir, paths.models_dir
    if not source.is_dir() or source.resolve() == dest.resolve():
        return ""
    moved, skipped = [], []
    for model in sorted(p for p in source.iterdir() if p.is_dir() and not p.name.endswith(_PARTIAL_SUFFIX)):
        final = dest / model.name
        if final.exists():
            skipped.append(model.name)  # already there: keep both, never overwrite
            continue
        dest.mkdir(parents=True, exist_ok=True)
        partial = dest / (model.name + _PARTIAL_SUFFIX)
        if partial.exists():
            shutil.rmtree(partial)  # left over from an interrupted move; the original is intact
        shutil.move(str(model), str(partial))  # a rename on the same disk
        os.rename(partial, final)
        moved.append(model.name)
    parts = []
    if moved:
        parts.append(f"Moved Whisper model(s) {', '.join(moved)} from {source} to {dest}.")
    if skipped:
        parts.append(f"Kept {', '.join(skipped)} in {source}: a copy is already in {dest}.")
    return " ".join(parts)
