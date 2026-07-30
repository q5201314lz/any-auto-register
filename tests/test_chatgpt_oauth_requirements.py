from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.base_platform import RegisterConfig
from core.totp import generate_totp
from platforms.chatgpt import browser_register as browser_register_module
from platforms.chatgpt.protocol_mailbox import ChatGPTProtocolMailboxWorker
from platforms.chatgpt.plugin import (
    ChatGPTPlatform,
    _assert_complete_oauth_callback,
    _generate_chatgpt_registration_password,
)
from platforms.chatgpt.register import RegistrationEngine


def test_assert_complete_oauth_callback_accepts_complete_payload():
    _assert_complete_oauth_callback({
        "account_id": "acct_123",
        "access_token": "at_123",
        "refresh_token": "rt_123",
        "id_token": "id_123",
    })


def test_assert_complete_oauth_callback_rejects_partial_payload():
    with pytest.raises(RuntimeError, match=r"OAuth .*refresh_token"):
        _assert_complete_oauth_callback({
            "account_id": "acct_123",
            "access_token": "at_123",
            "refresh_token": "",
            "id_token": "",
        })


def test_generate_chatgpt_registration_password_meets_openai_strength_requirements():
    for _ in range(8):
        password = _generate_chatgpt_registration_password()
        assert len(password) >= 12
        assert any(ch.islower() for ch in password)
        assert any(ch.isupper() for ch in password)
        assert any(ch.isdigit() for ch in password)
        assert any(ch in ",._!@#" for ch in password)


def test_chatgpt_platform_preserves_user_supplied_password():
    platform = object.__new__(ChatGPTPlatform)
    assert platform._prepare_registration_password("Secret123!") == "Secret123!"


def test_codex_oauth_login_password_forces_email_otp(monkeypatch):
    oauth_start = SimpleNamespace(
        auth_url="https://auth.openai.com/log-in/password",
        state="state_123",
        code_verifier="verifier_123",
        redirect_uri="http://localhost:1455/auth/callback",
        client_id="client_123",
    )

    class FakePage:
        url = "about:blank"

        def goto(self, url, **kwargs):
            self.url = url

        def evaluate(self, script):
            return "Test User Agent"

    page = FakePage()
    events = []

    monkeypatch.setattr("platforms.chatgpt.oauth.generate_oauth_url", lambda **kwargs: oauth_start)
    monkeypatch.setattr(browser_register_module, "_get_page_oauth_url", lambda page: "")

    def switch_to_otp(page, log):
        events.append("switch_to_otp")
        page.url = "https://auth.openai.com/email-verification"
        return True

    def submit_otp(page, code, log):
        events.append(("submit_otp", code))
        page.url = "http://localhost:1455/auth/callback?code=code_123&state=state_123"
        return {"ok": True, "status": 200, "text": ""}

    monkeypatch.setattr(browser_register_module, "_switch_login_password_to_otp", switch_to_otp)
    monkeypatch.setattr(browser_register_module, "_submit_otp_via_page", submit_otp)
    monkeypatch.setattr(
        browser_register_module,
        "_submit_oauth_password_direct",
        lambda *args, **kwargs: pytest.fail("login password must not be submitted"),
    )
    monkeypatch.setattr(
        browser_register_module,
        "_submit_callback_result",
        lambda callback_url, oauth_start, proxy: {"callback_url": callback_url},
    )

    result = browser_register_module._do_codex_oauth(
        page,
        {},
        "user@example.com",
        "Secret123!",
        lambda: "123456",
        None,
        None,
        lambda message: None,
    )

    assert events == ["switch_to_otp", ("submit_otp", "123456")]
    assert result["callback_url"].startswith("http://localhost:1455/auth/callback?")


def test_generate_totp_matches_rfc_6238_sha1_vector():
    assert generate_totp(
        "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
        timestamp=59,
        digits=8,
    ) == "94287082"


