"""Utter accounts (required sign-in): engine/account.py, the Keychain-backed AccountManager,
the account step of the welcome window, and the app's lock. A fake Supabase; no network."""

import dataclasses
import time
import urllib.error

import pytest

from engine import account
from engine.account import AuthError, Session, SupabaseAuth
from engine.config import AccountConfig, load_config
from interface import account_form as af
from interface.account_manager import AccountManager
from interface.keychain import Keychain, MemoryKeychain

URL = "https://utter-test.supabase.co"
KEY = "anon-public-key"


class FakeSupabase:
    """Just enough of Supabase Auth: users, 6-digit codes, tokens, errors in its JSON shape."""

    def __init__(self, confirm_email=True, online=True, old_style_errors=False):
        self.confirm_email = confirm_email
        self.online = online
        self.old_style_errors = old_style_errors
        self.users = {}  # email -> {"password", "confirmed", "id"}
        self.codes = {}  # (email, type) -> code
        self.refresh_tokens = {}  # token -> email
        self.requests = []
        self._n = 0

    def _error(self, status, code, msg):
        if self.old_style_errors:
            return status, {"error": code, "error_description": msg}
        return status, {"code": status, "error_code": code, "msg": msg}

    def _tokens(self, email):
        self._n += 1
        refresh = f"refresh-{self._n}"
        self.refresh_tokens[refresh] = email
        return 200, {"access_token": f"access-{self._n}", "token_type": "bearer", "expires_in": 3600,
                     "refresh_token": refresh, "user": {"id": self.users[email]["id"], "email": email}}

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, headers, body))
        if not self.online:
            raise urllib.error.URLError("offline")
        assert headers["apikey"] == KEY
        path = url[len(URL):]
        body = body or {}
        email = body.get("email", "")
        if path == "/auth/v1/signup":
            if email in self.users:
                return 200, {"id": "fake", "email": email, "identities": []}  # Supabase hides it
            if len(body["password"]) < 6:
                return self._error(422, "weak_password", "Password should be at least 6 characters")
            self.users[email] = {"password": body["password"], "confirmed": not self.confirm_email,
                                 "id": f"user-{len(self.users) + 1}"}
            if not self.confirm_email:
                return self._tokens(email)
            self.codes[(email, "signup")] = "123456"
            return 200, {"id": self.users[email]["id"], "email": email, "identities": [{"id": "x"}],
                         "confirmation_sent_at": "now"}
        if path == "/auth/v1/verify":
            if self.codes.get((email, body["type"])) != body["token"]:
                return self._error(403, "otp_expired", "Token has expired or is invalid")
            del self.codes[(email, body["type"])]
            self.users[email]["confirmed"] = True
            return self._tokens(email)
        if path == "/auth/v1/resend":
            self.codes[(email, "signup")] = "654321"
            return 200, {"message_id": "m1"}
        if path == "/auth/v1/token?grant_type=password":
            user = self.users.get(email)
            if not user or user["password"] != body["password"]:
                return self._error(400, "invalid_credentials" if not self.old_style_errors else "invalid_grant",
                                   "Invalid login credentials")
            if not user["confirmed"]:
                return self._error(400, "email_not_confirmed", "Email not confirmed")
            return self._tokens(email)
        if path == "/auth/v1/token?grant_type=refresh_token":
            owner = self.refresh_tokens.pop(body["refresh_token"], None)
            if owner is None:
                return self._error(400, "refresh_token_not_found", "Invalid Refresh Token")
            return self._tokens(owner)
        if path == "/auth/v1/recover":
            if email in self.users:
                self.codes[(email, "recovery")] = "111222"
            return 200, {}
        if path == "/auth/v1/user":
            assert headers["Authorization"].startswith("Bearer access-")
            self.users[self._last_verified]["password"] = body["password"]
            return 200, {"id": "x"}
        if path == "/auth/v1/logout":
            return 204, {}
        return 404, {}

    @property
    def _last_verified(self):
        return next(reversed(self.users))


@pytest.fixture
def server():
    return FakeSupabase()


@pytest.fixture
def auth(server):
    return SupabaseAuth(URL, KEY, transport=server, clock=lambda: 1000.0)


# ---- Checks before anything is sent --------------------------------------------------------

def test_typed_values_are_checked_first():
    assert account.check_email("me@example.com") is None
    assert account.check_email("me@example") and account.check_email("") and account.check_email("a b@c.de")
    assert account.check_password("12345678") is None and account.check_password("short")
    assert account.check_code("123 456") is None and account.check_code("12345") and account.check_code("abcdef")


# ---- Talking to Supabase -------------------------------------------------------------------

