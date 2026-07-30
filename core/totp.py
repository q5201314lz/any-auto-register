"""Small RFC 6238 TOTP helper used by account login flows."""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time


def normalize_totp_secret(secret: str) -> str:
    return "".join(str(secret or "").strip().upper().replace("-", "").split())


def generate_totp(
    secret: str,
    *,
    timestamp: float | None = None,
    period: int = 30,
    digits: int = 6,
) -> str:
    normalized = normalize_totp_secret(secret)
    if not normalized:
        raise ValueError("MFA 密钥为空")
    if period <= 0 or digits <= 0:
        raise ValueError("TOTP 参数无效")

    padding = "=" * ((8 - len(normalized) % 8) % 8)
    try:
        key = base64.b32decode(normalized + padding, casefold=True)
    except Exception as exc:
        raise ValueError("MFA 密钥不是有效的 Base32") from exc
    if not key:
        raise ValueError("MFA 密钥为空")

    counter = int((time.time() if timestamp is None else timestamp) // period)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** digits)).zfill(digits)