def test_codex_oauth_login_password_uses_configured_mfa(monkeypatch):
    oauth_start = SimpleNamespace(
        auth_url="https://auth.openai.com/log-in/password",
        state="state_123",
        code_verifier="verifier_123",
        redirect_uri="http://localhost:1455/auth/callback",
        client_id="client_123",
    )

    class FakePage:
        url = "about:blank"

        def goto(self, url, **kwargs):
            self.url = url

        def evaluate(self, script):
            return "Test User Agent"

    page = FakePage()
    events = []

    monkeypatch.setattr("platforms.chatgpt.oauth.generate_oauth_url", lambda **kwargs: oauth_start)
    monkeypatch.setattr(browser_register_module, "_get_page_oauth_url", lambda page: "")
    monkeypatch.setattr(
        browser_register_module,
        "_derive_oauth_state_from_page",
        lambda page: {
            "page_type": "mfa_challenge" if "/mfa" in page.url else "login_password",
            "continue_url": "",
        },
    )

    def submit_password(page, password, log):
        events.append(("password", password))
        page.url = "https://auth.openai.com/mfa"
        return {"ok": True, "status": 200, "text": ""}

    def submit_mfa(page, code, log):
        events.append(("mfa", code))
        page.url = "http://localhost:1455/auth/callback?code=code_123&state=state_123"
        return {"ok": True, "status": 200, "text": ""}

    monkeypatch.setattr(browser_register_module, "_submit_oauth_password_direct", submit_password)
    monkeypatch.setattr(browser_register_module, "_submit_otp_via_page", submit_mfa)
    monkeypatch.setattr(
        browser_register_module,
        "_submit_callback_result",
        lambda callback_url, oauth_start, proxy: {"callback_url": callback_url},
    )

    result = browser_register_module._do_codex_oauth(
        page,
        {},
        "user@example.com",
        "Secret123!",
        lambda: "email-code-must-not-be-used",
        None,
        None,
        lambda message: None,
        mfa_callback=lambda: "123456",
    )

    assert events == [("password", "Secret123!"), ("mfa", "123456")]
    assert result["callback_url"].startswith("http://localhost:1455/auth/callback?")


def test_protocol_codex_password_challenge_sends_and_validates_email_otp():
    engine = object.__new__(RegistrationEngine)
    engine._otp_sent_at = None
    engine._debug_log = lambda message: None
    engine._log = lambda message, level="info": None
    engine._get_verification_code = lambda: "654321"

    class Response:
        def __init__(self, status_code, data=None, text=""):
            self.status_code = status_code
            self._data = data or {}
            self.text = text
            self.headers = {"content-type": "application/json"}

        def json(self):
            return self._data

    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append(("GET", url, kwargs))
            return Response(200)

        def post(self, url, **kwargs):
            self.calls.append(("POST", url, kwargs))
            return Response(200, {"page": {"type": "add_phone"}})

    session = Session()
    page_type = engine._complete_codex_email_otp(
        session,
        send_code=True,
        referer="https://auth.openai.com/log-in/password",
    )

    assert page_type == "add_phone"
    assert engine._otp_sent_at is not None
    assert [call[0] for call in session.calls] == ["GET", "POST"]
    assert session.calls[0][1].endswith("/api/accounts/email-otp/send")
    assert json.loads(session.calls[1][2]["data"]) == {"code": "654321"}


def test_protocol_otp_send_rejects_redirect_false_positive():
    engine = object.__new__(RegistrationEngine)
    engine._otp_sent_at = None
    engine._debug_log = lambda message: None
    logs = []
    engine._log = lambda message, level="info": logs.append((level, message))
    engine._get_verification_code = lambda: pytest.fail("must not wait after redirect")

    response = SimpleNamespace(
        status_code=302,
        text="",
        headers={"location": "/log-in/password", "content-type": "text/html"},
    )
    session = SimpleNamespace(get=lambda *args, **kwargs: response)

    assert engine._complete_codex_email_otp(
        session,
        send_code=True,
        referer="https://auth.openai.com/log-in/password",
    ) is None
    assert any("发送失败" in message for _, message in logs)


