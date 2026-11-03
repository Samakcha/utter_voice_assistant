"""
WhatsApp Desktop Control & Automation Module.
Provides high-level actions for:
- open_whatsapp: Brings net.whatsapp.WhatsApp forward via system launcher, verifies dual frontmost.
- whatsapp_search: Clears search (AXCancel), focuses search (AXPress/Cmd+F), pastes name (Cmd+V),
  restores clipboard.

STRICT SAFETY & PRIVACY INVARIANTS:
1. DUAL FRONTMOST GUARD:
   whatsapp_search acts ONLY if BOTH NSWorkspace and lsappinfo report net.whatsapp.WhatsApp.
   Checked immediately before actions and before every key event.
   If not frontmost or disagreeing: returns error, does 0 AX actions, 0 key events.
2. NEVER-SEND KEYCODE WHITELIST:
   Allowed keycodes: strictly {3 (Cmd+F), 9 (Cmd+V)}.
   Return/Enter (36), Delete, Esc, Cmd+A, arrow keys are forbidden.
3. AX ACTION LIMIT:
   AXPress and AXCancel are performed ONLY on the search field element.
4. ZERO CONTENT ATTRIBUTES:
   Never queries values, descriptions, help, labels, or content text.
5. NO NESTED RUN LOOPS & NON-BLOCKING:
   Actions run on background worker threads; main thread is never blocked with sleeps or nested loops.
   AX calls and CGEvent posting dispatch cleanly.
"""

from dataclasses import dataclass
import logging
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

import AppKit
import Quartz

from .clipboard import (
    get_general_pasteboard,
    restore_pasteboard_snapshot,
    take_pasteboard_snapshot,
    wait_for_modifiers_released,
    write_dictation_to_pasteboard,
)
from .whatsapp_ax import (
    DEFAULT_AX_TIMEOUT_SEC,
    WHATSAPP_BUNDLE_ID,
    check_is_focused,
    enable_catalyst_accessibility,
    find_search_field,
    get_main_window,
    set_ax_timeout,
)

logger = logging.getLogger(__name__)

# Strict keycode whitelist: 3 = 'F' (Cmd+F), 9 = 'V' (Cmd+V)
ALLOWED_WHATSAPP_KEYCODES = {3, 9}


def run_on_main_thread(fn: Callable[..., Any], *args: Any) -> None:
    """Schedule callable to execute on macOS main UI thread."""
    if AppKit.NSThread.isMainThread():
        fn(*args)
    else:
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(lambda: fn(*args))


def get_frontmost_bundle_nsworkspace() -> str:
    """Query frontmost application bundle ID via Cocoa NSWorkspace."""
    try:
        ws = AppKit.NSWorkspace.sharedWorkspace()
        front = ws.frontmostApplication()
        if front and front.bundleIdentifier():
            return str(front.bundleIdentifier())
    except Exception as e:
        logger.debug("NSWorkspace frontmost query failed: %s", e)
    return "None"


