"""
Phase 4a · HS256 JWT 编解码。

每次函数调用重新读 os.environ，不做模块级缓存。
PyJWT 2.x 的 encode 返回 str（不是 bytes），不用 .decode()。
"""
from __future__ import annotations

import os
import time
from typing import TypedDict

import jwt as _jwt
from jwt import ExpiredSignatureError, InvalidSignatureError, InvalidTokenError, PyJWTError


class TokenPayload(TypedDict):
    sub: str
    is_admin: bool
    iat: int
    exp: int


def _load_secret() -> tuple[str, str, int]:
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise EnvironmentError("JWT_SECRET not set; check ~/.spider_xhs/.env")
    algorithm = os.environ.get("JWT_ALGORITHM", "HS256")
    ttl_hours = int(os.environ.get("JWT_TTL_HOURS", "24"))
    return secret, algorithm, ttl_hours


def encode_token(
    tenant_id: str, *, is_admin: bool = False, ttl_seconds: int | None = None
) -> str:
    """
    签 JWT。

    ttl_seconds 给定则覆盖 env；不给读 JWT_TTL_HOURS，默认 24h。
    ttl_seconds=-1 可签出立即过期的 token（测试用）。
    """
    secret, algorithm, default_ttl = _load_secret()
    if ttl_seconds is None:
        ttl_seconds = default_ttl * 3600

    now = int(time.time())
    payload: dict = {
        "sub": str(tenant_id),
        "is_admin": is_admin,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return _jwt.encode(payload, secret, algorithm=algorithm)


def decode_token(token: str) -> TokenPayload:
    """解码 + 校验签名 + 校验 exp。失败抛 PyJWT 异常，不吞。"""
    secret, algorithm, _ = _load_secret()
    payload = _jwt.decode(token, secret, algorithms=[algorithm])
    return TokenPayload(
        sub=str(payload["sub"]),
        is_admin=bool(payload.get("is_admin", False)),
        iat=int(payload["iat"]),
        exp=int(payload["exp"]),
    )
