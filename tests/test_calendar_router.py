"""
Tests for server/routers/calendar.py — CRUD + soft delete.

Coverage:
  - GET /api/v1/calendar (list, include_deleted filter)
  - GET /api/v1/calendar/{id} (200 / 404)
  - POST /api/v1/calendar (201)
  - PUT /api/v1/calendar/{id} (200 / 409 RevMismatch / 404)
  - DELETE /api/v1/calendar/{id} (soft → status=cancelled, hard → removed)
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


class MockCalendarBackend:
    """In-memory mock for calendar storage operations."""

    def __init__(self) -> None:
        self.items: dict[str, dict] = {}

    def create_calendar_item(
        self,
        tenant_id: str,
        scheduled_date: str,
        scheduled_time: str | None = None,
        topic_id: str | None = None,
        funnel_stage: str | None = None,
        content_id: str | None = None,
    ) -> dict:
        cal_id = f"cal_{uuid.uuid4().hex[:8]}"
        now = "2026-05-27T00:00:00Z"
        rec: dict[str, Any] = {
            "calendar_item_id": cal_id,
            "tenant_id": tenant_id,
            "topic_id": topic_id,
            "content_id": content_id,
            "scheduled_date": scheduled_date,
            "scheduled_time": scheduled_time,
            "funnel_stage": funnel_stage,
            "status": "planned",
            "delete_mode": "soft",
            "deleted_at": None,
            "created_by": "user",
            "rev": 1,
            "created_at": now,
            "updated_at": now,
        }
        self.items[cal_id] = rec
        return rec

    def get_calendar_item(self, tenant_id: str, cal_id: str) -> dict:
        rec = self.items.get(cal_id)
        if rec is None or rec["tenant_id"] != tenant_id:
            raise KeyError(cal_id)
        return rec

    def list_calendar_items(
        self,
        tenant_id: str,
        date_from: str | None = None,
        date_to: str | None = None,
        status: str | None = None,
        include_deleted: bool = False,
        page: int = 1,
        page_size: int = 20,
        sort: str = "scheduled_date",
    ) -> dict:
        items = [i for i in self.items.values() if i["tenant_id"] == tenant_id]
        if not include_deleted:
            items = [i for i in items if i.get("deleted_at") is None]
        if date_from:
            items = [i for i in items if i.get("scheduled_date", "") >= date_from]
        if date_to:
            items = [i for i in items if i.get("scheduled_date", "") <= date_to]
        if status:
            items = [i for i in items if i.get("status") == status]
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

    def update_calendar_item(
        self,
        tenant_id: str,
        cal_id: str,
        expected_rev: int,
        **changes: Any,
    ) -> dict:
        rec = self.items.get(cal_id)
        if rec is None or rec["tenant_id"] != tenant_id:
            raise KeyError(cal_id)
        if rec["rev"] != expected_rev:
            raise RevMismatch()
        rec.update(changes)
        rec["rev"] += 1
        rec["updated_at"] = "2026-05-27T01:00:00Z"
        return rec

    def delete_calendar_item(
        self,
        tenant_id: str,
        cal_id: str,
        expected_rev: int,
        mode: str = "soft",
    ) -> dict:
        rec = self.items.get(cal_id)
        if rec is None or rec["tenant_id"] != tenant_id:
            raise KeyError(cal_id)
        if rec["rev"] != expected_rev:
            raise RevMismatch()
        if mode == "hard":
            del self.items[cal_id]
            return {"deleted": True}
        # soft delete
        rec["status"] = "cancelled"
        rec["deleted_at"] = "2026-05-27T12:00:00Z"
        rec["rev"] += 1
        rec["updated_at"] = "2026-05-27T12:00:00Z"
        return {
            "calendar_item_id": cal_id,
            "status": "cancelled",
            "deleted_at": rec["deleted_at"],
            "rev": rec["rev"],
        }


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    clear_idempotency_caches_for_tests()


@pytest.fixture
def mock_backend(monkeypatch: pytest.MonkeyPatch) -> MockCalendarBackend:
    backend = MockCalendarBackend()
    import storage.factory as sf
    monkeypatch.setattr(sf, "get_backend", lambda: backend)
    return backend


def _ik(suffix: str = "") -> str:
    return f"cal-{suffix or uuid.uuid4().hex}"


async def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ── Tests ───────────────────────────────────────────────────────────────────


class TestCalendarRouter:

    async def test_list_empty(self, mock_backend: MockCalendarBackend) -> None:
        async with await _client() as ac:
            resp = await ac.get("/api/v1/calendar", headers=HEADER_A)
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    async def test_list_with_items(self, mock_backend: MockCalendarBackend) -> None:
        mock_backend.create_calendar_item(TENANT_A, "2026-06-01")
        mock_backend.create_calendar_item(TENANT_A, "2026-06-02")
        async with await _client() as ac:
            resp = await ac.get("/api/v1/calendar", headers=HEADER_A)
        assert resp.status_code == 200
        assert resp.json()["total"] == 2

    async def test_list_date_range(self, mock_backend: MockCalendarBackend) -> None:
        mock_backend.create_calendar_item(TENANT_A, "2026-06-01")
        mock_backend.create_calendar_item(TENANT_A, "2026-06-15")
        mock_backend.create_calendar_item(TENANT_A, "2026-07-01")
        async with await _client() as ac:
            resp = await ac.get("/api/v1/calendar?from=2026-06-01&to=2026-06-30", headers=HEADER_A)
        assert resp.status_code == 200
        assert resp.json()["total"] == 2

    async def test_list_pagination(self, mock_backend: MockCalendarBackend) -> None:
        for i in range(5):
            mock_backend.create_calendar_item(TENANT_A, f"2026-06-{i+1:02d}")
        async with await _client() as ac:
            resp = await ac.get("/api/v1/calendar?page=1&page_size=2", headers=HEADER_A)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["total"] == 5
        assert body["has_more"] is True

    async def test_get_by_id_200(self, mock_backend: MockCalendarBackend) -> None:
        rec = mock_backend.create_calendar_item(TENANT_A, "2026-06-01")
        async with await _client() as ac:
            resp = await ac.get(f"/api/v1/calendar/{rec['calendar_item_id']}", headers=HEADER_A)
        assert resp.status_code == 200
        assert resp.json()["scheduled_date"] == "2026-06-01"

    async def test_get_by_id_404(self, mock_backend: MockCalendarBackend) -> None:
        async with await _client() as ac:
            resp = await ac.get("/api/v1/calendar/nonexistent", headers=HEADER_A)
        assert resp.status_code == 404

    async def test_create_201(self, mock_backend: MockCalendarBackend) -> None:
        ik = _ik("create")
        async with await _client() as ac:
            resp = await ac.post(
                "/api/v1/calendar",
                json={"scheduled_date": "2026-06-15"},
                headers={**HEADER_A, "Idempotency-Key": ik},
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["scheduled_date"] == "2026-06-15"
        assert body["status"] == "planned"
        assert body["rev"] == 1

    async def test_update_200(self, mock_backend: MockCalendarBackend) -> None:
        rec = mock_backend.create_calendar_item(TENANT_A, "2026-06-01")
        ik = _ik("update")
        async with await _client() as ac:
            resp = await ac.put(
                f"/api/v1/calendar/{rec['calendar_item_id']}",
                json={"scheduled_date": "2026-06-20", "rev": 1},
                headers={**HEADER_A, "Idempotency-Key": ik},
            )
        assert resp.status_code == 200
        assert resp.json()["scheduled_date"] == "2026-06-20"
        assert resp.json()["rev"] == 2

    async def test_update_409(self, mock_backend: MockCalendarBackend) -> None:
        rec = mock_backend.create_calendar_item(TENANT_A, "2026-06-01")
        ik = _ik("update-409")
        async with await _client() as ac:
            resp = await ac.put(
                f"/api/v1/calendar/{rec['calendar_item_id']}",
                json={"scheduled_date": "2026-07-01", "rev": 99},
                headers={**HEADER_A, "Idempotency-Key": ik},
            )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "rev_mismatch"

    async def test_update_404(self, mock_backend: MockCalendarBackend) -> None:
        ik = _ik("update-404")
        async with await _client() as ac:
            resp = await ac.put(
                "/api/v1/calendar/nonexistent",
                json={"scheduled_date": "2026-07-01", "rev": 1},
                headers={**HEADER_A, "Idempotency-Key": ik},
            )
        assert resp.status_code == 404

    async def test_delete_soft(self, mock_backend: MockCalendarBackend) -> None:
        """Soft-deleted item should have status=cancelled and be hidden from default list."""
        rec = mock_backend.create_calendar_item(TENANT_A, "2026-06-01")
        ik = _ik("del-soft")
        async with await _client() as ac:
            resp = await ac.delete(
                f"/api/v1/calendar/{rec['calendar_item_id']}?rev=1&mode=soft",
                headers={**HEADER_A, "Idempotency-Key": ik},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "cancelled"

        # Default list (include_deleted=false) should NOT return it
        async with await _client() as ac2:
            list_resp = await ac2.get("/api/v1/calendar", headers=HEADER_A)
        assert list_resp.json()["total"] == 0

        # With include_deleted=true it should appear
        async with await _client() as ac3:
            list_with_deleted = await ac3.get("/api/v1/calendar?include_deleted=true", headers=HEADER_A)
        assert list_with_deleted.json()["total"] == 1

    async def test_delete_hard(self, mock_backend: MockCalendarBackend) -> None:
        """Hard-deleted item is completely removed."""
        rec = mock_backend.create_calendar_item(TENANT_A, "2026-06-01")
        ik = _ik("del-hard")
        async with await _client() as ac:
            resp = await ac.delete(
                f"/api/v1/calendar/{rec['calendar_item_id']}?rev=1&mode=hard",
                headers={**HEADER_A, "Idempotency-Key": ik},
            )
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

        # Item should be gone even with include_deleted
        async with await _client() as ac2:
            list_resp = await ac2.get("/api/v1/calendar?include_deleted=true", headers=HEADER_A)
        assert list_resp.json()["total"] == 0

    async def test_delete_404(self, mock_backend: MockCalendarBackend) -> None:
        ik = _ik("del-404")
        async with await _client() as ac:
            resp = await ac.delete(
                "/api/v1/calendar/nonexistent?rev=1",
                headers={**HEADER_A, "Idempotency-Key": ik},
            )
        assert resp.status_code == 404

    async def test_tenant_isolation(self, mock_backend: MockCalendarBackend) -> None:
        mock_backend.create_calendar_item(TENANT_A, "2026-06-01")
        async with await _client() as ac:
            resp = await ac.get("/api/v1/calendar", headers=HEADER_B)
        assert resp.json()["total"] == 0

    async def test_rejects_tenant_id_in_body(self, mock_backend: MockCalendarBackend) -> None:
        ik = _ik("no-tenant")
        async with await _client() as ac:
            resp = await ac.post(
                "/api/v1/calendar",
                json={"scheduled_date": "2026-06-01", "tenant_id": "hack"},
                headers={**HEADER_A, "Idempotency-Key": ik},
            )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "tenant_in_body_forbidden"
