"""
Who is signed in to Utter. The login (session tokens) is kept in the Keychain, so Utter
stays signed in across restarts and works offline; it is refreshed in the background when
online. Signing in is required before the hotkeys do anything (when accounts are set up).

A login lasts while Utter is used: each use of a hotkey restarts a 7-day clock. After 7 days
without using Utter, it asks to sign in again (with the password; no new code).
"""

import dataclasses
import logging
import threading
import time
from typing import Callable, Optional

from engine.account import AuthError, Session, SupabaseAuth
from .keychain import Keychain

logger = logging.getLogger(__name__)

REFRESH_EVERY_SEC = 6 * 3600
INACTIVE_DAYS = 7  # sign in again after this many days without using Utter
INACTIVE_LIMIT_SEC = INACTIVE_DAYS * 24 * 3600
SAVE_USE_EVERY_SEC = 3600  # "last used" is written to the Keychain at most once an hour
INACTIVE_NOTICE = f"You haven't used Utter for {INACTIVE_DAYS} days. Please sign in again."


class AccountManager:
    def __init__(self, auth: SupabaseAuth, keychain=None, required: bool = True,
                 clock: Callable[[], float] = time.time):
        self.auth = auth
        self.keychain = keychain if keychain is not None else Keychain()
        self.required = required
        self._clock = clock
        self.session: Optional[Session] = None
        self.notice = ""  # why the user was signed out (shown in the sign-in window)
        self.last_email = ""  # pre-filled in the sign-in window after a sign-out
        self.on_change: Callable[[], None] = lambda: None  # signed in or out (any thread)
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.auth.configured

    @property
    def signed_in(self) -> bool:
        return self.session is not None

    @property
    def locked(self) -> bool:
        """True while Utter must not be used: accounts are on and nobody is signed in."""
        return self.enabled and self.required and not self.signed_in

    @property
    def email(self) -> str:
        return self.session.email if self.session else ""

    def load(self) -> bool:
        """Read the saved login (at start). True when someone is signed in."""
        if not self.enabled:
            return False
        text = self.keychain.read()
        self.session = Session.from_json(text) if text else None
        if text and self.session is None:
            logger.warning("The saved login could not be read; please sign in again.")
            self.keychain.delete()
        if self.session is not None and not self.session.last_used:
            # Saved before the 7-day rule existed: count from now
            self.session = dataclasses.replace(self.session, last_used=self._clock())
            self.keychain.write(self.session.to_json())
        self.check_inactive()
        logger.info("Account: %s", "signed in" if self.session else "not signed in")
        return self.session is not None

    def check_inactive(self) -> bool:
        """Sign out when Utter was not used for 7 days. True if that just happened."""
        session = self.session
        if session is None or self._clock() - session.last_used < INACTIVE_LIMIT_SEC:
            return False
        logger.info("Account: not used for %d days; signing in is needed again.", INACTIVE_DAYS)
        self.sign_out(notice=INACTIVE_NOTICE)
        return True

    def mark_used(self) -> None:
        """A hotkey was used: the 7-day clock starts again (saved at most once an hour)."""
        with self._lock:
            session = self.session
            now = self._clock()
            if session is None or now - session.last_used < SAVE_USE_EVERY_SEC:
                return
            self.session = dataclasses.replace(session, last_used=now)
            self.keychain.write(self.session.to_json())

    def signed_in_with(self, session: Session) -> None:
        with self._lock:
            self.session = dataclasses.replace(session, last_used=self._clock())
            self.keychain.write(self.session.to_json())
            self.notice = ""
            self.last_email = session.email
        logger.info("Account: signed in.")
        self._changed()

    def sign_out(self, notice: str = "") -> None:
        with self._lock:
            session, self.session = self.session, None
            self.keychain.delete()
            self.notice = notice
            if session is not None:
                self.last_email = session.email
        if session is not None:
            threading.Thread(target=self.auth.sign_out, args=(session,), name="sign-out", daemon=True).start()
        logger.info("Account: signed out.")
        self._changed()

    def refresh(self) -> None:
        """Renew the login if it is close to expiring. Offline: keep it (Utter works offline).
        If Supabase says the login is no longer valid (account removed or blocked): sign out."""
        if self.check_inactive():
            return
        session = self.session
        if session is None or not session.expires_soon():
            return
        try:
            new = self.auth.refresh(session)
        except AuthError as e:
            if e.signed_out:
                logger.warning("Account: the saved login is no longer valid (%s); signing out.", e.code)
                with self._lock:
                    if self.session == session:
                        self.session = None
                        self.keychain.delete()
                self._changed()
            else:
                logger.info("Account: could not renew the login now (%s); trying again later.", e.code or "error")
            return
        with self._lock:
            if self.session == session:
                self.session = dataclasses.replace(new, last_used=session.last_used)
                self.keychain.write(self.session.to_json())
        logger.info("Account: login renewed.")

    def refresh_in_background(self) -> None:
        threading.Thread(target=self.refresh, name="account-refresh", daemon=True).start()

    def _changed(self) -> None:
        try:
            self.on_change()
        except Exception as e:
            logger.error("Account change callback failed: %s", e, exc_info=True)
