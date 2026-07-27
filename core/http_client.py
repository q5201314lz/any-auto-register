"""通用 HTTP 客户端 - 基于 curl_cffi，支持代理、重试、会话管理"""
"""
HTTP 客户端封装
基于 curl_cffi 的 HTTP 请求封装，支持代理和错误处理
"""

import os
import subprocess
import sys
import time
import json
from functools import lru_cache
from typing import Optional, Dict, Any, Union, Tuple
from dataclasses import dataclass
import logging

from curl_cffi import requests as cffi_requests
from curl_cffi.requests import Session, Response





logger = logging.getLogger(__name__)


@dataclass
class RequestConfig:
    """HTTP 请求配置"""
    timeout: int = 30
    max_retries: int = 3
    retry_delay: float = 1.0
    impersonate: str = "chrome136"
    verify_ssl: bool = True
    follow_redirects: bool = True


class HTTPClientError(Exception):
    """HTTP 客户端异常"""
    pass


def _is_curl_tls_connect_error(exc: BaseException) -> bool:
    text = str(exc or "").lower()
    return (
        "curl: (35)" in text
        or "curl: (28)" in text
        or "curl: (56)" in text
        or "tls connect error" in text
        or "ssl connection timeout" in text
        or "connection timed out" in text
        or "connection closed abruptly" in text
        or "openssl_internal:invalid library" in text
        or "invalid library (0)" in text
    )


def _normalize_proxy_url(proxy: Optional[str]) -> Optional[str]:
    value = str(proxy or "").strip()
    if not value:
        return None
    if "://" not in value:
        value = f"http://{value}"
    return value


@lru_cache(maxsize=1)
def _system_proxy_url() -> Optional[str]:
    """Return the active OS/env proxy for outbound HTTPS requests.

    macOS system proxy is not exposed through environment variables to services
    started from the app.  Without this, OpenAI domains can resolve to fake-ip
    addresses and curl_cffi fails with curl (35) TLS connect errors.
    """
    if str(os.environ.get("ACCOUNT_MANAGER_DISABLE_SYSTEM_PROXY") or "").lower() in {"1", "true", "yes", "on"}:
        return None

    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        proxy = _normalize_proxy_url(os.environ.get(key))
        if proxy:
            return proxy

    if sys.platform != "darwin":
        return None

    try:
        out = subprocess.check_output(["scutil", "--proxy"], text=True, timeout=2)
    except Exception:
        return None

    data: dict[str, str] = {}
    for line in out.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip()

    for prefix in ("HTTPS", "HTTP"):
        if data.get(f"{prefix}Enable") != "1":
            continue
        host = data.get(f"{prefix}Proxy")
        port = data.get(f"{prefix}Port")
        if host and port:
            return f"http://{host}:{port}"
    return None


def resolve_proxy_url(proxy_url: Optional[str]) -> Optional[str]:
    """Use explicit proxy first, otherwise inherit OS/env proxy."""
    return _normalize_proxy_url(proxy_url) or _system_proxy_url()


