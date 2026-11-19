"""
The account step of the welcome window: create an account, confirm it with the 6-digit
code from the email, sign in, or reset a forgotten password. Plain logic (testable);
interface/welcome_window.py draws it and runs `perform` on a background thread.
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

from engine.account import AuthError, Session, check_code, check_email, check_password
from engine.email_check import is_disposable, suggest_email

CREATE = "create"
CONFIRM = "confirm"
SIGN_IN = "sign_in"
RESET = "reset"
DONE = "done"

# Actions (buttons and links)
SUBMIT = "submit"
RESEND = "resend"
FORGOT = "forgot"
GO_CREATE = "go_create"
GO_SIGN_IN = "go_sign_in"
SIGN_OUT = "sign_out"
USE_SUGGESTION = "use_suggestion"  # "Did you mean name@gmail.com?" -> yes
KEEP_EMAIL = "keep_email"  # -> no, what I typed is right

DISPOSABLE_MESSAGE = "Please use your own email address (throwaway addresses can't be used)."


@dataclass
class Fields:
    email: bool = False
    password: bool = False
    code: bool = False


class AccountForm:
    def __init__(self, auth, signed_in_email: str = "", last_email: str = "", notice: str = ""):
        """last_email: someone signed in on this Mac before (open on "Sign in", pre-filled);
        notice: why they were signed out ("You haven't used Utter for 7 days...")."""
        self.auth = auth
        self.mode = DONE if signed_in_email else (SIGN_IN if last_email else CREATE)
        self.email = signed_in_email or last_email
        self.error = ""
        self.info = "" if signed_in_email else notice
        self.busy = False
        self.suggestion: Optional[str] = None  # a corrected email waiting for yes / no
        self._kept_email = ""  # the user said the typed email is right

    # ---- What to show ------------------------------------------------------------------

    def heading(self) -> str:
        return {CREATE: "Create your Utter account", CONFIRM: "Check your email",
                SIGN_IN: "Sign in to Utter", RESET: "Choose a new password",
                DONE: "You're signed in"}[self.mode]

    def subtitle(self) -> str:
        if self.mode == CREATE:
            return "Free. Only your email is sent; your voice and text never leave this Mac."
        if self.mode == CONFIRM:
            return f"Enter the 6-digit code we sent to {self.email}."
        if self.mode == SIGN_IN:
            return "Welcome back. Sign in with your email and password."
        if self.mode == RESET:
            return f"Enter the 6-digit code we sent to {self.email} and a new password."
        return f"Signed in as {self.email}. Utter stays signed in, even offline."

    def fields(self) -> Fields:
        if self.mode in (CREATE, SIGN_IN):
            return Fields(email=True, password=True)
        if self.mode == CONFIRM:
            return Fields(code=True)
        if self.mode == RESET:
            return Fields(code=True, password=True)
        return Fields()

    def password_placeholder(self) -> str:
        return "New password (8+ characters)" if self.mode in (CREATE, RESET) else "Password"

    def submit_title(self) -> Optional[str]:
        return {CREATE: "Create Account", CONFIRM: "Verify", SIGN_IN: "Sign In",
                RESET: "Set New Password"}.get(self.mode)

    def links(self) -> List[Tuple[str, str]]:
        if self.mode == CREATE and self.suggestion:
            return [(f"Yes, use {self.suggestion}", USE_SUGGESTION), ("No, mine is right", KEEP_EMAIL)]
        if self.mode == CREATE:
            return [("I already have an account", GO_SIGN_IN)]
        if self.mode == CONFIRM:
            return [("Send the code again", RESEND), ("Use a different email", GO_CREATE)]
        if self.mode == SIGN_IN:
            return [("Forgot password?", FORGOT), ("Create an account", GO_CREATE)]
        if self.mode == RESET:
            return [("Send the code again", FORGOT), ("Back to sign in", GO_SIGN_IN)]
        return [("Use a different account", SIGN_OUT)]

    @property
    def done(self) -> bool:
        return self.mode == DONE

    # ---- Doing -------------------------------------------------------------------------

    def switch(self, action: str) -> bool:
        """Links that only change the form (no network). True if handled here."""
        if action == GO_CREATE:
            self.mode, self.error, self.info, self.suggestion = CREATE, "", "", None
            return True
        if action == GO_SIGN_IN:
            self.mode, self.error, self.info = SIGN_IN, "", ""
            return True
        return False

    def check(self, action: str, email: str = "", password: str = "", code: str = "") -> Optional[str]:
        """What is wrong with the typed values before anything is sent (None: fine)."""
        if action == FORGOT and self.mode == SIGN_IN:
            return check_email(email)
        if action != SUBMIT:
            return None
        if self.mode == CREATE:
            return (check_email(email) or (DISPOSABLE_MESSAGE if is_disposable(email) else None)
                    or check_password(password))
        if self.mode == SIGN_IN:
            return check_email(email) or (None if password else "Please enter your password.")
        if self.mode == CONFIRM:
            return check_code(code)
        if self.mode == RESET:
            return check_code(code) or check_password(password)
        return None

    def perform(self, action: str, email: str = "", password: str = "", code: str = "") -> Optional[Session]:
        """Run one action against the server (blocking: call it off the main thread).
        Updates mode/error/info; returns the Session when someone just signed in."""
        self.error, self.info = "", ""
        if action == KEEP_EMAIL:
            self._kept_email, action = email.strip().lower(), SUBMIT
        self.suggestion = None
        problem = self.check(action, email, password, code)
        if problem:
            self.error = problem
            return None
        if action == SUBMIT and self.mode == CREATE:
            fixed = suggest_email(email)
            if fixed and email.strip().lower() != self._kept_email:
                self.suggestion = fixed  # ask first; nothing is sent yet
                self.info = f"Did you mean {fixed}?"
                return None
        if self.mode in (CREATE, SIGN_IN) or (action == FORGOT and self.mode == SIGN_IN):
            self.email = email.strip()
        try:
            return self._perform(action, password, code)
        except AuthError as e:
            self.error = e.message
            if e.code in ("user_already_exists", "email_exists") and self.mode == CREATE:
                self.mode = SIGN_IN  # keep the email; just ask for the password
            elif e.code == "email_not_confirmed" and self.mode == SIGN_IN:
                self.mode = CONFIRM
                try:
                    self.auth.resend_signup_code(self.email)
                    self.info = "We sent you a new code."
                except AuthError:
                    pass
            return None

    def _perform(self, action: str, password: str, code: str) -> Optional[Session]:
        if action == SUBMIT and self.mode == CREATE:
            session = self.auth.sign_up(self.email, password)
            if session is None:
                self.mode = CONFIRM
                return None
            return self._signed_in(session)
        if action == SUBMIT and self.mode == CONFIRM:
            return self._signed_in(self.auth.verify_signup(self.email, code))
        if action == RESEND and self.mode == CONFIRM:
            self.auth.resend_signup_code(self.email)
            self.info = "Sent again. It can take a minute; check your spam folder too."
            return None
        if action == SUBMIT and self.mode == SIGN_IN:
            return self._signed_in(self.auth.sign_in(self.email, password))
        if action == FORGOT:
            self.auth.send_password_reset(self.email)
            self.mode = RESET
            self.info = "If there is an account for this email, the code is on its way."
            return None
        if action == SUBMIT and self.mode == RESET:
            return self._signed_in(self.auth.reset_password(self.email, code, password))
        return None

    def _signed_in(self, session: Session) -> Session:
        self.mode = DONE
        self.email = session.email or self.email
        return session

    def signed_out(self) -> None:
        self.mode, self.error, self.info = SIGN_IN, "", ""