def test_create_account_confirm_with_the_emailed_code(auth, server):
    assert auth.sign_up(" new@example.com ", "a-good-password") is None  # code emailed
    with pytest.raises(AuthError) as wrong:
        auth.verify_signup("new@example.com", "000000")
    assert "expired or is wrong" in wrong.value.message
    session = auth.verify_signup("new@example.com", "123 456")
    assert session.email == "new@example.com" and session.user_id == "user-1"
    assert session.expires_at == 1000.0 + 3600  # from expires_in when expires_at is missing
    method, url, headers, body = server.requests[0]
    assert url == URL + "/auth/v1/signup" and body["email"] == "new@example.com"
    assert "a-good-password" not in url  # never in the address


def test_the_publishable_key_is_never_sent_as_a_bearer_token(auth, server):
    auth.sign_up("me@example.com", "a-good-password")
    _, _, headers, _ = server.requests[0]
    assert headers["apikey"] == KEY and "Authorization" not in headers
    session = auth.verify_signup("me@example.com", "123456")
    auth.sign_out(session)
    _, url, headers, _ = server.requests[-1]
    assert url.endswith("/logout") and headers["Authorization"] == f"Bearer {session.access_token}"


def test_projects_without_email_confirmation_sign_in_at_once(server):
    server.confirm_email = False
    auth = SupabaseAuth(URL, KEY, transport=server)
    assert isinstance(auth.sign_up("quick@example.com", "a-good-password"), Session)


def test_an_existing_email_is_reported_although_supabase_hides_it(auth, server):
    auth.sign_up("me@example.com", "a-good-password")
    with pytest.raises(AuthError) as e:
        auth.sign_up("me@example.com", "another-password")
    assert e.value.code == "user_already_exists" and "Sign in instead" in e.value.message


@pytest.mark.parametrize("old_style", [False, True])
def test_wrong_password_reads_plainly(old_style):
    server = FakeSupabase(confirm_email=False, old_style_errors=old_style)
    auth = SupabaseAuth(URL, KEY, transport=server)
    auth.sign_up("me@example.com", "a-good-password")
    with pytest.raises(AuthError) as e:
        auth.sign_in("me@example.com", "wrong-password")
    assert e.value.message == "Wrong email or password."


def test_unconfirmed_email_cannot_sign_in(auth):
    auth.sign_up("me@example.com", "a-good-password")
    with pytest.raises(AuthError) as e:
        auth.sign_in("me@example.com", "a-good-password")
    assert e.value.code == "email_not_confirmed" and "6-digit code" in e.value.message


def test_refresh_and_a_revoked_login(auth):
    auth.sign_up("me@example.com", "a-good-password")
    first = auth.verify_signup("me@example.com", "123456")
    second = auth.refresh(first)
    assert second.access_token != first.access_token and second.email == "me@example.com"
    with pytest.raises(AuthError) as e:
        auth.refresh(first)  # already used
    assert e.value.signed_out


def test_offline_is_not_the_same_as_signed_out(server, auth):
    server.online = False
    with pytest.raises(AuthError) as e:
        auth.sign_in("me@example.com", "a-good-password")
    assert e.value.offline and not e.value.signed_out and "internet" in e.value.message


def test_forgotten_password_reset_with_a_code(auth, server):
    server.confirm_email = False
    auth.sign_up("me@example.com", "old-password-1")
    auth.send_password_reset("me@example.com")
    session = auth.reset_password("me@example.com", "111222", "new-password-1")
    assert session.email == "me@example.com"
    assert auth.sign_in("me@example.com", "new-password-1")


def test_without_a_project_nothing_is_sent():
    auth = SupabaseAuth("", "", transport=lambda *a: pytest.fail("sent"))
    assert not auth.configured
    with pytest.raises(AuthError, match="not set up"):
        auth.sign_in("me@example.com", "x")
    assert not AccountConfig(url="", anon_key="").enabled


def test_project_comes_from_the_app_unless_the_settings_name_another(tmp_path):
    assert load_config().account == AccountConfig()
    path = tmp_path / "config.toml"
    shipped = (load_config.__globals__["DEFAULT_CONFIG_PATH"]).read_text(encoding="utf-8")
    path.write_text(shipped.replace('# url = "https://your-project.supabase.co"', f'url = "{URL}"')
                    .replace('# anon_key = "..."', f'anon_key = "{KEY}"'), encoding="utf-8")
    cfg = load_config(path).account
    assert cfg.url == URL and cfg.anon_key == KEY and cfg.enabled
    assert not hasattr(cfg, "required")  # signing in cannot be switched off in the settings file