def get_frontmost_bundle_lsappinfo() -> str:
    """
    Query frontmost application bundle ID via LaunchServices CLI (`lsappinfo`).
    Queries launchservicesd directly, completely independent of Cocoa event loop.
    """
    try:
        asn = subprocess.check_output(
            ["lsappinfo", "front"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if not asn:
            return "None"
        info = subprocess.check_output(
            ["lsappinfo", "info", "-only", "bundleid", asn],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        m = re.search(r'bundleID="([^"]+)"', info)
        if m:
            return m.group(1)
        for line in info.splitlines():
            line = line.strip()
            if "bundleID=" in line:
                return line.split("bundleID=", 1)[1].strip().strip('"')
    except Exception as e:
        logger.debug("lsappinfo frontmost query failed: %s", e)
    return "None"


def check_whatsapp_frontmost_dual() -> Tuple[bool, str, str]:
    """
    Verify whether WhatsApp is frontmost according to BOTH NSWorkspace and lsappinfo.
    Returns: (both_agree_whatsapp, nsworkspace_bundle, lsappinfo_bundle)
    """
    b_nsws = get_frontmost_bundle_nsworkspace()
    b_ls = get_frontmost_bundle_lsappinfo()
    both_agree = (b_nsws == WHATSAPP_BUNDLE_ID and b_ls == WHATSAPP_BUNDLE_ID)

    if b_nsws != b_ls:
        logger.warning(
            "[WhatsApp Dual Check] Frontmost checks DISAGREE: NSWorkspace='%s', lsappinfo='%s' (both_whatsapp=%s)",
            b_nsws,
            b_ls,
            both_agree,
        )
    else:
        logger.info(
            "[WhatsApp Dual Check] Frontmost checks AGREE: NSWorkspace='%s', lsappinfo='%s' (both_whatsapp=%s)",
            b_nsws,
            b_ls,
            both_agree,
        )

    return both_agree, b_nsws, b_ls


def post_whatsapp_key(
    keycode: int,
    modifiers: int = 0,
    dual_check_fn: Optional[Callable[[], Tuple[bool, str, str]]] = None,
) -> None:
    """
    Post a keyboard event strictly constrained to whitelist {3, 9}.
    CRITICAL SAFETY RULE:
    Post key ONLY if BOTH independent checks report net.whatsapp.WhatsApp.
    If they disagree or either is not WhatsApp, aborts immediately.
    """
    assert keycode in ALLOWED_WHATSAPP_KEYCODES, (
        f"NEVER-SEND VIOLATION: Keycode {keycode} not in whitelist {ALLOWED_WHATSAPP_KEYCODES}"
    )

    check_fn = dual_check_fn or check_whatsapp_frontmost_dual
    both_agree, b_nsws, b_ls = check_fn()

    if not both_agree:
        err_msg = (
            f"SAFETY ABORT: Key {keycode} blocked! "
            f"Frontmost checks: NSWorkspace='{b_nsws}', lsappinfo='{b_ls}'. Both must be '{WHATSAPP_BUNDLE_ID}'."
        )
        logger.error("[WhatsApp Safety] %s", err_msg)
        raise RuntimeError(err_msg)

    def _post():
        down = Quartz.CGEventCreateKeyboardEvent(None, keycode, True)
        up = Quartz.CGEventCreateKeyboardEvent(None, keycode, False)
        if modifiers:
            Quartz.CGEventSetFlags(down, modifiers)
            Quartz.CGEventSetFlags(up, modifiers)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        time.sleep(0.02)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)

    run_on_main_thread(_post)


def get_whatsapp_running_app() -> Optional[Any]:
    """Retrieve NSRunningApplication instance for WhatsApp desktop."""
    try:
        ws = AppKit.NSWorkspace.sharedWorkspace()
        for a in ws.runningApplications():
            if a.bundleIdentifier() == WHATSAPP_BUNDLE_ID:
                return a
    except Exception as e:
        logger.debug("Failed querying running applications: %s", e)
    return None


@dataclass
class WhatsAppActionResult:
    success: bool
    message: str
    error: Optional[str] = None
    execution_time_sec: float = 0.0
    nsworkspace_bundle: str = "None"
    lsappinfo_bundle: str = "None"
    keys_posted: int = 0


def open_whatsapp_async(
    callback: Callable[[WhatsAppActionResult], None],
    timeout_sec: float = 3.0,
) -> None:
    """
    Brings WhatsApp forward using system launcher (`open -b net.whatsapp.WhatsApp`),
    and verifies it is frontmost via dual checks. Runs on a background thread.
    """
    def _worker():
        t0 = time.perf_counter()
        logger.info("[WhatsApp Action] Launching net.whatsapp.WhatsApp via system launcher...")

        try:
            subprocess.run(["open", "-b", WHATSAPP_BUNDLE_ID], capture_output=True, check=False)
        except Exception as e:
            res = WhatsAppActionResult(
                success=False,
                message=f"Failed to launch WhatsApp: {e}",
                error=str(e),
                execution_time_sec=time.perf_counter() - t0,
            )
            run_on_main_thread(lambda: callback(res))
            return

        # Poll dual frontmost checks up to timeout_sec on background thread
        deadline = time.time() + timeout_sec
        last_nsws = "None"
        last_ls = "None"
        success = False

        while time.time() < deadline:
            both_front, last_nsws, last_ls = check_whatsapp_frontmost_dual()
            if both_front:
                success = True
                break
            time.sleep(0.1)

        t_total = time.perf_counter() - t0
        if success:
            res = WhatsAppActionResult(
                success=True,
                message="WhatsApp ready",
                execution_time_sec=t_total,
                nsworkspace_bundle=last_nsws,
                lsappinfo_bundle=last_ls,
            )
        else:
            disagree_note = (
                f"Checks disagreed: NSWorkspace='{last_nsws}', lsappinfo='{last_ls}'"
                if last_nsws != last_ls
                else f"Both reported '{last_nsws}'"
            )
            res = WhatsAppActionResult(
                success=False,
                message=f"Couldn't open WhatsApp: {disagree_note}",
                error=disagree_note,
                execution_time_sec=t_total,
                nsworkspace_bundle=last_nsws,
                lsappinfo_bundle=last_ls,
            )

        run_on_main_thread(lambda: callback(res))

    threading.Thread(target=_worker, name="wa-open-worker", daemon=True).start()


def search_whatsapp_async(
    name: str,
    callback: Callable[[WhatsAppActionResult], None],
    clipboard_restore_delay_ms: int = 400,
    modifier_release_timeout_ms: int = 500,
    mock_dual_check: Optional[Callable[[], Tuple[bool, str, str]]] = None,
) -> None:
    """
    Execute WhatsApp search sequence for <name>:
    1. Verify dual frontmost checks (NSWorkspace and lsappinfo both net.whatsapp.WhatsApp).
    2. AXCancel on search field to clear old search.
    3. AXPress on search field.
    4. Verify focused element subrole == 'AXSearchField'. If not, try Cmd+F once.
    5. Paste name with transient clipboard save/restore, wait for modifiers, post Cmd+V.
    6. Restore clipboard in finally block.
    Runs on background worker thread without blocking main UI thread.
    """
    def _worker():
        t0 = time.perf_counter()
        keys_posted = 0

        # Guard: Check dual frontmost immediately before any action.
        # whatsapp_search must NOT launch or activate WhatsApp (zero AX actions, zero key events).
        # If not frontmost: return error immediately. Only open_whatsapp may run `open -b`.
        check_fn = mock_dual_check or check_whatsapp_frontmost_dual
        both_front, b_nsws, b_ls = check_fn()
        if not both_front:
            err_msg = "WhatsApp isn't in front. Say 'open WhatsApp' first."
            logger.info(
                "[WhatsApp Guard BLOCKED] %s (NSWorkspace='%s', lsappinfo='%s')",
                err_msg,
                b_nsws,
                b_ls,
            )
            res = WhatsAppActionResult(
                success=False,
                message=err_msg,
                error=err_msg,
                execution_time_sec=time.perf_counter() - t0,
                nsworkspace_bundle=b_nsws,
                lsappinfo_bundle=b_ls,
            )
            run_on_main_thread(lambda: callback(res))
            return

        # Obtain WhatsApp running application
        wa_app = get_whatsapp_running_app()

        if not wa_app:
            err_msg = "WhatsApp is not running."
            res = WhatsAppActionResult(
                success=False,
                message=err_msg,
                error=err_msg,
                execution_time_sec=time.perf_counter() - t0,
                nsworkspace_bundle=b_nsws,
                lsappinfo_bundle=b_ls,
            )
            run_on_main_thread(lambda: callback(res))
            return

        try:
            import ApplicationServices
            app_el = ApplicationServices.AXUIElementCreateApplication(wa_app.processIdentifier())
            set_ax_timeout(app_el, DEFAULT_AX_TIMEOUT_SEC)
        except Exception as e:
            res = WhatsAppActionResult(
                success=False,
                message="Couldn't reach WhatsApp",
                error=str(e),
                execution_time_sec=time.perf_counter() - t0,
                nsworkspace_bundle=b_nsws,
                lsappinfo_bundle=b_ls,
            )
            run_on_main_thread(lambda: callback(res))
            return

        main_win = get_main_window(app_el)
        if not main_win:
            res = WhatsAppActionResult(
                success=False,
                message="WhatsApp's UI isn't readable. Try restarting WhatsApp.",
                error="Main window not found",
                execution_time_sec=time.perf_counter() - t0,
                nsworkspace_bundle=b_nsws,
                lsappinfo_bundle=b_ls,
            )
            run_on_main_thread(lambda: callback(res))
            return

        search_field, _ = find_search_field(main_win, max_depth=15)
        if not search_field:
            # Fallback: enable Mac Catalyst assistive flags and retry once
            enable_catalyst_accessibility(app_el)
            time.sleep(0.5)
            search_field, _ = find_search_field(main_win, max_depth=15)

        if not search_field:
            res = WhatsAppActionResult(
                success=False,
                message="WhatsApp's UI isn't readable. Try restarting WhatsApp.",
                error="Search field not found in window tree",
                execution_time_sec=time.perf_counter() - t0,
                nsworkspace_bundle=b_nsws,
                lsappinfo_bundle=b_ls,
            )
            run_on_main_thread(lambda: callback(res))
            return

        set_ax_timeout(search_field, DEFAULT_AX_TIMEOUT_SEC)

        # Step 1: AXCancel on search field to clear prior search
        try:
            ApplicationServices.AXUIElementPerformAction(search_field, "AXCancel")
            time.sleep(0.15)
        except Exception as e:
            logger.debug("AXCancel on search field: %s", e)

        # Step 2: AXPress on search field to focus it
        try:
            ApplicationServices.AXUIElementPerformAction(search_field, "AXPress")
            time.sleep(0.15)
        except Exception as e:
            logger.debug("AXPress on search field: %s", e)

        # Step 3: Verify focused element has subrole AXSearchField
        is_focused, f_role, f_subrole = check_is_focused(search_field, app_el)
        if f_subrole != "AXSearchField":
            logger.debug("AXPress did not focus search field (focused subrole='%s'). Trying Cmd+F fallback...", f_subrole)
            try:
                post_whatsapp_key(3, Quartz.kCGEventFlagMaskCommand, dual_check_fn=mock_dual_check)
                keys_posted += 1
                time.sleep(0.2)
                is_focused, f_role, f_subrole = check_is_focused(search_field, app_el)
            except Exception as e:
                logger.error("Cmd+F fallback failed: %s", e)

        if f_subrole != "AXSearchField":
            err_msg = "Couldn't focus WhatsApp search"
            logger.warning("[WhatsApp Search] Focus verification failed (role='%s', subrole='%s')", f_role, f_subrole)
            res = WhatsAppActionResult(
                success=False,
                message=err_msg,
                error=f"Focus verification failed (subrole={f_subrole})",
                execution_time_sec=time.perf_counter() - t0,
                nsworkspace_bundle=b_nsws,
                lsappinfo_bundle=b_ls,
                keys_posted=keys_posted,
            )
            run_on_main_thread(lambda: callback(res))
            return

        # Step 4: Paste <name> with transient markers, wait for modifier keys, post Cmd+V
        pb = get_general_pasteboard()
        snapshot = take_pasteboard_snapshot(pb)
        expected_change_count = write_dictation_to_pasteboard(name, pb)

        try:
            # Wait for Control and Option keys to be released before posting Cmd+V
            wait_for_modifiers_released(timeout_ms=modifier_release_timeout_ms)

            # Re-verify dual frontmost right before posting Cmd+V
            post_whatsapp_key(9, Quartz.kCGEventFlagMaskCommand, dual_check_fn=mock_dual_check)
            keys_posted += 1
        finally:
            # Non-blocking delayed restoration: only restore if changeCount matches expected_change_count
            delay_sec = max(0.05, float(clipboard_restore_delay_ms) / 1000.0)

            def _delayed_restore():
                restore_pasteboard_snapshot(snapshot, expected_change_count, pb)

            timer = threading.Timer(delay_sec, _delayed_restore)
            timer.daemon = True
            timer.start()

        t_total = time.perf_counter() - t0
        res = WhatsAppActionResult(
            success=True,
            message="Pick the chat, click the message box, then press Control+Option+D to dictate. Press Enter to send.",
            execution_time_sec=t_total,
            nsworkspace_bundle=b_nsws,
            lsappinfo_bundle=b_ls,
            keys_posted=keys_posted,
        )
        run_on_main_thread(lambda: callback(res))

    threading.Thread(target=_worker, name="wa-search-worker", daemon=True).start()
