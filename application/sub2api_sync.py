"""Upload saved ChatGPT accounts to a configured Sub2API instance."""
from __future__ import annotations

import logging
from typing import Any, Callable

import requests

from application.account_exports import _make_sub2api_json
from infrastructure.accounts_repository import AccountsRepository


logger = logging.getLogger(__name__)


class Sub2ApiClient:
    def __init__(self, base_url: str, api_key: str, *, timeout: int = 30):
        root = str(base_url or "").strip().rstrip("/")
        self.api_base = root if root.endswith("/api/v1") else f"{root}/api/v1"
        self.api_key = str(api_key or "").strip()
        self.timeout = timeout

    def import_data(self, data: dict[str, Any]) -> tuple[bool, str]:
        if not self.api_base or self.api_base == "/api/v1":
            return False, "Sub2API URL 未配置"
        if not self.api_key:
            return False, "Sub2API 管理密钥未配置"

        try:
            response = requests.post(
                f"{self.api_base}/admin/accounts/data",
                headers={
                    "X-API-Key": self.api_key,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json={"data": data, "skip_default_group_bind": True},
                timeout=self.timeout,
            )
        except Exception as exc:
            return False, f"请求异常: {exc}"

        try:
            payload = response.json()
        except Exception:
            payload = {}
        if response.status_code not in (200, 201):
            detail = payload.get("message") if isinstance(payload, dict) else ""
            return False, detail or f"HTTP {response.status_code}: {response.text[:200]}"
        if isinstance(payload, dict) and payload.get("code") not in (None, 0):
            return False, str(payload.get("message") or f"接口错误: {payload.get('code')}")

        result = payload.get("data", payload) if isinstance(payload, dict) else {}
        failed = int((result or {}).get("account_failed") or 0)
        created = int((result or {}).get("account_created") or 0)
        if failed:
            return False, f"导入失败 {failed} 个账号"
        return True, f"同步成功（新增 {created}）"


def _get_sub2api_config() -> tuple[bool, str, str]:
    try:
        from core.config_store import config_store

        base_url = config_store.get("sub2api_url", "").strip()
        api_key = config_store.get("sub2api_api_key", "").strip()
        enabled_value = config_store.get("sub2api_enabled", "").strip().lower()
        # Preserve the behavior of existing installations that already configured
        # Sub2API before the explicit enable switch was introduced.
        enabled = (
            enabled_value in {"1", "true", "yes", "on"}
            if enabled_value
            else bool(base_url and api_key)
        )
        return enabled, base_url, api_key
    except Exception:
        return False, "", ""


def push_saved_account_to_sub2api(
    account_id: int,
    *,
    log_fn: Callable[..., None] | None = None,
) -> bool:
    """Push one saved ChatGPT account and report every upload state."""
    log = log_fn or logger.info

    def emit(message: str, *, level: str = "info") -> None:
        try:
            log(message, level=level)
        except TypeError:
            log(message)

    enabled, base_url, api_key = _get_sub2api_config()
    if not enabled:
        emit("  [Sub2API] 未启用，跳过自动上传")
        return False
    if not base_url or not api_key:
        emit("  [Sub2API] 配置不完整，跳过自动上传", level="warning")
        return False

    account = AccountsRepository().get(int(account_id))
    if account is None:
        emit(f"  [Sub2API] 未找到账号 ID={account_id}，跳过自动上传", level="warning")
        return False
    if account.platform != "chatgpt":
        emit(f"  [Sub2API] {account.email} 不是 ChatGPT 账号，跳过自动上传")
        return False

    data = _make_sub2api_json(account)
    credentials = ((data.get("accounts") or [{}])[0].get("credentials") or {})
    if not credentials.get("access_token") or not credentials.get("refresh_token"):
        emit("  [Sub2API] 缺少 access_token 或 refresh_token，已跳过", level="warning")
        return False

    emit(f"  [Sub2API] 开始上传: {account.email} -> {base_url.rstrip('/')}")
    ok, message = Sub2ApiClient(base_url, api_key).import_data(data)
    if ok:
        emit(f"  [Sub2API] ✓ {account.email} {message}")
    else:
        emit(f"  [Sub2API] ✗ {account.email} {message}", level="warning")
    return ok
