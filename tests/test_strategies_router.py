"""
Tests for server/routers/strategies.py — CRUD + anchor validation.

Coverage:
  - GET /api/v1/strategies (list)
  - GET /api/v1/strategies/{id} (200 / 404)
  - POST /api/v1/strategies (201, topic_id or manual_input_hint)
  - POST /api/v1/strategies 无 topic_id 且无 manual_input_hint → 422
  - PUT /api/v1/strategies/{id} (200 / 409 RevMismatch / 404)
  - DELETE /api/v1/strategies/{id} (200 / 404)
  - tenant isolation
  - body tenant_id → 422
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("JWT_SECRET", "test_secret_for_pytest_only_not_for_prod")
os.environ.setdefault("JWT_ALGORITHM", "HS256")

from security.jwt import encode_token
from server.main import app
from server.middleware.idempotency import clear_idempotency_caches_for_tests
from storage.base import RevMismatch

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
JWT_A = encode_token(TENANT_A)
JWT_B = encode_token(TENANT_B)
HEADER_A = {"Authorization": f"Bearer {JWT_A}"}
HEADER_B = {"Authorization": f"Bearer {JWT_B}"}


class MockStrategiesBackend:
    """In-memory mock for content_strategies storage operations."""

    def __init__(self) -> None:
        self.strategies: dict[str, dict] = {}

    def create_strategy(
        self,
        tenant_id: str,
        topic_id: str | None = None,
        manual_input_hint: str | None = None,
        target_reader: str | None = None,
        funnel_stage: str | None = None,
        angle: str | None = None,
        hook: str | None = None,
        key_points: list[dict] | None = None,
        cta: str | None = None,
        avoid_points: list[dict] | None = None,
        evidence_refs: list[dict] | None = None,
        memory_refs: list[dict] | None = None,
        knowledge_refs: list[dict] | None = None,
    ) -> dict:
        strategy_id = f"s_{uuid.uuid4().hex[:8]}"
        now = "2026-05-27T00:00:00Z"
        rec: dict[str, Any] = {
            "strategy_id": strategy_id,
            "tenant_id": tenant_id,
            "topic_id": topic_id,
            "manual_input_hint": manual_input_hint,
            "target_reader": target_reader,
            "funnel_stage": funnel_stage,
            "angle": angle,
            "hook": hook,
            "key_points": key_points or [],
            "cta": cta,
            "avoid_points": avoid_points or [],
            "evidence_refs": evidence_refs or [],
            "memory_refs": memory_refs or [],
            "knowledge_refs": knowledge_refs or [],
            "created_by": "user",
            "rev": 1,
            "created_at": now,
            "updated_at": now,
        }
        self.strategies[strategy_id] = rec
        return rec

    def get_strategy(self, tenant_id: str, strategy_id: str) -> dict:
        rec = self.strategies.get(strategy_id)
        if rec is None or rec["tenant_id"] != tenant_id:
            raise KeyError(strategy_id)
        return rec

    def list_strategies(
        self,
        tenant_id: str,
        topic_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
        sort: str = "-created_at",
    ) -> dict:
        items = [s for s in self.strategies.values() if s["tenant_id"] == tenant_id]
        if topic_id:
            items = [s for s in items if s.get("topic_id") == topic_id]
        total = len(items)
        start = (page - 1) * page_size
        end = start + page_size
        return {
            "items": items[start:end],
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_more": end < total,
        }

    def update_strategy(
        self,
        tenant_id: str,
        strategy_id: str,
        expected_rev: int,
        **changes: Any,
    ) -> dict:
        rec = self.strategies.get(strategy_id)
        if rec is None or rec["tenant_id"] != tenant_id:
            raise KeyError(strategy_id)
        if rec["rev"] != expected_rev:
            raise RevMismatch()
        rec.update(changes)
        rec["rev"] += 1
        rec["updated_at"] = "2026-05-27T01:00:00Z"
        return rec

    def delete_strategy(self, tenant_id: str, strategy_id: str) -> None:
        rec = self.strategies.get(strategy_id)
        if rec is None or rec["tenant_id"] != tenant_id:
            raise KeyError(strategy_id)
        del self.strategies[strategy_id]


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    clear_idempotency_caches_for_tests()


@pytest.fixture
def mock_backend(monkeypatch: pytest.MonkeyPatch) -> MockStrategiesBackend:
    backend = MockStrategiesBackend()
    import storage.factory as sf
    monkeypatch.setattr(sf, "get_backend", lambda: backend)
    return backend


def _ik(suffix: str = "") -> str:
    return f"strat-{suffix or uuid.uuid4().hex}"


async def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ── Tests ───────────────────────────────────────────────────────────────────


class TestStrategiesRouter:

    async def test_list_empty(self, mock_backend: MockStrategiesBackend) -> None:
        async with await _client() as ac:
            resp = await ac.get("/api/v1/strategies", headers=HEADER_A)
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    async def test_list_with_items(self, mock_backend: MockStrategiesBackend) -> None:
        mock_backend.create_strategy(TENANT_A, topic_id="t_001")
        mock_backend.create_strategy(TENANT_A, manual_input_hint="Hint A")
        async with await _client() as ac:
            resp = await ac.get("/api/v1/strategies", headers=HEADER_A)
        assert resp.status_code == 200
        assert resp.json()["total"] == 2

    async def test_list_filter_by_topic(self, mock_backend: MockStrategiesBackend) -> None:
        mock_backend.create_strategy(TENANT_A, topic_id="t_001")
        mock_backend.create_strategy(TENANT_A, topic_id="t_002")
        mock_backend.create_strategy(TENANT_A, manual_input_hint="No topic")
        async with await _client() as ac:
            resp = await ac.get("/api/v1/strategies?topic_id=t_001", headers=HEADER_A)
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    async def test_get_by_id_200(self, mock_backend: MockStrategiesBackend) -> None:
        rec = mock_backend.create_strategy(TENANT_A, topic_id="t_001")
        async with await _client() as ac:
            resp = await ac.get(f"/api/v1/strategies/{rec['strategy_id']}", headers=HEADER_A)
        assert resp.status_code == 200
        assert resp.json()["strategy_id"] == rec["strategy_id"]

    async def test_get_by_id_404(self, mock_backend: MockStrategiesBackend) -> None:
        async with await _client() as ac:
            resp = await ac.get("/api/v1/strategies/nonexistent", headers=HEADER_A)
        assert resp.status_code == 404

    async def test_create_with_topic_id_201(self, mock_backend: MockStrategiesBackend) -> None:
        ik = _ik("create-topic")
        async with await _client() as ac:
            resp = await ac.post(
                "/api/v1/strategies",
                json={"topic_id": "t_001", "target_reader": "Factory owners"},
                headers={**HEADER_A, "Idempotency-Key": ik},
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["topic_id"] == "t_001"
        assert body["target_reader"] == "Factory owners"
        assert body["rev"] == 1

    async def test_create_with_manual_input_hint_201(self, mock_backend: MockStrategiesBackend) -> None:
        ik = _ik("create-hint")
        async with await _client() as ac:
            resp = await ac.post(
                "/api/v1/strategies",
                json={"manual_input_hint": "User provided strategy hints"},
                headers={**HEADER_A, "Idempotency-Key": ik},
            )
        assert resp.status_code == 201
        assert resp.json()["manual_input_hint"] == "User provided strategy hints"

    async def test_create_422_when_missing_anchor(self, mock_backend: MockStrategiesBackend) -> None:
        ik = _ik("create-no-anchor")
        async with await _client() as ac:
            resp = await ac.post(
                "/api/v1/strategies",
                json={"target_reader": "Nobody"},
                headers={**HEADER_A, "Idempotency-Key": ik},
            )
        assert resp.status_code == 422
        body = resp.json()
        assert body["error"]["code"] == "strategy_missing_anchor"
        assert body["error"]["field"] == "topic_id"

    async def test_update_200(self, mock_backend: MockStrategiesBackend) -> None:
        rec = mock_backend.create_strategy(TENANT_A, topic_id="t_001")
        ik = _ik("update")
        async with await _client() as ac:
            resp = await ac.put(
                f"/api/v1/strategies/{rec['strategy_id']}",
                json={"hook": "New hook text", "rev": 1},
                headers={**HEADER_A, "Idempotency-Key": ik},
            )
        assert resp.status_code == 200
        assert resp.json()["hook"] == "New hook text"
        assert resp.json()["rev"] == 2

    async def test_update_409(self, mock_backend: MockStrategiesBackend) -> None:
        rec = mock_backend.create_strategy(TENANT_A, topic_id="t_001")
        ik = _ik("update-409")
        async with await _client() as ac:
            resp = await ac.put(
                f"/api/v1/strategies/{rec['strategy_id']}",
                json={"angle": "Stale angle", "rev": 99},
                headers={**HEADER_A, "Idempotency-Key": ik},
            )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "rev_mismatch"

    async def test_update_404(self, mock_backend: MockStrategiesBackend) -> None:
        ik = _ik("update-404")
        async with await _client() as ac:
            resp = await ac.put(
                "/api/v1/strategies/nonexistent",
                json={"hook": "Nope", "rev": 1},
                headers={**HEADER_A, "Idempotency-Key": ik},
            )
        assert resp.status_code == 404

    async def test_delete_200(self, mock_backend: MockStrategiesBackend) -> None:
        rec = mock_backend.create_strategy(TENANT_A, topic_id="t_001")
        ik = _ik("delete")
        async with await _client() as ac:
            resp = await ac.delete(
                f"/api/v1/strategies/{rec['strategy_id']}",
                headers={**HEADER_A, "Idempotency-Key": ik},
            )
        assert resp.status_code == 200
        assert resp.json() == {"deleted": True, "strategy_id": rec["strategy_id"]}

    async def test_delete_404(self, mock_backend: MockStrategiesBackend) -> None:
        ik = _ik("delete-404")
        async with await _client() as ac:
            resp = await ac.delete(
                "/api/v1/strategies/nonexistent",
                headers={**HEADER_A, "Idempotency-Key": ik},
            )
        assert resp.status_code == 404

    async def test_tenant_isolation(self, mock_backend: MockStrategiesBackend) -> None:
        mock_backend.create_strategy(TENANT_A, topic_id="t_001")
        async with await _client() as ac:
            resp = await ac.get("/api/v1/strategies", headers=HEADER_B)
        assert resp.json()["total"] == 0

    async def test_rejects_tenant_id_in_body(self, mock_backend: MockStrategiesBackend) -> None:
        ik = _ik("no-tenant")
        async with await _client() as ac:
            resp = await ac.post(
                "/api/v1/strategies",
                json={"topic_id": "t_001", "tenant_id": "hack"},
                headers={**HEADER_A, "Idempotency-Key": ik},
            )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "tenant_in_body_forbidden"