def test_protocol_password_challenge_uses_browser_passwordless_action(monkeypatch):
    engine = object.__new__(RegistrationEngine)
    engine.email = "user@example.com"
    engine.password = "Secret123!"
    engine.proxy_url = "http://127.0.0.1:18080"
    engine.phone_callback = None
    engine._otp_sent_at = None
    engine._last_codex_error = ""
    engine._log = lambda message, level="info": None
    engine._get_verification_code = lambda: "654321"
    captured = {}

    def retry(self, email, password):
        captured.update(email=email, password=password, proxy=self.proxy)
        assert self.otp_callback() == "654321"
        return {"access_token": "at", "refresh_token": "rt"}

    monkeypatch.setattr(browser_register_module.ChatGPTBrowserRegister, "_retry_oauth_fresh_browser", retry)

    token_info = engine._complete_codex_login_password_in_browser()

    assert token_info == {"access_token": "at", "refresh_token": "rt"}
    assert captured == {
        "email": "user@example.com",
        "password": "Secret123!",
        "proxy": "http://127.0.0.1:18080",
    }
    assert engine._otp_sent_at is not None


def test_mfa_browser_login_disables_email_otp_callback(monkeypatch):
    engine = object.__new__(RegistrationEngine)
    engine.email = "user@example.com"
    engine.password = "Secret123!"
    engine.totp_secret = "JBSWY3DPEHPK3PXP"
    engine.proxy_url = None
    engine.phone_callback = None
    engine._last_codex_error = ""
    engine._log = lambda message, level="info": None
    engine._get_verification_code = lambda: pytest.fail("MFA login must not read mailbox OTP")

    def retry(self, email, password):
        assert self.otp_callback is None
        assert callable(self.mfa_callback)
        assert self.mfa_callback().isdigit()
        return {"access_token": "at", "refresh_token": "rt"}

    monkeypatch.setattr(browser_register_module.ChatGPTBrowserRegister, "_retry_oauth_fresh_browser", retry)

    token_info = engine._complete_codex_login_password_in_browser()

    assert token_info == {"access_token": "at", "refresh_token": "rt"}


def test_existing_mfa_login_bypasses_protocol_email_otp_detection(monkeypatch):
    engine = object.__new__(RegistrationEngine)
    engine.logs = []
    engine.email = "user@example.com"
    engine.password = "Secret123!"
    engine.totp_secret = ""
    engine.email_info = {"email": "user@example.com"}
    engine.email_service = SimpleNamespace(service_type=SimpleNamespace(value="local_ms_pool"))
    engine.proxy_url = None
    engine._last_codex_error = ""
    engine._is_existing_account = False
    engine._codex_direct_token_info = None
    engine._log = lambda message, level="info": None
    engine._preflight_codex_auth_network = lambda: (True, "ok")
    engine._debug_log = lambda message, level="info": None
    engine._acquire_codex_callback = lambda: pytest.fail("MFA login must bypass protocol page detection")
    engine._complete_codex_login_password_in_browser = lambda: {
        "email": "user@example.com",
        "account_id": "acct",
        "access_token": "at",
        "refresh_token": "rt",
        "id_token": "id",
    }
    monkeypatch.setattr(
        "platforms.chatgpt.register.submit_callback_url",
        lambda **kwargs: pytest.fail("browser token must not be exchanged again"),
    )

    result = engine.login_existing_via_codex_auth(
        password="Secret123!",
        totp_secret="JBSWY3DPEHPK3PXP",
    )

    assert result.success is True
    assert result.access_token == "at"
    assert result.refresh_token == "rt"


