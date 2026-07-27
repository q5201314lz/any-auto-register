from core.base_platform import Account
from core.db import save_account


def test_save_account_returns_readable_id_after_session_closes():
    saved = save_account(Account(
        platform="chatgpt",
        email="saved@example.com",
        password="password",
        extra={
            "access_token": "access-token",
            "refresh_token": "refresh-token",
        },
    ))

    assert isinstance(saved.id, int)
    assert saved.id > 0
    assert saved.email == "saved@example.com"
