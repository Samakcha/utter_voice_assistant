"""
Installed-app inventory and spoken-name matching for "open <app>" commands.
Pure engine component with NO UI code.

The inventory is built by listing the standard Applications folders and reading each
app's Info.plist (no Spotlight, no Launch Services, nothing sent anywhere). A spoken name
is matched in strict levels; the first level with any match decides:

  1. alias      - your own aliases from config.toml, then a few built-in ones
                  ("chrome" -> Google Chrome), used only if the target app is installed
  2. exact      - the app's name ("calculator" -> Calculator)
  3. compact    - the same letters without spaces ("x code" -> Xcode, "face time" -> FaceTime)
  4. partial    - every spoken word is a whole word of exactly one app ("chrome" -> Google Chrome)
  5. fuzzy      - one small spelling slip in a longer name ("spotifi" -> Spotify), only
                  when exactly one app is that close

One match opens that app. Two or more at the same level is ambiguous and opens nothing;
no match opens nothing. Names that only differ in generic words ("app", "pro") never match.
"""

from dataclasses import dataclass
import logging
from pathlib import Path
import plistlib
import re
import threading
import time
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

DEFAULT_APP_DIRS: Tuple[Path, ...] = (
    Path("/Applications"),
    Path("/Applications/Utilities"),
    Path("/System/Applications"),
    Path("/System/Applications/Utilities"),
    Path("~/Applications").expanduser(),
)
# Apps that live outside the Applications folders but people open by name
EXTRA_APPS: Tuple[Path, ...] = (Path("/System/Library/CoreServices/Finder.app"),)

# Built-in aliases (spoken -> app name). Used only when the target app is installed.
BUILTIN_ALIASES: Dict[str, str] = {
    "chrome": "Google Chrome",
    "google chrome": "Google Chrome",
    "vs code": "Visual Studio Code",
    "vscode": "Visual Studio Code",
    "settings": "System Settings",
    "system preferences": "System Settings",
    "preferences": "System Settings",
    "quicktime": "QuickTime Player",
    "itunes": "Music",
    "word": "Microsoft Word",
    "excel": "Microsoft Excel",
    "powerpoint": "Microsoft PowerPoint",
    "outlook": "Microsoft Outlook",
    "teams": "Microsoft Teams",
}

# Words that say nothing about which app is meant; a partial match needs more than these
_GENERIC_WORDS = frozenset({
    "app", "apps", "application", "the", "my", "a", "an", "new", "pro", "player", "utility",
    "mac", "macos", "os", "apple", "for", "of", "and", "to", "lite", "free", "x",
})

LEVEL_ALIAS = "alias"
LEVEL_EXACT = "exact"
LEVEL_COMPACT = "compact"
LEVEL_PARTIAL = "partial"
LEVEL_FUZZY = "fuzzy"
STRONG_LEVELS = frozenset({LEVEL_ALIAS, LEVEL_EXACT, LEVEL_COMPACT})

STATUS_FOUND = "found"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_UNKNOWN = "unknown"


@dataclass(frozen=True)
class AppEntry:
    """One installed app."""
    name: str               # what Finder shows, e.g. "Visual Studio Code"
    path: Path              # the .app bundle on disk
    bundle_id: str = ""
    other_names: Tuple[str, ...] = ()  # display/bundle names from Info.plist, if different


@dataclass(frozen=True)
class AppResolution:
    status: str                                  # found / ambiguous / unknown
    spoken: str                                  # the name as heard (cleaned)
    app: Optional[AppEntry] = None
    level: str = ""
    candidates: Tuple[AppEntry, ...] = ()

    @property
    def found(self) -> bool:
        return self.status == STATUS_FOUND and self.app is not None


