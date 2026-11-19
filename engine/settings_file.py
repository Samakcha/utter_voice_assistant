"""
Save changed settings into the user's config.toml without losing anything else in it:
every explanation (# comment), every setting the window doesn't show, and the order stay as
they are; only the changed values are rewritten in place. The Settings window
(interface/settings_window.py) uses this; people can still edit the file by hand.

A change is only saved if the whole file still reads correctly afterwards (load_config),
and the file is replaced in one step, so a crash can never leave half a file behind.
"""

import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import tomllib
from typing import Dict, List, Optional, Tuple

from .config import AppConfig, load_config

Value = object  # bool, int, float, str or a list of str

_HEADER = re.compile(r"^\s*\[\s*([A-Za-z0-9_.\-\"' ]+?)\s*\]\s*(#.*)?$")
_KEY = re.compile(r"^\s*(\"[^\"]*\"|'[^']*'|[A-Za-z0-9_\-]+)\s*=")
_BARE_KEY = re.compile(r"^[A-Za-z0-9_\-]+$")


class SettingsSaveError(Exception):
    """The change could not be saved (the file is left as it was)."""


def toml_value(value: Value) -> str:
    """How a value is written in TOML."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        text = repr(round(value, 4))
        return text if ("." in text or "e" in text) else text + ".0"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)  # a TOML basic string
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    raise TypeError(f"cannot write {type(value).__name__} to the settings file")


def toml_key(key: str) -> str:
    return key if _BARE_KEY.match(key) else json.dumps(key, ensure_ascii=False)


def _key_name(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return json.loads(raw) if raw[0] == '"' else raw[1:-1]
    return raw


def _section_name(raw: str) -> str:
    return ".".join(_key_name(part) for part in raw.split("."))


def _scan_value(text: str) -> Tuple[int, bool]:
    """Where the value in `text` (what follows '=') ends, and whether it is complete
    (an array can go on over several lines). Comments after it are not part of it."""
    depth, quote, i = 0, "", 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        elif ch == "#":
            return i, depth <= 0
        i += 1
    return len(text), depth <= 0 and not quote


class _Entry:
    """One `key = value` in the file: its lines and where the value sits on them."""

    def __init__(self, first: int, last: int, key: str):
        self.first, self.last, self.key = first, last, key


def _parse(lines: List[str]):
    """{section: {key: _Entry}}, {section: header line}, {section: last line of section}."""
    entries: Dict[str, Dict[str, _Entry]] = {"": {}}
    headers: Dict[str, int] = {"": -1}
    section_end: Dict[str, int] = {"": -1}
    section = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        header = _HEADER.match(line)
        if header and not line.lstrip().startswith("[["):
            section = _section_name(header.group(1))
            entries.setdefault(section, {})
            headers[section] = i
            section_end[section] = i
            i += 1
            continue
        key = _KEY.match(line)
        if key:
            start = i
            rest = line[key.end():]
            _, complete = _scan_value(rest)
            while not complete and i + 1 < len(lines):
                i += 1
                rest += "\n" + lines[i]
                _, complete = _scan_value(rest)
            entries[section][_key_name(key.group(1))] = _Entry(start, i, _key_name(key.group(1)))
            section_end[section] = i
        i += 1
    return entries, headers, section_end


def _replace_value(lines: List[str], entry: _Entry, value: Value) -> List[str]:
    block = "\n".join(lines[entry.first:entry.last + 1])
    key = _KEY.match(block)
    rest = block[key.end():]
    end, _ = _scan_value(rest)
    comment = rest[end:]
    lead = rest[:len(rest) - len(rest.lstrip(" \t"))] or " "
    new = block[:key.end()] + lead + toml_value(value) + ("  " + comment.strip() if comment.strip() else "")
    return new.split("\n")


def update_settings_text(text: str, changes: Dict[str, Dict[str, Value]],
                         tables: Optional[Dict[str, Dict[str, str]]] = None) -> str:
    """
    changes: {"hotkeys": {"command_mode": "<ctrl>+<alt>+m"}, ...}  values written in place
             (a setting that isn't in the file yet is added at the end of its section;
             a section that isn't there is added at the end of the file).
    tables:  {"apps.aliases": {"browser": "Safari"}}  the whole table's entries replaced
             (its comments kept).
    """
    lines = text.split("\n")
    trailing_newline = text.endswith("\n")
    if trailing_newline:
        lines = lines[:-1]

    for section, values in (changes or {}).items():
        for key, value in values.items():
            entries, headers, section_end = _parse(lines)
            entry = entries.get(section, {}).get(key)
            if entry is not None:
                lines[entry.first:entry.last + 1] = _replace_value(lines, entry, value)
            elif section in headers:
                at = section_end[section] + 1
                lines.insert(at, f"{toml_key(key)} = {toml_value(value)}")
            else:
                lines += ["", f"[{section}]", f"{toml_key(key)} = {toml_value(value)}"]

    for section, mapping in (tables or {}).items():
        entries, headers, section_end = _parse(lines)
        old = sorted(entries.get(section, {}).values(), key=lambda e: e.first, reverse=True)
        insert_at = old[-1].first if old else None
        for entry in old:
            del lines[entry.first:entry.last + 1]
        new_lines = [f"{toml_key(k)} = {toml_value(v)}" for k, v in mapping.items()]
        if section not in headers:
            if new_lines:
                lines += ["", f"[{section}]"] + new_lines
            continue
        if insert_at is None:
            # after the header and its comment lines
            entries, headers, section_end = _parse(lines)
            insert_at = section_end[section] + 1
            while (insert_at < len(lines) and lines[insert_at].lstrip().startswith("#")):
                insert_at += 1
        lines[insert_at:insert_at] = new_lines

    result = "\n".join(lines)
    return result + "\n" if trailing_newline else result


def save_settings(path: Path, changes: Dict[str, Dict[str, Value]],
                  tables: Optional[Dict[str, Dict[str, str]]] = None,
                  default_config: Optional[Path] = None) -> AppConfig:
    """Write the changes into `path` (created from `default_config` if missing) and return
    the settings as they now are. Raises SettingsSaveError and leaves the file untouched
    if the result would not load."""
    path = Path(path)
    if not path.is_file():
        if default_config is None or not Path(default_config).is_file():
            raise SettingsSaveError(f"No settings file at {path}.")
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(default_config, path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise SettingsSaveError(f"Could not read the settings file: {e}") from e
    new_text = update_settings_text(text, changes, tables)
    try:
        tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as e:
        raise SettingsSaveError(f"The settings file could not be updated ({e}). "
                                "Open it from Settings → Advanced to check it.") from e
    fd, tmp_name = tempfile.mkstemp(prefix=".config-", suffix=".toml", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_text)
        config = load_config(tmp)
        os.replace(tmp, path)
    except SettingsSaveError:
        raise
    except Exception as e:
        raise SettingsSaveError(f"The new setting could not be saved: {e}") from e
    finally:
        if tmp.exists():
            tmp.unlink()
    return config
