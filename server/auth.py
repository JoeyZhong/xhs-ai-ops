"""JWT Bearer token authentication dependency.

Phase 4a §A3: decode JWT → AuthContext on success, 401 on any failure.
Legacy static token fallback removed in P2.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import ExpiredSignatureError, PyJWTError

from security.jwt import decode_token

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthContext:
    """已验证通过的认证上下文，注入每个需要鉴权的 endpoint。"""
    tenant_id: str = "default"
    is_admin: bool = False


async def verify_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
) -> AuthContext:
    """验证 Bearer JWT token → AuthContext 或 401。"""
    token = request.query_params.get("token")
    if token is None:
        if credentials is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        token = credentials.credentials

    try:
        payload = decode_token(token)
        return AuthContext(
            tenant_id=str(payload["sub"]),
            is_admin=bool(payload.get("is_admin", False)),
        )
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
