"""
Tests for server/routers/topics_v2.py — 5 endpoints.

Coverage:
  - GET /api/v1/topics (list, pagination, sort)
  - GET /api/v1/topics/{id} (200 / 404)
  - POST /api/v1/topics (201 + rev=1)
  - PUT /api/v1/topics/{id} (200 / 409 RevMismatch / 404)
  - DELETE /api/v1/topics/{id} (200 / 404)
  - tenant_id 跨租户不可见
  - body 里塞 tenant_id → 422
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


# ── Mock backend ────────────────────────────────────────────────────────────


class MockTopicsBackend:
    """In-memory mock that mirrors the storage methods topics_v2 router calls."""

    def __init__(self) -> None:
        self.topics: dict[str, dict] = {}

    def create_topic(
        self,
        tenant_id: str,
        title: str,
        goal_id: str | None = None,
        persona_id: str | None = None,
        angle: str | None = None,
        funnel_stage: str | None = None,
        source: str = "manual",
        source_refs: list[dict] | None = None,
    ) -> dict:
        topic_id = f"t_{uuid.uuid4().hex[:8]}"
        now = "2026-05-27T00:00:00Z"
        rec: dict[str, Any] = {
            "topic_id": topic_id,
            "tenant_id": tenant_id,
            "goal_id": goal_id,
            "persona_id": persona_id,
            "title": title,
            "angle": angle,
            "funnel_stage": funnel_stage,
            "source": source,
            "source_refs": source_refs or [],
            "status": "idea",
            "created_by": "user",
            "rev": 1,
            "created_at": now,
            "updated_at": now,
        }
        self.topics[topic_id] = rec
        return rec

    def get_topic(self, tenant_id: str, topic_id: str) -> dict:
        rec = self.topics.get(topic_id)
        if rec is None or rec["tenant_id"] != tenant_id:
            raise KeyError(topic_id)
        return rec

    def list_topics(
        self,
        tenant_id: str,
        goal_id: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
        sort: str = "-updated_at",
    ) -> dict:
        items = [t for t in self.topics.values() if t["tenant_id"] == tenant_id]
        if goal_id:
            items = [t for t in items if t.get("goal_id") == goal_id]
        if status:
            items = [t for t in items if t.get("status") == status]
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

    def update_topic(
        self,
        tenant_id: str,
        topic_id: str,
        expected_rev: int,
        **changes: Any,
    ) -> dict:
        rec = self.topics.get(topic_id)
        if rec is None or rec["tenant_id"] != tenant_id:
            raise KeyError(topic_id)
        if rec["rev"] != expected_rev:
            raise RevMismatch()
        rec.update(changes)
        rec["rev"] += 1
        rec["updated_at"] = "2026-05-27T01:00:00Z"
        return rec

    def delete_topic(
        self,
        tenant_id: str,
        topic_id: str,
        expected_rev: int,
    ) -> dict:
        rec = self.topics.get(topic_id)
        if rec is None or rec["tenant_id"] != tenant_id:
            raise KeyError(topic_id)
        if rec["rev"] != expected_rev:
            raise RevMismatch()
        rec["status"] = "archived"
        rec["rev"] += 1
        rec["updated_at"] = "2026-05-27T01:00:00Z"
        return rec


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    clear_idempotency_caches_for_tests()


@pytest.fixture
def mock_backend(monkeypatch: pytest.MonkeyPatch) -> MockTopicsBackend:
    backend = MockTopicsBackend()
    import storage.factory as sf
    monkeypatch.setattr(sf, "get_backend", lambda: backend)
    return backend


def _ik(suffix: str = "") -> str:
    """Unique idempotency key per call."""
    return f"topics-{suffix or uuid.uuid4().hex}"


async def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ── Tests ───────────────────────────────────────────────────────────────────


class TestTopicsRouter:
    """GET /api/v1/topics — list."""

    async def test_list_empty(self, mock_backend: MockTopicsBackend) -> None:
        async with await _client() as ac:
            resp = await ac.get("/api/v1/topics", headers=HEADER_A)
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0
        assert body["page"] == 1
        assert body["page_size"] == 20
        assert body["has_more"] is False

    async def test_list_with_items(self, mock_backend: MockTopicsBackend) -> None:
        mock_backend.create_topic(TENANT_A, "Topic A")
        mock_backend.create_topic(TENANT_A, "Topic B")
        async with await _client() as ac:
            resp = await ac.get("/api/v1/topics", headers=HEADER_A)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert len(body["items"]) == 2

    async def test_list_pagination(self, mock_backend: MockTopicsBackend) -> None:
        for i in range(5):
            mock_backend.create_topic(TENANT_A, f"Topic {i}")
        async with await _client() as ac:
            resp = await ac.get("/api/v1/topics?page=1&page_size=2", headers=HEADER_A)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["total"] == 5
        assert body["page"] == 1
        assert body["page_size"] == 2
        assert body["has_more"] is True

    async def test_list_sort(self, mock_backend: MockTopicsBackend) -> None:
        mock_backend.create_topic(TENANT_A, "A")
        mock_backend.create_topic(TENANT_A, "B")
        async with await _client() as ac:
            resp = await ac.get("/api/v1/topics?sort=title", headers=HEADER_A)
        assert resp.status_code == 200

    async def test_list_filter_by_status(self, mock_backend: MockTopicsBackend) -> None:
        t1 = mock_backend.create_topic(TENANT_A, "Idea")
        mock_backend.update_topic(TENANT_A, t1["topic_id"], expected_rev=1, status="planned")
        mock_backend.create_topic(TENANT_A, "Draft")
        async with await _client() as ac:
            resp = await ac.get("/api/v1/topics?status=planned", headers=HEADER_A)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["status"] == "planned"

    """GET /api/v1/topics/{id} — get by id."""

    async def test_get_by_id_200(self, mock_backend: MockTopicsBackend) -> None:
        rec = mock_backend.create_topic(TENANT_A, "My Topic")
        async with await _client() as ac:
            resp = await ac.get(f"/api/v1/topics/{rec['topic_id']}", headers=HEADER_A)
        assert resp.status_code == 200
        body = resp.json()
        assert body["title"] == "My Topic"
        assert body["rev"] == 1
        assert body["topic_id"] == rec["topic_id"]

    async def test_get_by_id_404(self, mock_backend: MockTopicsBackend) -> None:
        async with await _client() as ac:
            resp = await ac.get("/api/v1/topics/nonexistent", headers=HEADER_A)
        assert resp.status_code == 404
        body = resp.json()
        assert body["error"]["code"] == "not_found"

    """POST /api/v1/topics — create."""

    async def test_create_201(self, mock_backend: MockTopicsBackend) -> None:
        ik = _ik("create")
        async with await _client() as ac:
            resp = await ac.post(
                "/api/v1/topics",
                json={"title": "New Topic"},
                headers={**HEADER_A, "Idempotency-Key": ik},
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["title"] == "New Topic"
        assert body["rev"] == 1
        assert body["status"] == "idea"
        assert body["source"] == "manual"

    async def test_create_with_optional_fields(self, mock_backend: MockTopicsBackend) -> None:
        ik = _ik("create-full")
        async with await _client() as ac:
            resp = await ac.post(
                "/api/v1/topics",
                json={
                    "title": "Full Topic",
                    "goal_id": "goal_001",
                    "funnel_stage": "traffic",
                    "source": "market_insight",
                },
                headers={**HEADER_A, "Idempotency-Key": ik},
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["goal_id"] == "goal_001"
        assert body["funnel_stage"] == "traffic"
        assert body["source"] == "market_insight"

    """PUT /api/v1/topics/{id} — update."""

    async def test_update_200(self, mock_backend: MockTopicsBackend) -> None:
        rec = mock_backend.create_topic(TENANT_A, "Old Title")
        ik = _ik("update")
        async with await _client() as ac:
            resp = await ac.put(
                f"/api/v1/topics/{rec['topic_id']}",
                json={"title": "New Title", "rev": 1},
                headers={**HEADER_A, "Idempotency-Key": ik},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["title"] == "New Title"
        assert body["rev"] == 2  # incremented

    async def test_update_409_rev_mismatch(self, mock_backend: MockTopicsBackend) -> None:
        rec = mock_backend.create_topic(TENANT_A, "Stale")
        ik = _ik("update-409")
        async with await _client() as ac:
            resp = await ac.put(
                f"/api/v1/topics/{rec['topic_id']}",
                json={"title": "Conflict", "rev": 99},
                headers={**HEADER_A, "Idempotency-Key": ik},
            )
        assert resp.status_code == 409
        body = resp.json()
        assert body["error"]["code"] == "rev_mismatch"
        assert body["error"]["current_rev"] == 1  # latest rev

    async def test_update_404(self, mock_backend: MockTopicsBackend) -> None:
        ik = _ik("update-404")
        async with await _client() as ac:
            resp = await ac.put(
                "/api/v1/topics/nonexistent",
                json={"title": "Nope", "rev": 1},
                headers={**HEADER_A, "Idempotency-Key": ik},
            )
        assert resp.status_code == 404
        body = resp.json()
        assert body["error"]["code"] == "not_found"

    """DELETE /api/v1/topics/{id} — archive."""

    async def test_delete_200(self, mock_backend: MockTopicsBackend) -> None:
        rec = mock_backend.create_topic(TENANT_A, "To Archive")
        ik = _ik("delete")
        async with await _client() as ac:
            resp = await ac.delete(
                f"/api/v1/topics/{rec['topic_id']}?rev=1",
                headers={**HEADER_A, "Idempotency-Key": ik},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["topic_id"] == rec["topic_id"]
        assert body["status"] == "archived"

    async def test_delete_404(self, mock_backend: MockTopicsBackend) -> None:
        ik = _ik("delete-404")
        async with await _client() as ac:
            resp = await ac.delete(
                "/api/v1/topics/nonexistent?rev=1",
                headers={**HEADER_A, "Idempotency-Key": ik},
            )
        assert resp.status_code == 404
        body = resp.json()
        assert body["error"]["code"] == "not_found"

    """Tenant isolation."""

    async def test_tenant_isolation_list(self, mock_backend: MockTopicsBackend) -> None:
        mock_backend.create_topic(TENANT_A, "A's Topic")
        mock_backend.create_topic(TENANT_A, "A's Other")
        async with await _client() as ac:
            resp_b = await ac.get("/api/v1/topics", headers=HEADER_B)
        assert resp_b.status_code == 200
        assert resp_b.json()["total"] == 0  # B sees nothing from A

    async def test_tenant_isolation_get(self, mock_backend: MockTopicsBackend) -> None:
        rec = mock_backend.create_topic(TENANT_A, "Secret Topic")
        async with await _client() as ac:
            resp = await ac.get(f"/api/v1/topics/{rec['topic_id']}", headers=HEADER_B)
        assert resp.status_code == 404  # B cannot get A's topic

    """Body tenant_id forbidden."""

    async def test_create_rejects_tenant_id_in_body(self, mock_backend: MockTopicsBackend) -> None:
        ik = _ik("no-tenant-body")
        async with await _client() as ac:
            resp = await ac.post(
                "/api/v1/topics",
                json={"title": "Bad", "tenant_id": "should-not-pass"},
                headers={**HEADER_A, "Idempotency-Key": ik},
            )
        assert resp.status_code == 422
        body = resp.json()
        assert body["error"]["code"] == "tenant_in_body_forbidden"

    async def test_update_rejects_tenant_id_in_body(self, mock_backend: MockTopicsBackend) -> None:
        rec = mock_backend.create_topic(TENANT_A, "Clean")
        ik = _ik("no-tenant-body-upd")
        async with await _client() as ac:
            resp = await ac.put(
                f"/api/v1/topics/{rec['topic_id']}",
                json={"title": "Dirty", "rev": 1, "tenant_id": "sneaky"},
                headers={**HEADER_A, "Idempotency-Key": ik},
            )
        assert resp.status_code == 422
        body = resp.json()
        assert body["error"]["code"] == "tenant_in_body_forbidden"