class HTTPClient:
    """
    HTTP 客户端封装
    支持代理、重试、错误处理和会话管理
    """

    def __init__(
        self,
        proxy_url: Optional[str] = None,
        config: Optional[RequestConfig] = None,
        session: Optional[Session] = None
    ):
        """
        初始化 HTTP 客户端

        Args:
            proxy_url: 代理 URL，如 "http://127.0.0.1:7890"
            config: 请求配置
            session: 可重用的会话对象
        """
        self.proxy_url = resolve_proxy_url(proxy_url)
        self.config = config or RequestConfig()
        self._session = session

    @property
    def proxies(self) -> Optional[Dict[str, str]]:
        """获取代理配置"""
        if not self.proxy_url:
            return None
        return {
            "http": self.proxy_url,
            "https": self.proxy_url,
        }

    @property
    def session(self) -> Session:
        """获取会话对象（单例）"""
        if self._session is None:
            self._session = Session(
                proxies=self.proxies,
                impersonate=self.config.impersonate,
                verify=self.config.verify_ssl,
                timeout=self.config.timeout
            )
        return self._session

    def request(
        self,
        method: str,
        url: str,
        **kwargs
    ) -> Response:
        """
        发送 HTTP 请求

        Args:
            method: HTTP 方法 (GET, POST, PUT, DELETE, etc.)
            url: 请求 URL
            **kwargs: 其他请求参数

        Returns:
            Response 对象

        Raises:
            HTTPClientError: 请求失败
        """
        # 设置默认参数
        kwargs.setdefault("timeout", self.config.timeout)
        kwargs.setdefault("allow_redirects", self.config.follow_redirects)

        # 添加代理配置
        if self.proxies and "proxies" not in kwargs:
            kwargs["proxies"] = self.proxies

        last_exception = None
        for attempt in range(self.config.max_retries):
            try:
                response = self.session.request(method, url, **kwargs)

                # 检查响应状态码
                if response.status_code >= 400:
                    logger.warning(
                        f"HTTP {response.status_code} for {method} {url}"
                        f" (attempt {attempt + 1}/{self.config.max_retries})"
                    )

                    # 如果是服务器错误，重试
                    if response.status_code >= 500 and attempt < self.config.max_retries - 1:
                        time.sleep(self.config.retry_delay * (attempt + 1))
                        continue

                return response

            except (cffi_requests.RequestsError, ConnectionError, TimeoutError) as e:
                last_exception = e
                if _is_curl_tls_connect_error(e):
                    self._session = None
                    fallback_response = self._request_with_tls_fallback(method, url, kwargs)
                    if fallback_response is not None:
                        return fallback_response
                logger.warning(
                    f"请求失败: {method} {url} (attempt {attempt + 1}/{self.config.max_retries}): {e}"
                )

                if attempt < self.config.max_retries - 1:
                    time.sleep(self.config.retry_delay * (attempt + 1))
                else:
                    break

        raise HTTPClientError(
            f"请求失败，最大重试次数已达: {method} {url} - {last_exception}"
        )

    def _request_with_tls_fallback(
        self,
        method: str,
        url: str,
        kwargs: dict[str, Any],
    ) -> Optional[Response]:
        """Retry curl TLS connect failures with a fresh session and safer TLS profiles."""
        fallback_profiles = ["chrome120", "chrome110"]
        proxy_candidates: list[Optional[Dict[str, str]]] = [self.proxies]
        if self.proxies:
            # If the proxy itself triggers curl/OpenSSL TLS failures, allow one
            # direct fallback so the task does not waste the reserved mailbox.
            proxy_candidates.append(None)

        for proxies in proxy_candidates:
            for impersonate in fallback_profiles:
                try:
                    request_kwargs = dict(kwargs)
                    request_kwargs["proxies"] = proxies
                    request_kwargs.setdefault("timeout", self.config.timeout)
                    request_kwargs.setdefault("allow_redirects", self.config.follow_redirects)
                    session = Session(
                        proxies=proxies,
                        impersonate=impersonate,
                        verify=self.config.verify_ssl,
                        timeout=self.config.timeout,
                    )
                    return session.request(method, url, **request_kwargs)
                except (cffi_requests.RequestsError, ConnectionError, TimeoutError) as exc:
                    if _is_curl_tls_connect_error(exc):
                        continue
                    raise
        return None

    def get(self, url: str, **kwargs) -> Response:
        """发送 GET 请求"""
        return self.request("GET", url, **kwargs)

    def post(self, url: str, data: Any = None, json: Any = None, **kwargs) -> Response:
        """发送 POST 请求"""
        return self.request("POST", url, data=data, json=json, **kwargs)

    def put(self, url: str, data: Any = None, json: Any = None, **kwargs) -> Response:
        """发送 PUT 请求"""
        return self.request("PUT", url, data=data, json=json, **kwargs)

    def delete(self, url: str, **kwargs) -> Response:
        """发送 DELETE 请求"""
        return self.request("DELETE", url, **kwargs)

    def head(self, url: str, **kwargs) -> Response:
        """发送 HEAD 请求"""
        return self.request("HEAD", url, **kwargs)

    def options(self, url: str, **kwargs) -> Response:
        """发送 OPTIONS 请求"""
        return self.request("OPTIONS", url, **kwargs)

    def patch(self, url: str, data: Any = None, json: Any = None, **kwargs) -> Response:
        """发送 PATCH 请求"""
        return self.request("PATCH", url, data=data, json=json, **kwargs)

    def download_file(self, url: str, filepath: str, chunk_size: int = 8192) -> None:
        """
        下载文件

        Args:
            url: 文件 URL
            filepath: 保存路径
            chunk_size: 块大小

        Raises:
            HTTPClientError: 下载失败
        """
        try:
            response = self.get(url, stream=True)
            response.raise_for_status()

            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)

        except Exception as e:
            raise HTTPClientError(f"下载文件失败: {url} - {e}")

    def check_proxy(self, test_url: str = "https://httpbin.org/ip") -> bool:
        """
        检查代理是否可用

        Args:
            test_url: 测试 URL

        Returns:
            bool: 代理是否可用
        """
        if not self.proxy_url:
            return False

        try:
            response = self.get(test_url, timeout=10)
            return response.status_code == 200
        except Exception:
            return False

    def close(self):
        """关闭会话"""
        if self._session:
            self._session.close()
            self._session = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
