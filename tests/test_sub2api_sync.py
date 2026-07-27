from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from application.sub2api_sync import Sub2ApiClient, push_saved_account_to_sub2api
from application.tasks import _auto_push_sub2api
from domain.accounts import AccountRecord


def _response(status_code: int, payload: dict) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    response.text = str(payload)
    return response


def _account() -> AccountRecord:
    return AccountRecord(
        id=7,
        platform="chatgpt",
        email="user@example.com",
        password="password",
        user_id="account-id",
        credentials=[
            {"scope": "platform", "key": "access_token", "value": "access-token"},
            {"scope": "platform", "key": "refresh_token", "value": "refresh-token"},
        ],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def test_client_uses_admin_api_key_and_import_endpoint():
    response = _response(200, {
        "code": 0,
        "message": "success",
        "data": {"account_created": 1, "account_failed": 0},
    })
    client = Sub2ApiClient("https://sub.example.com", "admin-key")

    with patch("application.sub2api_sync.requests.post", return_value=response) as post:
        ok, message = client.import_data({"type": "sub2api-data", "accounts": []})

    assert ok is True
    assert "新增 1" in message
    request = post.call_args
    assert request.args[0] == "https://sub.example.com/api/v1/admin/accounts/data"
    assert request.kwargs["headers"]["X-API-Key"] == "admin-key"
    assert request.kwargs["json"]["skip_default_group_bind"] is True


def test_client_reports_api_failure():
    response = _response(401, {"code": "UNAUTHORIZED", "message": "bad key"})
    with patch("application.sub2api_sync.requests.post", return_value=response):
        ok, message = Sub2ApiClient("https://sub.example.com/api/v1", "bad").import_data({})

    assert ok is False
    assert message == "bad key"


def test_push_saved_chatgpt_account_reuses_export_format():
    logs = []
    with (
        patch("application.sub2api_sync._get_sub2api_config", return_value=(True, "https://sub.example.com", "admin-key")),
        patch("application.sub2api_sync.AccountsRepository.get", return_value=_account()),
        patch.object(Sub2ApiClient, "import_data", return_value=(True, "同步成功（新增 1）")) as upload,
    ):
        assert push_saved_account_to_sub2api(7, log_fn=logs.append) is True

    data = upload.call_args.args[0]
    assert data["type"] == "sub2api-data"
    assert data["accounts"][0]["name"] == "user@example.com"
    assert data["accounts"][0]["credentials"]["refresh_token"] == "refresh-token"
    assert any("开始上传: user@example.com -> https://sub.example.com" in line for line in logs)
    assert any("✓ user@example.com 同步成功（新增 1）" in line for line in logs)
    assert all("admin-key" not in line for line in logs)


def test_push_skips_when_not_configured():
    logs = []
    with patch("application.sub2api_sync._get_sub2api_config", return_value=(False, "", "")):
        assert push_saved_account_to_sub2api(7, log_fn=logs.append) is False

    assert logs == ["  [Sub2API] 未启用，跳过自动上传"]


def test_push_reports_upload_failure_without_leaking_key():
    logs = []
    with (
        patch("application.sub2api_sync._get_sub2api_config", return_value=(True, "https://sub.example.com/", "secret-key")),
        patch("application.sub2api_sync.AccountsRepository.get", return_value=_account()),
        patch.object(Sub2ApiClient, "import_data", return_value=(False, "HTTP 503")),
    ):
        assert push_saved_account_to_sub2api(7, log_fn=logs.append) is False

    assert any("开始上传: user@example.com -> https://sub.example.com" in line for line in logs)
    assert any("✗ user@example.com HTTP 503" in line for line in logs)
    assert all("secret-key" not in line for line in logs)


def test_auto_push_exception_does_not_escape_registration_post_processing():
    class DetachedAccount:
        @property
        def id(self):
            raise RuntimeError("detached")

    task_logger = MagicMock()
    _auto_push_sub2api(task_logger, DetachedAccount())

    task_logger.log.assert_called_once()
    assert "自动推送异常: detached" in task_logger.log.call_args.args[0]
    assert task_logger.log.call_args.kwargs["level"] == "warning"
