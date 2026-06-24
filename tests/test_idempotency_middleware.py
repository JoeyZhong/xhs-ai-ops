from __future__ import annotations

import os

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from security.jwt import encode_token
from server.auth import AuthContext, verify_token


os.environ.setdefault("JWT_SECRET", "test-jwt-secret-for-pytest-only")
os.environ.setdefault("JWT_ALGORITHM", "HS256")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from agent_tools import idempotency as idempotency_backend
    from server.middleware.idempotency import (
        IdempotencyRoute,
        clear_idempotency_caches_for_tests,
    )

    monkeypatch.setattr(idempotency_backend, "_IDEMPOT_DIR", tmp_path / "idempot")
    clear_idempotency_caches_for_tests()

    app = FastAPI()
    router = APIRouter(route_class=IdempotencyRoute)
    calls = {"count": 0}

    @router.post("/api/v1/test/write")
    async def write_endpoint(
        payload: dict,
        auth: AuthContext = Depends(verify_token),
    ) -> dict:
        calls["count"] += 1
        return {
            "calls": calls["count"],
            "payload": payload,
            "tenant_id": auth.tenant_id,
        }

    app.include_router(router)
    app.state.calls = calls

    with TestClient(app) as test_client:
        yield test_client


def _headers(key: str | None = "idem-key-1") -> dict[str, str]:
    headers = {"Authorization": f"Bearer {encode_token('tenant-a')}"}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def test_write_endpoint_missing_idempotency_key_returns_428(client):
    response = client.post(
        "/api/v1/test/write",
        json={"topic": "A"},
        headers=_headers(key=None),
    )

    assert response.status_code == 428
    assert response.json()["error"]["code"] == "missing_idempotency_key"
    assert response.json()["error"]["request_id"]
    assert client.app.state.calls["count"] == 0


def test_same_key_and_payload_replays_cached_response(client):
    first = client.post(
        "/api/v1/test/write?b=2&a=1",
        json={"topic": "A", "count": 1},
        headers=_headers("idem-key-replay"),
    )
    second = client.post(
        "/api/v1/test/write?a=1&b=2",
        json={"count": 1, "topic": "A"},
        headers=_headers("idem-key-replay"),
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()
    assert first.json()["calls"] == 1
    assert client.app.state.calls["count"] == 1


def test_same_key_with_different_payload_returns_409(client):
    first = client.post(
        "/api/v1/test/write",
        json={"topic": "A"},
        headers=_headers("idem-key-conflict"),
    )
    second = client.post(
        "/api/v1/test/write",
        json={"topic": "B"},
        headers=_headers("idem-key-conflict"),
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "idempotency_conflict"
    assert second.json()["error"]["request_id"]
    assert client.app.state.calls["count"] == 1
