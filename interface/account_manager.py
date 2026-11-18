"""
Who is signed in to Utter. The login (session tokens) is kept in the Keychain, so Utter
stays signed in across restarts and works offline; it is refreshed in the background when
online. Signing in is required before the hotkeys do anything (when accounts are set up).
"""

import logging
import threading
from typing import Callable, Optional

from engine.account import AuthError, Session, SupabaseAuth
from .keychain import Keychain

logger = logging.getLogger(__name__)

REFRESH_EVERY_SEC = 6 * 3600


class AccountManager:
    def __init__(self, auth: SupabaseAuth, keychain=None, required: bool = True):
        self.auth = auth
        self.keychain = keychain if keychain is not None else Keychain()
        self.required = required
        self.session: Optional[Session] = None
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
        logger.info("Account: %s", "signed in" if self.session else "not signed in")
        return self.session is not None

    def signed_in_with(self, session: Session) -> None:
        with self._lock:
            self.session = session
            self.keychain.write(session.to_json())
        logger.info("Account: signed in.")
        self._changed()

    def sign_out(self) -> None:
        with self._lock:
            session, self.session = self.session, None
            self.keychain.delete()
        if session is not None:
            threading.Thread(target=self.auth.sign_out, args=(session,), name="sign-out", daemon=True).start()
        logger.info("Account: signed out.")
        self._changed()

    def refresh(self) -> None:
        """Renew the login if it is close to expiring. Offline: keep it (Utter works offline).
        If Supabase says the login is no longer valid (account removed or blocked): sign out."""
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
                self.session = new
                self.keychain.write(new.to_json())
        logger.info("Account: login renewed.")

    def refresh_in_background(self) -> None:
        threading.Thread(target=self.refresh, name="account-refresh", daemon=True).start()

    def _changed(self) -> None:
        try:
            self.on_change()
        except Exception as e:
            logger.error("Account change callback failed: %s", e, exc_info=True)
