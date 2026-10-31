"""AppleScript macOS Notes creator with injection safety and UTF-8 handling."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
import html
import logging
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Standard AppleScript to create a note by reading HTML from a UTF-8 temporary file.
# Using a file eliminates locale encoding issues, bypasses OS argument length limits,
# and ensures user text is never interpreted as script code.
APPLESCRIPT_CREATE_NOTE = """
on run argv
    set filePath to item 1 of argv
    set folderName to item 2 of argv
    set revealNote to (item 3 of argv is "true")
    
    set noteBody to (read POSIX file filePath as «class utf8»)
    
    tell application "Notes"
        set targetFolder to missing value
        if folderName is not "" then
            set matchingFolders to every folder whose name is folderName
            if (count of matchingFolders) > 0 then
                set targetFolder to item 1 of matchingFolders
            end if
        end if
        
        if targetFolder is not missing value then
            set newNote to make new note at targetFolder with properties {body:noteBody}
        else
            set newNote to make new note with properties {body:noteBody}
        end if
        
        if revealNote then
            show newNote
            activate
        end if
        
        set noteId to id of newNote
        set isCustomFolder to (targetFolder is not missing value)
        return noteId & "|" & isCustomFolder
    end tell
end run
"""


@dataclass(frozen=True)
class NoteResult:
    """Result of attempting to create a note."""
    success: bool
    note_id: Optional[str]
    title: str
    error: Optional[str] = None
    used_custom_folder: bool = False
    execution_time_sec: float = 0.0

    @property
    def message(self) -> str:
        if self.success:
            return f"Created note '{self.title}'"
        return self.error or "Failed to create note."


def format_note_html(title: str, body: str) -> str:
    """
    Format note title and body into standard HTML for the macOS Notes app.
    Escapes HTML entities and translates paragraphs and linebreaks safely.
    """
    escaped_title = html.escape(title.strip())
    escaped_body = html.escape(body.strip())

    paragraphs = [p.strip() for p in escaped_body.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [escaped_body]

    paragraphs_html = "".join(f"<p>{p.replace('\n', '<br>')}</p>" for p in paragraphs)
    return f"<h1>{escaped_title}</h1><br>{paragraphs_html}"


def generate_note_title(template: str, timestamp: Optional[datetime] = None) -> str:
    """Generate note title from template string with date and time placeholders."""
    now = timestamp or datetime.now()
    date_str = now.strftime("%d %b %Y")  # e.g. "30 Oct 2026"
    time_str = now.strftime("%H:%M")      # e.g. "20:30"
    date_iso = now.strftime("%Y-%m-%d")  # e.g. "2026-10-30"
    time_iso = now.strftime("%H:%M:%S")  # e.g. "20:30:15"
    return template.format(
        date=date_str,
        time=time_str,
        date_iso=date_iso,
        time_iso=time_iso,
    )


def save_note_failure_backup(
    text: str,
    title: str,
    error_message: str = "",
    backups_dir: Optional[Path] = None,
    *,
    error_msg: Optional[str] = None,
    backup_dir: Optional[Path] = None,
) -> Path:
    """
    Save transcribed note text to a permanent .txt backup file upon save failure.
    Stored in note_backups/ (never pruned).
    """
    err = error_msg if error_msg is not None else error_message
    target_dir = backup_dir if backup_dir is not None else (backups_dir or Path("note_backups"))
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_path = target_dir / f"note_backup_{timestamp_str}.txt"
    content = (
        f"Title: {title}\n"
        f"Date:  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Error: {err}\n"
        f"--------------------------------------------------\n"
        f"{text}\n"
    )
    file_path.write_text(content, encoding="utf-8")
    logger.info("Saved note failure backup to %s", file_path)
    return file_path


class BaseNotesWriter(ABC):
    """Abstract interface for macOS Notes creation."""

    @abstractmethod
    def create_note(
        self,
        title: str,
        body: str = "",
        folder: str = "",
        reveal: bool = False,
        body_html: Optional[str] = None,
    ) -> NoteResult:
        """Create a new note in macOS Notes."""
        pass


class AppleScriptNotesWriter(BaseNotesWriter):
    """Production implementation using osascript on a background worker thread."""

    def __init__(self, timeout_sec: float = 15.0):
        self.timeout_sec = timeout_sec

    def create_note(
        self,
        title: str,
        body: str = "",
        folder: str = "",
        reveal: bool = False,
        body_html: Optional[str] = None,
    ) -> NoteResult:
        t0 = time.perf_counter()
        html_content = body_html if body_html is not None else format_note_html(title, body)

        # Write HTML body to a temporary UTF-8 file
        tmp_file = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False)
        try:
            tmp_file.write(html_content)
            tmp_file.close()
            tmp_path = tmp_file.name

            reveal_str = "true" if reveal else "false"
            cmd = ["osascript", "-e", APPLESCRIPT_CREATE_NOTE, tmp_path, folder, reveal_str]

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
                check=False,
            )
            elapsed = time.perf_counter() - t0

            if proc.returncode == 0:
                raw_out = proc.stdout.strip()
                parts = raw_out.split("|")
                note_id = parts[0] if parts else raw_out
                used_custom = parts[1].lower() == "true" if len(parts) > 1 else False

                if folder and not used_custom:
                    logger.warning("Folder '%s' not found; note was saved in Notes default folder.", folder)

                return NoteResult(
                    success=True,
                    note_id=note_id,
                    title=title,
                    used_custom_folder=used_custom,
                    execution_time_sec=elapsed,
                )
            else:
                stderr_text = proc.stderr.strip()
                error_msg = self._map_error_message(stderr_text)
                logger.error("Failed to create note in Notes app: %s (stderr: %s)", error_msg, stderr_text)
                return NoteResult(
                    success=False,
                    note_id=None,
                    title=title,
                    error=error_msg,
                    execution_time_sec=elapsed,
                )
        except subprocess.TimeoutExpired:
            elapsed = time.perf_counter() - t0
            err_msg = f"Notes app did not respond within {self.timeout_sec:.0f} seconds."
            logger.error(err_msg)
            return NoteResult(
                success=False,
                note_id=None,
                title=title,
                error=err_msg,
                execution_time_sec=elapsed,
            )
        except Exception as e:
            elapsed = time.perf_counter() - t0
            logger.error("Unexpected error writing note: %s", e, exc_info=True)
            return NoteResult(
                success=False,
                note_id=None,
                title=title,
                error=str(e),
                execution_time_sec=elapsed,
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    @staticmethod
    def _map_error_message(stderr_text: str) -> str:
        """Map raw AppleScript error codes to clear, actionable user messages."""
        if "-1743" in stderr_text:
            return (
                "Automation permission denied. Please enable permission for Voice Assistant "
                "in System Settings > Privacy & Security > Automation (under your Terminal or Python app)."
            )
        if "-1728" in stderr_text or "Can’t get application" in stderr_text:
            return "macOS Notes app is not installed or not accessible."
        if "-600" in stderr_text or "-609" in stderr_text:
            return "Notes app connection failed; app may be hung or launching."
        return stderr_text or "Unknown AppleScript error."
