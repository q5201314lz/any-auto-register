from __future__ import annotations

from types import SimpleNamespace
import json

from core.local_ms_mailbox import (
    LocalMicrosoftMailboxEntry,
    LocalMicrosoftMailboxPool,
    OUTLOOK_IMAP_SCOPE,
    OUTLOOK_TOKEN_URL,
    parse_xinlan_common_rows,
)


def _entry() -> LocalMicrosoftMailboxEntry:
    return LocalMicrosoftMailboxEntry(
        email="user@outlook.com",
        login_account="user@outlook.com",
        client_id="client-id",
        refresh_token="refresh-token",
    )


def test_three_column_login_mfa_row_ignores_product_description():
    entries = parse_xinlan_common_rows(
        "account@icloud.com----LoginPassword123----JBSWY3DPEHPK3PXP\n"
        "**商品说明**\n"
        "MFA验证码获取地址：https://2fa.fun/index.html"
    )

    assert len(entries) == 1
    assert entries[0].email == "account@icloud.com"
    assert entries[0].password == "LoginPassword123"
    assert entries[0].login_account == "account@icloud.com"
    assert entries[0].totp_secret == "JBSWY3DPEHPK3PXP"
    assert not entries[0].graph_ready
    assert not entries[0].imap_ready


def test_pipe_login_mfa_row_preserves_plus_email_and_password_spaces():
    entries = parse_xinlan_common_rows(
        "x+y@icloud.com| password with spaces |JBSWY3DPEHPK3PXP\n"
        "登录教程：https://example.com"
    )

    assert len(entries) == 1
    assert entries[0].email == "x+y@icloud.com"
    assert entries[0].password == " password with spaces "
    assert entries[0].totp_secret == "JBSWY3DPEHPK3PXP"


def test_pipe_login_mfa_row_uses_first_and_last_separator():
    entries = parse_xinlan_common_rows(
        "account@icloud.com|password|with|pipes|JBSWY3DPEHPK3PXP"
    )

    assert len(entries) == 1
    assert entries[0].password == "password|with|pipes"
    assert entries[0].totp_secret == "JBSWY3DPEHPK3PXP"


def test_three_hyphen_icloud_relay_rows_preserve_email_and_code_url():
    entries = parse_xinlan_common_rows(
        "odds.04alibi+hfi0vu5u890a8x24@icloud.com---"
        "https://mail.20000408.xyz/show/token/odds.04alibi%2Bhfi0vu5u890a8x24@icloud.com\n"
        "frame-squawk4r@icloud.com---"
        "https://mail.20000408.xyz/show/token/frame-squawk4r@icloud.com"
    )

    assert len(entries) == 2
    assert entries[0].email == "odds.04alibi+hfi0vu5u890a8x24@icloud.com"
    assert entries[0].receive_provider == "icloud_api"
    assert entries[0].icloud_api_url == (
        "https://mail.20000408.xyz/show/token/"
        "odds.04alibi%2Bhfi0vu5u890a8x24@icloud.com"
    )
    assert entries[0].icloud_api_ready
    assert entries[1].email == "frame-squawk4r@icloud.com"
    assert entries[1].icloud_api_ready


def test_four_hyphen_icloud_relay_format_remains_supported():
    entries = parse_xinlan_common_rows(
        "account@icloud.com----https://mail.example.com/show/token/account@icloud.com"
    )

    assert len(entries) == 1
    assert entries[0].email == "account@icloud.com"
    assert entries[0].icloud_api_ready


def test_outlook_imap_token_uses_consumers_endpoint_and_imap_scope(monkeypatch):
    captured = {}

    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"access_token": "imap-access-token"}

    def fake_post(url, *, data, proxies, timeout):
        captured.update(url=url, data=data, proxies=proxies, timeout=timeout)
        return Response()

    monkeypatch.setattr("core.local_ms_mailbox.requests.post", fake_post)

    mailbox = LocalMicrosoftMailboxPool()
    token = mailbox._outlook_imap_access_token(_entry())

    assert token == "imap-access-token"
    assert captured["url"] == OUTLOOK_TOKEN_URL
    assert captured["data"]["scope"] == OUTLOOK_IMAP_SCOPE


