from __future__ import annotations

import pytest

from core.http_client import _is_curl_tls_connect_error
from platforms.chatgpt.http_client import OpenAIHTTPClient


@pytest.mark.parametrize(
    "message",
    [
        "curl: (28) Connection timed out after 12001 milliseconds",
        "curl: (28) SSL connection timeout",
        "curl: (56) Connection closed abruptly",
        "curl: (35) TLS connect error",
        "OpenSSL SSL_connect: SSL_ERROR_SYSCALL",
    ],
)
def test_retryable_openai_connect_errors(message):
    expected = "ssl_error_syscall" not in message.lower()
    assert _is_curl_tls_connect_error(RuntimeError(message)) is expected


def test_non_connect_error_is_not_retried_as_tls_failure():
    assert not _is_curl_tls_connect_error(RuntimeError("HTTP 401 unauthorized"))


def test_openai_client_uses_cloudflare_compatible_profile():
    client = OpenAIHTTPClient(proxy_url="http://127.0.0.1:18080")
    assert client.config.impersonate == "chrome131"
