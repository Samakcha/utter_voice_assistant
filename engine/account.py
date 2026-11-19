"""
Utter accounts: create an account, sign in and stay signed in, using Supabase Auth over
plain HTTPS (no extra package). Only the email address and password go to Supabase, and
only here: voice, dictated text and notes never leave the Mac.

Signing up and resetting a password use a 6-digit code sent by email (typed into Utter),
so no website is needed. The project's email templates must show the code ({{ .Token }});
see the PRD, section 6.17.

Passwords are never stored or logged. The session (tokens) is kept by the interface, in
the macOS Keychain.
"""

from dataclasses import asdict, dataclass
import json
import logging
import re
import ssl
import time
from typing import Callable, Dict, Optional, Tuple
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

MIN_PASSWORD_LENGTH = 8
CODE_LENGTH = 6
TIMEOUT_SEC = 15.0
REFRESH_MARGIN_SEC = 300  # refresh the access token when it has less than 5 minutes left

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Supabase's error codes -> what the user reads
FRIENDLY_ERRORS = {
    "invalid_credentials": "Wrong email or password.",
    "invalid_grant": "Wrong email or password.",
    "email_not_confirmed": "Please confirm your email first: enter the 6-digit code we sent you.",
    "user_already_exists": "An account with this email already exists. Sign in instead.",
    "email_exists": "An account with this email already exists. Sign in instead.",
    "weak_password": f"Please choose a stronger password (at least {MIN_PASSWORD_LENGTH} characters).",
    "over_email_send_rate_limit": "Too many emails were sent just now. Please wait a few minutes and try again.",
    "over_request_rate_limit": "Too many tries. Please wait a minute and try again.",
    "otp_expired": "That code has expired or is wrong. Ask for a new one.",
    "otp_disabled": "Sign-in codes are switched off for this Utter project.",
    "signup_disabled": "New accounts are switched off right now.",
    "email_address_invalid": "That email address doesn't look right.",
    "validation_failed": "Please check the email and password.",
    "user_banned": "This account has been blocked.",
    "user_not_found": "No account was found for this email.",
    "session_not_found": "You were signed out. Please sign in again.",
    "refresh_token_not_found": "You were signed out. Please sign in again.",
    "refresh_token_already_used": "You were signed out. Please sign in again.",
}
NETWORK_ERROR = "Can't reach the internet. Utter needs it once to sign in; please check your connection."

# These mean the saved login is no longer valid (not just "offline")
SIGNED_OUT_CODES = {"refresh_token_not_found", "refresh_token_already_used", "session_not_found",
                    "user_banned", "user_not_found", "invalid_grant", "bad_jwt"}


class AuthError(Exception):
    """A sign-in problem, with a message the user can read. offline=True: no connection."""

    def __init__(self, message: str, code: str = "", offline: bool = False, status: int = 0):
        super().__init__(message)
        self.message = message
        self.code = code
        self.offline = offline
        self.status = status

    @property
    def signed_out(self) -> bool:
        return self.code in SIGNED_OUT_CODES


@dataclass(frozen=True)
class Session:
    access_token: str
    refresh_token: str
    expires_at: float  # seconds since 1970
    email: str
    user_id: str
    last_used: float = 0.0  # when Utter was last used (a hotkey); 0: not known yet

    def expires_soon(self, now: Optional[float] = None) -> bool:
        return (now if now is not None else time.time()) >= self.expires_at - REFRESH_MARGIN_SEC

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> Optional["Session"]:
        try:
            data = json.loads(text)
            return cls(access_token=str(data["access_token"]), refresh_token=str(data["refresh_token"]),
                       expires_at=float(data["expires_at"]), email=str(data.get("email", "")),
                       user_id=str(data.get("user_id", "")), last_used=float(data.get("last_used", 0.0)))
        except (ValueError, KeyError, TypeError):
            return None


def check_email(email: str) -> Optional[str]:
    """None when it looks like an email address, else what to fix."""
    return None if _EMAIL.match((email or "").strip()) else "Please enter a valid email address."


def check_password(password: str) -> Optional[str]:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        return f"The password needs at least {MIN_PASSWORD_LENGTH} characters."
    return None


def check_code(code: str) -> Optional[str]:
    code = (code or "").strip().replace(" ", "")
    return None if (code.isdigit() and len(code) >= CODE_LENGTH) else f"Enter the {CODE_LENGTH}-digit code from the email."


# (method, url, headers, body) -> (HTTP status, parsed JSON)
Transport = Callable[[str, str, Dict[str, str], Optional[dict]], Tuple[int, dict]]


def urllib_transport(method: str, url: str, headers: Dict[str, str], body: Optional[dict]) -> Tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    context = ssl.create_default_context()  # macOS's certificates (/etc/ssl/cert.pem)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SEC, context=context) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as e:
        raw = e.read()
        status = e.code
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError:
        parsed = {}
    return status, parsed if isinstance(parsed, dict) else {}