def normalize_app_name(text: str) -> str:
    """Lowercase, '&' as 'and', no '.app', punctuation as spaces, single spaces."""
    text = (text or "").lower().replace("&", " and ").replace("’", "'")
    text = re.sub(r"\.app\b", "", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def clean_spoken_app_name(text: str) -> str:
    """Normalized spoken name without a leading 'the'/'my' or a trailing 'app'/'application'."""
    words = normalize_app_name(text).split()
    while words and words[0] in ("the", "my"):
        words = words[1:]
    while words and words[-1] in ("app", "application", "apps"):
        words = words[:-1]
    return " ".join(words)


def _compact(text: str) -> str:
    return normalize_app_name(text).replace(" ", "")


def _edit_distance(a: str, b: str, limit: int) -> int:
    """Levenshtein distance, stopping early once it is certainly above limit."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cur[j] = min(prev[j - 1] + (ca != cb), prev[j] + 1, cur[j - 1] + 1)
        if min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def _fuzzy_limit(length: int) -> int:
    if length < 5:
        return 0
    return 1 if length < 9 else 2


# ---- Scanning ----------------------------------------------------------------------------

def _read_info_plist(app_path: Path) -> dict:
    try:
        with open(app_path / "Contents" / "Info.plist", "rb") as f:
            data = plistlib.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _app_entry(app_path: Path) -> Optional[AppEntry]:
    info = _read_info_plist(app_path)
    if info.get("LSBackgroundOnly") in (True, "1", 1):
        return None
    name = app_path.name[: -len(".app")]
    if not name or name.startswith("."):
        return None
    others = []
    for key in ("CFBundleDisplayName", "CFBundleName"):
        value = info.get(key)
        if isinstance(value, str):
            value = value.strip()
            if len(value) >= 2 and normalize_app_name(value) != normalize_app_name(name) and value not in others:
                others.append(value)
    return AppEntry(
        name=name,
        path=app_path,
        bundle_id=str(info.get("CFBundleIdentifier", "") or ""),
        other_names=tuple(others),
    )


def _app_bundles(folder: Path) -> Iterable[Path]:
    """The .app bundles in a folder and in its plain subfolders (one level, e.g. 'Python 3.12')."""
    try:
        entries = sorted(folder.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.name.startswith("."):
            continue
        if entry.suffix == ".app":
            if entry.is_dir():
                yield entry
        elif entry.is_dir() and not entry.is_symlink():
            try:
                for sub in sorted(entry.iterdir()):
                    if sub.suffix == ".app" and sub.is_dir() and not sub.name.startswith("."):
                        yield sub
            except OSError:
                continue


def scan_applications(
    folders: Sequence[Path] = DEFAULT_APP_DIRS, extra_apps: Sequence[Path] = EXTRA_APPS
) -> List[AppEntry]:
    """All launchable apps in the given folders, one entry per app (first folder wins)."""
    apps: List[AppEntry] = []
    seen_paths, seen_ids = set(), set()
    candidates: List[Path] = []
    for folder in folders:
        candidates.extend(_app_bundles(folder))
    candidates.extend(p for p in extra_apps if p.is_dir())
    for path in candidates:
        try:
            real = path.resolve()
        except OSError:
            real = path
        if real in seen_paths:
            continue
        seen_paths.add(real)
        entry = _app_entry(path)
        if entry is None:
            continue
        if entry.bundle_id:
            if entry.bundle_id in seen_ids:
                continue
            seen_ids.add(entry.bundle_id)
        apps.append(entry)
    return apps


# ---- Matching ----------------------------------------------------------------------------

def _unique(apps: Iterable[AppEntry]) -> List[AppEntry]:
    out, seen = [], set()
    for app in apps:
        if app.path not in seen:
            seen.add(app.path)
            out.append(app)
    return out


def resolve_app_name(
    spoken: str, apps: Sequence[AppEntry], aliases: Optional[Dict[str, str]] = None
) -> AppResolution:
    """Match a spoken app name against the inventory (see the module docstring for the levels)."""
    name = clean_spoken_app_name(spoken)
    if not name:
        return AppResolution(status=STATUS_UNKNOWN, spoken=name)

    def decide(matches: List[AppEntry], level: str) -> Optional[AppResolution]:
        matches = _unique(matches)
        if len(matches) == 1:
            return AppResolution(status=STATUS_FOUND, spoken=name, app=matches[0], level=level)
        if len(matches) > 1:
            ordered = tuple(sorted(matches, key=lambda a: a.name.lower())[:4])
            return AppResolution(status=STATUS_AMBIGUOUS, spoken=name, level=level, candidates=ordered)
        return None

    def by_exact_name(target: str, use_other_names: bool) -> List[AppEntry]:
        t = normalize_app_name(target)
        if use_other_names:
            return [a for a in apps if any(normalize_app_name(o) == t for o in a.other_names)]
        return [a for a in apps if normalize_app_name(a.name) == t]

    # 1. Aliases: yours first, then built-in; only if the target is installed
    for table in (aliases or {}, BUILTIN_ALIASES):
        normalized = {_compact(k): v for k, v in table.items()}  # "quick time" = "quicktime"
        target = normalized.get(name.replace(" ", ""))
        if target:
            hits = by_exact_name(target, False) or by_exact_name(target, True)
            result = decide(hits, LEVEL_ALIAS)
            if result:
                return result

    # 2. Exact name (Finder name first, then Info.plist names)
    for use_other in (False, True):
        result = decide(by_exact_name(name, use_other), LEVEL_EXACT)
        if result:
            return result

    # 3. Compact: same letters, spaces ignored ("x code" = "Xcode")
    compact = name.replace(" ", "")
    for use_other in (False, True):
        hits = [
            a for a in apps
            if any(_compact(n) == compact for n in ((a.name,) if not use_other else a.other_names))
        ]
        result = decide(hits, LEVEL_COMPACT)
        if result:
            return result

    # 4. Partial: every spoken word is a whole word of the app name, in order
    spoken_words = name.split()
    if any(w not in _GENERIC_WORDS and len(w) >= 3 for w in spoken_words):
        def contains_in_order(app_name: str) -> bool:
            words = normalize_app_name(app_name).split()
            pos = 0
            for w in spoken_words:
                try:
                    pos = words.index(w, pos) + 1
                except ValueError:
                    return False
            return True

        hits = [a for a in apps if contains_in_order(a.name) or any(contains_in_order(o) for o in a.other_names)]
        result = decide(hits, LEVEL_PARTIAL)
        if result:
            return result

    # 5. Fuzzy: one small slip in a longer name, only if a single app is the closest
    limit = _fuzzy_limit(len(compact))
    if limit:
        scored: List[Tuple[int, AppEntry]] = []
        for a in apps:
            best = min(_edit_distance(compact, _compact(n), limit) for n in (a.name,) + a.other_names)
            if best <= limit:
                scored.append((best, a))
        if scored:
            top = min(d for d, _ in scored)
            result = decide([a for d, a in scored if d == top], LEVEL_FUZZY)
            if result:
                return result

    return AppResolution(status=STATUS_UNKNOWN, spoken=name)


class AppInventory:
    """
    Thread-safe list of installed apps, scanned on first use and again when a name is not
    found (so an app installed while the assistant runs is picked up).
    """

    RESCAN_MIN_INTERVAL_SEC = 5.0

    def __init__(
        self,
        aliases: Optional[Dict[str, str]] = None,
        scanner: Callable[[], List[AppEntry]] = scan_applications,
    ):
        self.aliases = dict(aliases or {})
        self._scanner = scanner
        self._lock = threading.Lock()
        self._apps: Optional[List[AppEntry]] = None
        self._scanned_at = 0.0

    def refresh(self) -> int:
        """Scan the Applications folders now. Returns the number of apps found."""
        t0 = time.perf_counter()
        try:
            apps = self._scanner()
        except Exception as e:
            logger.error("Could not list installed apps: %s", e, exc_info=True)
            apps = []
        with self._lock:
            self._apps = apps
            self._scanned_at = time.monotonic()
        logger.info("App list: %d apps found in %.2fs.", len(apps), time.perf_counter() - t0)
        return len(apps)

    @property
    def apps(self) -> List[AppEntry]:
        with self._lock:
            ready = self._apps is not None
        if not ready:
            self.refresh()
        with self._lock:
            return list(self._apps or [])

    def resolve(self, spoken: str) -> AppResolution:
        return resolve_app_name(spoken, self.apps, self.aliases)

    def resolve_or_refresh(self, spoken: str) -> AppResolution:
        """Resolve; if nothing matches, rescan once (not more than every few seconds) and retry."""
        result = self.resolve(spoken)
        if result.status != STATUS_UNKNOWN:
            return result
        with self._lock:
            stale = time.monotonic() - self._scanned_at >= self.RESCAN_MIN_INTERVAL_SEC
        if stale:
            self.refresh()
            result = self.resolve(spoken)
        return result

    def can_finish_early(self, spoken: str) -> bool:
        """
        True if the name surely names one app and the user can't still be saying a longer
        one: "open Calculator" may run at the first pause, "open Antigravity" waits in case
        "IDE" follows.
        """
        result = self.resolve(spoken)
        if not result.found or result.level not in STRONG_LEVELS:
            return False
        name = clean_spoken_app_name(spoken)
        for app in self.apps:
            for n in (app.name,) + app.other_names:
                if normalize_app_name(n).startswith(name + " "):
                    return False
        return True