def test_session_round_trips_and_bad_data_is_ignored():
    s = Session("a", "r", 123.0, "me@example.com", "u1")
    assert Session.from_json(s.to_json()) == s
    assert Session.from_json("{broken") is None and Session.from_json('{"x": 1}') is None
    assert s.expires_soon(now=123.0 - 10) and not s.expires_soon(now=123.0 - 3600)


def test_the_real_transport_is_blocked_in_tests():
    with pytest.raises(RuntimeError, match="SAFETY NET"):
        account.urllib_transport("GET", URL, {}, None)


# ---- Staying signed in (Keychain) ----------------------------------------------------------

def _manager(server, keychain=None, configured=True):
    auth = SupabaseAuth(URL if configured else "", KEY if configured else "", transport=server)
    return AccountManager(auth, keychain if keychain is not None else MemoryKeychain())


def _wait_for(condition, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end and not condition():
        time.sleep(0.01)
    return condition()


def test_without_accounts_set_up_nobody_is_locked_out(server):
    manager = _manager(server, configured=False)
    assert not manager.enabled and not manager.locked and manager.load() is False


def test_login_is_kept_in_the_keychain_and_survives_a_restart(server):
    server.confirm_email = False
    keychain = MemoryKeychain()
    manager = _manager(server, keychain)
    assert manager.load() is False and manager.locked
    changes = []
    manager.on_change = lambda: changes.append(manager.signed_in)
    manager.signed_in_with(SupabaseAuth(URL, KEY, transport=server).sign_up("me@example.com", "a-good-password"))
    assert not manager.locked and changes == [True]
    assert "a-good-password" not in keychain.text  # tokens only, never the password
    again = _manager(server, keychain)  # next start of Utter
    assert again.load() and again.email == "me@example.com" and not again.locked


def test_a_broken_saved_login_is_removed(server):
    keychain = MemoryKeychain("not json")
    manager = _manager(server, keychain)
    assert manager.load() is False and keychain.text is None and manager.locked


def test_sign_out_forgets_the_login_and_tells_the_server(server):
    server.confirm_email = False
    manager = _manager(server)
    manager.signed_in_with(SupabaseAuth(URL, KEY, transport=server).sign_up("me@example.com", "a-good-password"))
    manager.sign_out()
    assert manager.locked and manager.keychain.text is None
    assert _wait_for(lambda: any(r[1].endswith("/logout") for r in server.requests))


def test_offline_keeps_you_signed_in_but_a_revoked_login_does_not(server):
    server.confirm_email = False
    manager = _manager(server)
    session = SupabaseAuth(URL, KEY, transport=server).sign_up("me@example.com", "a-good-password")
    old = dataclasses.replace(session, expires_at=time.time() - 1)
    manager.signed_in_with(old)
    server.online = False
    manager.refresh()
    assert manager.session == old  # offline: still signed in, Utter keeps working
    server.online = True
    manager.refresh()
    assert manager.session != old and not manager.locked  # renewed
    server.refresh_tokens.clear()  # account removed on the server
    manager.session = dataclasses.replace(manager.session, expires_at=time.time() - 1)
    manager.refresh()
    assert manager.locked and manager.keychain.text is None


def test_a_fresh_login_is_not_renewed_needlessly(server):
    manager = _manager(server)
    manager.session = Session("a", "r", time.time() + 3600, "me@example.com", "u")
    manager.refresh()
    assert server.requests == []


def test_the_real_keychain_is_blocked_in_tests():
    assert Keychain(service="com.utter.test").read() is None  # logged, not raised, never touched


# ---- The account step's logic --------------------------------------------------------------

def test_create_then_confirm_then_signed_in(auth):
    form = af.AccountForm(auth)
    assert form.mode == af.CREATE and form.fields().email and form.fields().password
    assert form.perform(af.SUBMIT, "me@example.com", "short") is None
    assert "8 characters" in form.error and form.mode == af.CREATE  # nothing was sent
    assert form.perform(af.SUBMIT, "me@example.com", "a-good-password") is None
    assert form.mode == af.CONFIRM and "me@example.com" in form.subtitle() and form.fields().code
    form.perform(af.RESEND)
    assert "Sent again" in form.info
    session = form.perform(af.SUBMIT, code="654321")
    assert session is not None and form.done and form.heading() == "You're signed in"
    assert form.links() == [("Use a different account", af.SIGN_OUT)]


def test_existing_account_moves_to_sign_in_with_the_email_kept(auth):
    auth.sign_up("me@example.com", "a-good-password")
    form = af.AccountForm(auth)
    form.perform(af.SUBMIT, "me@example.com", "a-good-password")
    assert form.mode == af.SIGN_IN and form.email == "me@example.com" and "Sign in instead" in form.error


def test_sign_in_before_confirming_asks_for_the_code(auth):
    auth.sign_up("me@example.com", "a-good-password")
    form = af.AccountForm(auth)
    form.switch(af.GO_SIGN_IN)
    form.perform(af.SUBMIT, "me@example.com", "a-good-password")
    assert form.mode == af.CONFIRM and form.info == "We sent you a new code."
    assert form.perform(af.SUBMIT, code="654321") is not None


def test_forgot_password_flow(auth, server):
    server.confirm_email = False
    auth.sign_up("me@example.com", "old-password-1")
    form = af.AccountForm(auth)
    form.switch(af.GO_SIGN_IN)
    assert form.check(af.FORGOT, email="nope") is not None  # needs the email first
    form.perform(af.FORGOT, "me@example.com")
    assert form.mode == af.RESET and form.fields().code and form.fields().password
    assert form.perform(af.SUBMIT, password="new-password-1", code="111222") is not None and form.done


def test_already_signed_in_shows_done():
    form = af.AccountForm(auth=None, signed_in_email="me@example.com")
    assert form.done and "me@example.com" in form.subtitle() and form.submit_title() is None
    form.signed_out()
    assert form.mode == af.SIGN_IN


def test_offline_sign_in_says_so(server, auth):
    server.online = False
    form = af.AccountForm(auth)
    form.switch(af.GO_SIGN_IN)
    form.perform(af.SUBMIT, "me@example.com", "a-good-password")
    assert "internet" in form.error and form.mode == af.SIGN_IN


# ---- The window ----------------------------------------------------------------------------

def _window(auth, **kwargs):
    from interface.welcome_window import WelcomeWindow
    events = []
    window = WelcomeWindow(
        command_hotkey="<ctrl>+<alt>+m", dictation_hotkey="<ctrl>+<alt>+d",
        permissions=lambda: {"microphone": True, "accessibility": True, "input_monitoring": True},
        turn_on=lambda key: None, account_form=af.AccountForm(auth),
        signed_in=lambda s: events.append(("signed_in", s.email)),
        sign_out=lambda: events.append("sign_out"),
        finish=lambda on: events.append(("finish", on)),
        background=lambda work: work(), main=lambda work: work(),  # no threads in tests
        **kwargs)
    return window, events


def test_welcome_cannot_continue_past_the_account_step_until_signed_in(auth):
    window, events = _window(auth)
    assert window.flow.steps == ["welcome", "account", "permissions", "try", "finish"]
    window.next()
    assert window.flow.step_name == "account" and not window.next_button.isEnabled()
    window.next()
    assert window.flow.step_name == "account"  # required
    window.email_field.setStringValue_("me@example.com")
    window.password_field.setStringValue_("a-good-password")
    window.account(af.SUBMIT)
    assert window.account_form.mode == af.CONFIRM and window.password_field.stringValue() == ""
    assert not window.code_field.isHidden() and window.email_field.isHidden()
    window.code_field.setStringValue_("123456")
    window.account(af.SUBMIT)
    assert events == [("signed_in", "me@example.com")]
    assert window.next_button.isEnabled() and not window.account_check.isHidden()
    window.next()
    assert window.flow.step_name == "permissions"
    window.close()


def test_errors_show_in_red_and_nothing_is_sent_for_bad_input(auth, server):
    window, _ = _window(auth, start_step="account")
    window.email_field.setStringValue_("not-an-email")
    window.account(af.SUBMIT)
    assert "valid email" in window.account_message.stringValue() and server.requests == []
    window.close()


def test_sign_in_window_is_just_the_account_step(auth):
    from interface.setup_flow import ACCOUNT_ONLY
    window, events = _window(auth, steps=ACCOUNT_ONLY)
    assert window.window.title() == "Sign in to Utter" and window.step_label.stringValue() == ""
    assert window.back_button.isHidden() and window.next_button.title() == "Start Using Utter"
    window.account(af.GO_SIGN_IN)
    assert window.account_form.mode == af.SIGN_IN
    window.close()


def test_use_a_different_account_signs_out(server):
    from interface.welcome_window import WelcomeWindow
    events = []
    window = WelcomeWindow(
        "<ctrl>+<alt>+m", "<ctrl>+<alt>+d", permissions=lambda: {}, turn_on=lambda k: None,
        account_form=af.AccountForm(None, signed_in_email="me@example.com"),
        sign_out=lambda: events.append("sign_out"), start_step="account",
        background=lambda w: w(), main=lambda w: w())
    assert window.next_button.isEnabled()
    window.account(af.SIGN_OUT)
    assert events == ["sign_out"] and window.account_form.mode == af.SIGN_IN
    assert not window.next_button.isEnabled()
    window.close()


# ---- In the app ----------------------------------------------------------------------------

def _app(monkeypatch, tmp_path, server, signed_in=False):
    import interface.app as app_mod
    from engine.config import VocabularyConfig
    from engine.session import SessionManager
    config = dataclasses.replace(load_config(), vocabulary=VocabularyConfig(file=None),
                                 account=AccountConfig(url=URL, anon_key=KEY))
    app = app_mod.VoiceAssistantApp(config=config, session_manager=SessionManager(config=config))
    app.overlay = None
    app.setup_record = tmp_path / "setup.json"
    manager = _manager(server)
    if signed_in:
        manager.session = Session("a", "r", time.time() + 3600, "me@example.com", "u1")
    manager.on_change = app._on_account_change
    app.account = manager
    app._refresh_account_menu()
    app.model_ready = True
    return app_mod, app


def test_signed_out_utter_does_nothing_but_ask_to_sign_in(monkeypatch, tmp_path, server):
    app_mod, app = _app(monkeypatch, tmp_path, server)
    shown, started = [], []
    monkeypatch.setattr(app, "show_sign_in", lambda *_: shown.append(True))
    monkeypatch.setattr(app.session_manager, "start_session", lambda mode: started.append(mode))
    app._on_command_hotkey()
    app._on_dictation_hotkey()
    assert started == [] and shown == [True, True]
    assert app._ready_status_text() == "🔒 Sign in to use Utter"
    assert app.account_item.title == "Sign In…" and "Sign In…" in app.menu
    app.menu_icon.stop()


def test_signed_in_utter_works_and_offers_sign_out(monkeypatch, tmp_path, server):
    app_mod, app = _app(monkeypatch, tmp_path, server, signed_in=True)
    started = []
    monkeypatch.setattr(app.session_manager, "start_session", lambda mode: started.append(mode))
    app._on_command_hotkey()
    assert len(started) == 1
    assert app.account_item.title == "Sign Out (me@example.com)"
    monkeypatch.setattr(app_mod.rumps, "alert", lambda *a, **k: 1)  # "Sign Out"
    app.confirm_sign_out()
    assert app.account.locked and app.account_item.title == "Sign In…"
    assert app.status_item.title == "🔒 Sign in to use Utter"
    app.menu_icon.stop()


def test_without_accounts_there_is_no_account_menu_and_no_lock(monkeypatch, tmp_path):
    import interface.app as app_mod
    from engine.config import VocabularyConfig
    from engine.session import SessionManager
    config = dataclasses.replace(load_config(), vocabulary=VocabularyConfig(file=None),
                                 account=AccountConfig(url="", anon_key=""))
    app = app_mod.VoiceAssistantApp(config=config, session_manager=SessionManager(config=config))
    assert "Sign In…" not in app.menu and not app.account.locked
    app.start_account(show_sign_in=True)  # nothing happens, nothing read from the Keychain
    app.menu_icon.stop()


def test_start_reads_the_saved_login_and_opens_sign_in_only_when_needed(monkeypatch, tmp_path, server):
    app_mod, app = _app(monkeypatch, tmp_path, server)
    scheduled = []
    monkeypatch.setattr(app_mod, "run_soon", scheduled.append)
    monkeypatch.setattr(app_mod.rumps, "Timer", lambda cb, sec: type("T", (), {"start": lambda s: None,
                                                                              "stop": lambda s: None})())
    app.start_account(show_sign_in=True)
    assert scheduled == [app.show_sign_in]  # nobody signed in: the sign-in window opens
    scheduled.clear()
    app.account.keychain.write(Session("a", "r", time.time() + 3600, "me@example.com", "u").to_json())
    app.start_account(show_sign_in=True)
    assert scheduled == [] and not app.account.locked
    app.menu_icon.stop()


def test_setup_window_includes_the_account_step_when_accounts_are_on(monkeypatch, tmp_path, server):
    import interface.welcome_window as ww
    app_mod, app = _app(monkeypatch, tmp_path, server)
    monkeypatch.setattr(ww.WelcomeWindow, "show", lambda self: None)
    app.show_setup()
    assert "account" in app.welcome.flow.steps
    app.welcome.close()
    app.welcome = None
    app.show_sign_in()
    assert app.welcome.flow.steps == ["account"]
    app.welcome.close()
    app.menu_icon.stop()