def test_graph_failure_falls_back_to_imap_and_caches_strategy(monkeypatch):
    mailbox = LocalMicrosoftMailboxPool()
    entry = _entry()
    account = SimpleNamespace(email=entry.email, extra={})
    calls = []

    monkeypatch.setattr(mailbox, "_entry_for_account", lambda _: entry)

    def graph_messages(_):
        calls.append("graph")
        raise RuntimeError("AADSTS70000")

    def imap_messages(_):
        calls.append("imap")
        return [{"id": "message-1"}]

    monkeypatch.setattr(mailbox, "_graph_messages", graph_messages)
    monkeypatch.setattr(mailbox, "_outlook_oauth_imap_messages", imap_messages)

    assert mailbox._messages(account) == [{"id": "message-1"}]
    assert mailbox._messages(account) == [{"id": "message-1"}]
    assert calls == ["graph", "imap", "imap"]


def test_failed_mailbox_is_released_for_immediate_retry(tmp_path):
    state_file = tmp_path / "mailbox-state.json"
    mailbox = LocalMicrosoftMailboxPool(
        pool_text="first@outlook.com----password\nsecond@outlook.com----password",
        state_file=str(state_file),
        failure_cooldown_seconds=1800,
    )

    first = mailbox.get_email()
    assert first.email == "first@outlook.com"
    assert mailbox.release_email(first)

    retried = mailbox.get_email()
    assert retried.email == "first@outlook.com"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert not state.get("cooldowns")
    assert "first@outlook.com" in state["used"]


def test_exhaustive_run_attempts_each_mailbox_once(tmp_path):
    mailbox = LocalMicrosoftMailboxPool(
        pool_text="first@outlook.com----password\nsecond@outlook.com----password",
        state_file=str(tmp_path / "mailbox-state.json"),
        avoid_repeat=True,
    )

    first = mailbox.get_email()
    assert mailbox.release_email(first)
    second = mailbox.get_email()

    assert first.email == "first@outlook.com"
    assert second.email == "second@outlook.com"


def test_available_count_excludes_successfully_reserved_mailboxes(tmp_path):
    mailbox = LocalMicrosoftMailboxPool(
        pool_text="first@outlook.com----password\nsecond@outlook.com----password",
        state_file=str(tmp_path / "mailbox-state.json"),
    )

    assert mailbox.available_count() == 2
    mailbox.get_email()
    assert mailbox.available_count() == 1


def test_expired_failure_cooldown_allows_mailbox_reuse(tmp_path):
    state_file = tmp_path / "mailbox-state.json"
    state_file.write_text(
        json.dumps({
            "used": {},
            "cooldowns": {
                "first@outlook.com": {"cooldown_until": 1},
            },
        }),
        encoding="utf-8",
    )
    mailbox = LocalMicrosoftMailboxPool(
        pool_text="first@outlook.com----password",
        state_file=str(state_file),
        failure_cooldown_seconds=1800,
    )

    assert mailbox.get_email().email == "first@outlook.com"


def test_network_failure_release_does_not_cool_down_mailbox(tmp_path):
    state_file = tmp_path / "mailbox-state.json"
    mailbox = LocalMicrosoftMailboxPool(
        pool_text="first@outlook.com----password",
        state_file=str(state_file),
        failure_cooldown_seconds=1800,
    )

    account = mailbox.get_email()
    assert mailbox.release_email(account, cooldown=False)
    assert mailbox.peek_email() == "first@outlook.com"
    assert not mailbox._state().get("cooldowns")


def test_clear_failure_cooldowns(tmp_path):
    state_file = tmp_path / "mailbox-state.json"
    state_file.write_text(
        json.dumps({
            "used": {},
            "cooldowns": {
                "first@outlook.com": {"cooldown_until": 9999999999},
            },
        }),
        encoding="utf-8",
    )
    mailbox = LocalMicrosoftMailboxPool(
        pool_text="first@outlook.com----password",
        state_file=str(state_file),
        failure_cooldown_seconds=1800,
    )

    assert mailbox.clear_failure_cooldowns(["first@outlook.com"]) == 1
    assert mailbox.peek_email() == "first@outlook.com"


def test_release_unsaved_reservations_keeps_saved_accounts(tmp_path):
    mailbox = LocalMicrosoftMailboxPool(
        pool_text="saved@outlook.com----password\norphan@outlook.com----password",
        state_file=str(tmp_path / "mailbox-state.json"),
    )
    mailbox.get_email()
    mailbox.get_email()

    released = mailbox.release_unsaved_reservations({"SAVED@outlook.com"})

    assert released == ["orphan@outlook.com"]
    assert mailbox.available_count() == 1
    assert "saved@outlook.com" in mailbox._state()["used"]
    assert "orphan@outlook.com" not in mailbox._state()["used"]