def test_existing_login_accepts_browser_oauth_token_without_callback_exchange(monkeypatch):
    engine = object.__new__(RegistrationEngine)
    engine.logs = []
    engine.email = "user@example.com"
    engine.password = "Secret123!"
    engine.email_info = {"email": "user@example.com"}
    engine.email_service = SimpleNamespace(service_type=SimpleNamespace(value="local_ms_pool"))
    engine.proxy_url = None
    engine._last_codex_error = ""
    engine._is_existing_account = False
    engine._codex_direct_token_info = {
        "email": "user@example.com",
        "account_id": "acct",
        "access_token": "at",
        "refresh_token": "rt",
        "id_token": "id",
    }
    engine._log = lambda message, level="info": None
    engine._preflight_codex_auth_network = lambda: (True, "ok")
    engine._debug_log = lambda message, level="info": None
    engine._acquire_codex_callback = lambda: "browser-token-ready"
    monkeypatch.setattr(
        "platforms.chatgpt.register.submit_callback_url",
        lambda **kwargs: pytest.fail("browser token must not be exchanged again"),
    )

    result = engine.login_existing_via_codex_auth()

    assert result.success is True
    assert result.access_token == "at"
    assert result.refresh_token == "rt"


def test_protocol_mailbox_mapper_rejects_partial_oauth_result():
    platform = object.__new__(ChatGPTPlatform)
    platform.mailbox = None
    platform.config = RegisterConfig()
    adapter = ChatGPTPlatform.build_protocol_mailbox_adapter(platform)
    ctx = SimpleNamespace(password="Secret123!", proxy=None, log=lambda message: None)
    result = SimpleNamespace(
        email="user@example.com",
        password="Secret123!",
        account_id="acct_123",
        access_token="at_123",
        refresh_token="",
        id_token="",
        session_token="sess_123",
        workspace_id="",
    )

    with pytest.raises(RuntimeError, match=r"OAuth .*refresh_token"):
        adapter.result_mapper(ctx, result)


def test_protocol_mailbox_worker_passes_password_and_mfa_credentials():
    account = SimpleNamespace(
        email="user@example.com",
        account_id="user@example.com",
        extra={
            "provider_account": {
                "provider_name": "local_mail_pool",
                "credentials": {
                    "password": "Secret123!",
                    "totp_secret": "JBSWY3DPEHPK3PXP",
                },
            },
            "provider_resource": {"provider_name": "local_mail_pool"},
        },
    )
    mailbox = SimpleNamespace(release_email=lambda *args, **kwargs: False)
    worker = ChatGPTProtocolMailboxWorker(
        mailbox=mailbox,
        mailbox_account=account,
        provider="local_mail_pool",
    )
    captured = {}

    def login_existing(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(success=True)

    worker.engine.login_existing_via_codex_auth = login_existing

    result = worker.run(email=account.email, password="generated-password")

    assert result.success is True
    assert captured == {
        "email": "user@example.com",
        "password": "Secret123!",
        "totp_secret": "JBSWY3DPEHPK3PXP",
    }


def test_browser_register_run_rejects_session_fallback(monkeypatch):
    class FakePage:
        def __init__(self):
            self.url = "about:blank"
            self.context = SimpleNamespace(cookies=lambda: [])

        def goto(self, url, **kwargs):
            self.url = url

    class FakeBrowser:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def new_page(self):
            return FakePage()

    monkeypatch.setattr(browser_register_module, "Camoufox", lambda **kwargs: FakeBrowser())
    monkeypatch.setattr(browser_register_module, "_browser_registration_flow", lambda *args, **kwargs: {"page_type": "chatgpt_home"})
    monkeypatch.setattr(browser_register_module, "_click_first", lambda page, selectors, timeout=3: setattr(page, "url", "https://auth.openai.com/log-in") or selectors[0])
    monkeypatch.setattr(browser_register_module, "_get_cookies", lambda page: {})
    monkeypatch.setattr(browser_register_module, "_do_codex_oauth", lambda *args, **kwargs: None)
    monkeypatch.setattr(browser_register_module.ChatGPTBrowserRegister, "_retry_oauth_fresh_browser", lambda self, email, password: None)
    monkeypatch.setattr(browser_register_module.time, "sleep", lambda seconds: None)

    worker = browser_register_module.ChatGPTBrowserRegister(
        headless=True,
        proxy=None,
        otp_callback=None,
        log_fn=lambda message: None,
    )

    with pytest.raises(RuntimeError, match="已拒绝回退"):
        worker.run(email="user@example.com", password="Secret123!")
