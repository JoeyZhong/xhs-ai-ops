"""Phase 4a · §A3.3 JWT 单元测试（7 cases）。"""
from __future__ import annotations

import time

import pytest
from jwt import ExpiredSignatureError, InvalidSignatureError

from security.jwt import TokenPayload, decode_token, encode_token


@pytest.fixture(autouse=True)
def _set_jwt_env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test_secret_for_pytest_only_not_for_prod")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("JWT_TTL_HOURS", "24")


class TestJwt:
    def test_encode_decode_roundtrip(self):
        token = encode_token("tenant-abc-123")
        payload = decode_token(token)
        assert payload["sub"] == "tenant-abc-123"
        assert payload["is_admin"] is False
        assert isinstance(payload["iat"], int)
        assert isinstance(payload["exp"], int)
        assert payload["exp"] - payload["iat"] == 86400

    def test_admin_flag(self):
        token = encode_token("admin-tenant", is_admin=True)
        payload = decode_token(token)
        assert payload["sub"] == "admin-tenant"
        assert payload["is_admin"] is True

    def test_expired_token(self):
        token = encode_token("ephemeral", ttl_seconds=-1)
        time.sleep(0.1)
        with pytest.raises(ExpiredSignatureError):
            decode_token(token)

    def test_tampered_token(self):
        token = encode_token("tamper-me")
        last = token[-1]
        replacement = "b" if last == "a" else "a"
        tampered = token[:-1] + replacement
        if tampered == token:
            tampered = token[:-1] + ("c" if replacement == "b" else "b")
        with pytest.raises(InvalidSignatureError):
            decode_token(tampered)

    def test_missing_secret(self, monkeypatch):
        monkeypatch.delenv("JWT_SECRET", raising=False)
        with pytest.raises(EnvironmentError, match="JWT_SECRET"):
            encode_token("no-secret")

    def test_default_ttl(self, monkeypatch):
        monkeypatch.delenv("JWT_TTL_HOURS", raising=False)
        token = encode_token("default-ttl")
        payload = decode_token(token)
        assert payload["exp"] - payload["iat"] == 86400

    def test_custom_ttl_via_env(self, monkeypatch):
        monkeypatch.setenv("JWT_TTL_HOURS", "1")
        token = encode_token("short-ttl")
        payload = decode_token(token)
        assert payload["exp"] - payload["iat"] == 3600
