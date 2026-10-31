"""
Unit tests for the macOS Notes writer layer (engine/notes_writer.py).
Tests HTML formatting, UTF-8 safety, injection immunity, template rendering,
permission error handling, and failure backup preservation.
"""

from pathlib import Path
import subprocess
import pytest

from engine.notes_writer import (
    AppleScriptNotesWriter,
    format_note_html,
    generate_note_title,
    save_note_failure_backup,
)


def test_format_note_html_basic():
    title = "Voice note 2026-10-30"
    text = "Hello world.\nThis is a second line."
    html = format_note_html(title, text)
    assert "<h1>Voice note 2026-10-30</h1>" in html
    assert "<p>Hello world.<br>This is a second line.</p>" in html


def test_format_note_html_escaping():
    title = "Test & Check <1> >0 \"Quotes\""
    text = "Line with <b>HTML</b> & 'quotes' <tag>"
    html = format_note_html(title, text)
    # Title escaped
    assert "Test &amp; Check &lt;1&gt; &gt;0 &quot;Quotes&quot;" in html
    # Body escaped
    assert "Line with &lt;b&gt;HTML&lt;/b&gt; &amp; &#x27;quotes&#x27; &lt;tag&gt;" in html
    assert "<b>HTML</b>" not in html


def test_format_note_html_unicode_and_hindi():
    title = "Hindi Note: कार्य सूची"
    text = "नमस्ते दुनिया! 🚀\nकल सुबह 10 बजे मीटिंग है।"
    html = format_note_html(title, text)
    assert "कार्य सूची" in html
    assert "नमस्ते दुनिया! 🚀" in html
    assert "<br>कल सुबह 10 बजे मीटिंग है।" in html


def test_generate_note_title_custom_template():
    template = "Daily Log - {date_iso} at {time}"
    title = generate_note_title(template)
    assert title.startswith("Daily Log - 20")
    assert " at " in title


def test_generate_note_title_default():
    title = generate_note_title("Voice note {date} {time}")
    assert title.startswith("Voice note ")


def test_save_note_failure_backup(tmp_path: Path):
    backup_dir = tmp_path / "note_backups"
    title = "Emergency Note"
    text = "Crucial meeting minutes with Hindi: ध्यान रखें"
    error_msg = "macOS Notes Automation permission denied (-1743)"

    backup_path = save_note_failure_backup(text, title, error_msg, backup_dir)
    assert backup_path.exists()
    assert backup_path.parent == backup_dir
    assert backup_path.name.startswith("note_backup_")
    assert backup_path.suffix == ".txt"

    content = backup_path.read_text(encoding="utf-8")
    assert f"Title: {title}" in content
    assert f"Error: {error_msg}" in content
    assert text in content


def test_applescript_writer_success(monkeypatch):
    writer = AppleScriptNotesWriter(timeout_sec=5.0)

    def mock_run(cmd, *args, **kwargs):
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="note_id:x-coredata://123|false\n", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)
    res = writer.create_note(title="Test Note", body_html="<div>Test</div>")
    assert res.success is True
    assert res.note_id == "note_id:x-coredata://123"
    assert "Created note 'Test Note'" in res.message


def test_applescript_writer_permission_error_1743(monkeypatch):
    writer = AppleScriptNotesWriter(timeout_sec=5.0)

    def mock_run(cmd, *args, **kwargs):
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=1,
            stdout="",
            stderr="execution error: Not authorized to send Apple events to Notes. (-1743)\n",
        )

    monkeypatch.setattr(subprocess, "run", mock_run)
    res = writer.create_note(title="Test Note", body_html="<div>Test</div>")
    assert res.success is False
    assert "Automation permission denied" in res.error
    assert "System Settings" in res.error


def test_applescript_writer_timeout(monkeypatch):
    writer = AppleScriptNotesWriter(timeout_sec=1.0)

    def mock_run(cmd, *args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=1.0)

    monkeypatch.setattr(subprocess, "run", mock_run)
    res = writer.create_note(title="Slow Note", body_html="<div>Slow</div>")
    assert res.success is False
    assert "did not respond" in res.error