class SupabaseAuth:
    """The few Supabase Auth calls Utter needs. Every method raises AuthError on failure."""

    def __init__(self, url: str, anon_key: str, transport: Optional[Transport] = None,
                 clock: Callable[[], float] = time.time):
        self.url = (url or "").rstrip("/")
        self.anon_key = anon_key or ""
        self._transport = transport or urllib_transport
        self._clock = clock

    @property
    def configured(self) -> bool:
        return bool(self.url and self.anon_key)

    # ---- Calls ---------------------------------------------------------------------------

    def sign_up(self, email: str, password: str) -> Optional[Session]:
        """Create an account. Returns None when a code was emailed (confirm with verify_signup),
        or a Session if the project confirms emails automatically."""
        email = email.strip()
        data = self._call("POST", "/auth/v1/signup", {"email": email, "password": password})
        if "access_token" in data:
            return self._session(data, email)
        user = data.get("user") if isinstance(data.get("user"), dict) else data
        if isinstance(user.get("identities"), list) and not user["identities"]:
            # Supabase's answer for an email that already has an account (it never says so directly)
            raise AuthError(FRIENDLY_ERRORS["user_already_exists"], "user_already_exists")
        return None

    def verify_signup(self, email: str, code: str) -> Session:
        return self._verify(email, code, "signup")

    def resend_signup_code(self, email: str) -> None:
        self._call("POST", "/auth/v1/resend", {"email": email.strip(), "type": "signup"})

    def sign_in(self, email: str, password: str) -> Session:
        email = email.strip()
        data = self._call("POST", "/auth/v1/token?grant_type=password", {"email": email, "password": password})
        return self._session(data, email)

    def refresh(self, session: Session) -> Session:
        data = self._call("POST", "/auth/v1/token?grant_type=refresh_token",
                          {"refresh_token": session.refresh_token})
        return self._session(data, session.email)

    def send_password_reset(self, email: str) -> None:
        self._call("POST", "/auth/v1/recover", {"email": email.strip()})

    def reset_password(self, email: str, code: str, new_password: str) -> Session:
        """The code from the reset email signs in; then the new password is set."""
        session = self._verify(email, code, "recovery")
        self._call("PUT", "/auth/v1/user", {"password": new_password}, token=session.access_token)
        return session

    def sign_out(self, session: Session) -> None:
        try:
            self._call("POST", "/auth/v1/logout", None, token=session.access_token)
        except AuthError as e:
            logger.debug("Sign-out on the server failed (signed out on this Mac anyway): %s", e.code)

    # ---- Plumbing ------------------------------------------------------------------------

    def _verify(self, email: str, code: str, kind: str) -> Session:
        email = email.strip()
        data = self._call("POST", "/auth/v1/verify",
                          {"type": kind, "email": email, "token": code.strip().replace(" ", "")})
        return self._session(data, email)

    def _session(self, data: dict, email: str) -> Session:
        if not data.get("access_token") or not data.get("refresh_token"):
            raise AuthError("Unexpected answer from the sign-in server. Please try again.", "bad_response")
        user = data.get("user") if isinstance(data.get("user"), dict) else {}
        expires_at = data.get("expires_at")
        if not expires_at:
            expires_at = self._clock() + float(data.get("expires_in", 3600))
        return Session(access_token=str(data["access_token"]), refresh_token=str(data["refresh_token"]),
                       expires_at=float(expires_at), email=str(user.get("email") or email),
                       user_id=str(user.get("id", "")), last_used=self._clock())

    def _call(self, method: str, path: str, body: Optional[dict], token: Optional[str] = None) -> dict:
        if not self.configured:
            raise AuthError("Accounts are not set up in this copy of Utter.", "not_configured")
        # The project key goes in "apikey" only: Supabase's new publishable keys (sb_publishable_...)
        # are not tokens and are refused as "Authorization: Bearer"; that header carries a
        # signed-in user's access token, and only when there is one.
        headers = {"apikey": self.anon_key, "Content-Type": "application/json", "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            status, data = self._transport(method, self.url + path, headers, body)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            logger.info("Sign-in server not reachable: %s", type(e).__name__)
            raise AuthError(NETWORK_ERROR, "network", offline=True) from e
        if 200 <= status < 300:
            return data
        code = str(data.get("error_code") or data.get("error") or "")
        message = FRIENDLY_ERRORS.get(code)
        if message is None:
            text = str(data.get("msg") or data.get("error_description") or data.get("message") or "")
            if "already registered" in text.lower():
                code, message = "user_already_exists", FRIENDLY_ERRORS["user_already_exists"]
            elif "invalid login" in text.lower():
                code, message = "invalid_credentials", FRIENDLY_ERRORS["invalid_credentials"]
            else:
                message = text or f"The sign-in server said no (error {status}). Please try again."
        logger.info("Account request %s failed: HTTP %d %s", path.split("?")[0], status, code or "-")
        raise AuthError(message, code, status=status)
