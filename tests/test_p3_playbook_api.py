"""
P3.2.D6-D9 · Playbook Draft Review API 验收测试
运行：pytest tests/test_p3_playbook_api.py -v

覆盖目标：
- S1: GET /drafts — 空 playbook 返回空列表
- S2: GET /drafts — 只有 draft entry 时正确列出
- S3: GET /drafts — 混合 status 只返回 draft
- S4: POST /drafts/{id}/accept — 采纳后 status=active
- S5: POST /drafts/{id}/accept — 非 draft 报 400
- S6: POST /drafts/{id}/reject — 驳回后 status=rejected
- S7: POST /drafts/{id}/reject — 不存在的 entry 报 404
- S8: PUT /drafts/{id} — 编辑后 body 更新 + status=active
- S9: GET /drafts/count — 返回正确的 count
- S10: POST /drafts/{id}/accept — 无 token 报 401
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from security.jwt import encode_token

os.environ.setdefault("JWT_SECRET", "test_secret_for_p2_only")
os.environ.setdefault("JWT_ALGORITHM", "HS256")

import uuid as _uuid
from server.middleware.idempotency import clear_idempotency_caches_for_tests

# fixture writes playbook to memory/default/ (hardcoded) → token "default"
JWT = encode_token("default")
AUTH = {"Authorization": f"Bearer {JWT}"}


def _wauth() -> dict:
    """Headers for write endpoints: auth + fresh Idempotency-Key (IdempotencyRoute)."""
    return {**AUTH, "Idempotency-Key": _uuid.uuid4().hex}


# ── helper：在 tmp 目录写入 playbook.md ─────────────────────────────────

def _write_playbook(memory_dir: Path, content: str):
    """写入 memory/default/content/playbook.md。"""
    path = memory_dir / "default" / "content" / "playbook.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ── fixture ──────────────────────────────────────────────────────────────

@pytest.fixture()
def client(tmp_path):
    """创建隔离环境，patch settings + 让 LocalJsonBackend 指向 tmp_path。"""
    clear_idempotency_caches_for_tests()
    settings = {
        "scheduler": {"enabled": False},
    }
    (tmp_path / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

    def _make_backend():
        from storage.local_json import LocalJsonBackend
        return LocalJsonBackend(base_dir=str(tmp_path))

    with patch("server.routers.playbook._get_memory_layer") as mock_factory:
        # make _get_memory_layer return a MemoryLayer with the tmp backend
        from agents.memory import MemoryLayer
        mock_factory.side_effect = lambda: MemoryLayer(storage=_make_backend())
        from server.main import app
        app.dependency_overrides.clear()
        with TestClient(app) as c:
            yield c


# ── S1-S3 · List drafts ─────────────────────────────────────────────────

class TestListDrafts:
    def test_empty(self, client, tmp_path):
        """无 playbook → 空列表。"""
        r = client.get("/api/v1/playbook/drafts", headers=_wauth())
        assert r.status_code == 200
        data = r.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_only_drafts_listed(self, client, tmp_path):
        """只有 draft entry 时正确列出。"""
        _write_playbook(tmp_path / "memory",
            "§id: weekly-2026-05-07 §rev: 1 §status: draft §source: scheduler §confidence: low\n"
            "本周 insights\n\n"
            "§id: weekly-2026-05-01 §rev: 1 §status: draft §source: scheduler §confidence: high\n"
            "上周 insights"
        )
        r = client.get("/api/v1/playbook/drafts", headers=_wauth())
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 2
        ids = [i["id"] for i in data["items"]]
        assert "weekly-2026-05-07" in ids
        assert "weekly-2026-05-01" in ids
        # 验证字段完整
        item = data["items"][0]
        assert "body" in item
        assert "status" in item
        assert "source" in item
        assert "confidence" in item
        assert "rev" in item

    def test_mixed_status_filters(self, client, tmp_path):
        """混合 status 只返回 draft。"""
        _write_playbook(tmp_path / "memory",
            "§id: draft-1 §rev: 1 §status: draft §source: scheduler §confidence: low\n"
            "draft insight\n\n"
            "§id: active-1 §rev: 2 §status: active §source: manual §confidence: high\n"
            "active insight\n\n"
            "§id: rejected-1 §rev: 1 §status: rejected §source: manual §confidence: high\n"
            "rejected insight"
        )
        r = client.get("/api/v1/playbook/drafts", headers=_wauth())
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        assert data["items"][0]["id"] == "draft-1"


# ── S4-S5 · Accept ──────────────────────────────────────────────────────

class TestAccept:
    def test_accept_draft(self, client, tmp_path):
        """采纳后 status=active, rev+1。"""
        _write_playbook(tmp_path / "memory",
            "§id: weekly-test §rev: 1 §status: draft §source: scheduler §confidence: low\n"
            "test insight"
        )
        r = client.post("/api/v1/playbook/drafts/weekly-test/accept", headers=_wauth())
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["new_rev"] == 2

        # 验证文件变更
        path = tmp_path / "memory" / "default" / "content" / "playbook.md"
        content = path.read_text(encoding="utf-8")
        assert "§status: active" in content
        assert "§rev: 2" in content

    def test_accept_non_draft_400(self, client, tmp_path):
        """active entry → accept 报 400。"""
        _write_playbook(tmp_path / "memory",
            "§id: active-1 §rev: 2 §status: active §source: manual §confidence: high\n"
            "already active"
        )
        r = client.post("/api/v1/playbook/drafts/active-1/accept", headers=_wauth())
        assert r.status_code == 400

    def test_accept_nonexistent_404(self, client, tmp_path):
        """不存在的 entry → 404。"""
        r = client.post("/api/v1/playbook/drafts/ghost/accept", headers=_wauth())
        assert r.status_code == 404


# ── S6-S7 · Reject ──────────────────────────────────────────────────────

class TestReject:
    def test_reject_draft(self, client, tmp_path):
        """驳回后 status=rejected, rev+1。"""
        _write_playbook(tmp_path / "memory",
            "§id: weekly-test §rev: 1 §status: draft §source: scheduler §confidence: low\n"
            "test insight"
        )
        r = client.post("/api/v1/playbook/drafts/weekly-test/reject", headers=_wauth())
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["new_rev"] == 2

        path = tmp_path / "memory" / "default" / "content" / "playbook.md"
        content = path.read_text(encoding="utf-8")
        assert "§status: rejected" in content

    def test_reject_non_draft_400(self, client, tmp_path):
        """active entry → reject 报 400。"""
        _write_playbook(tmp_path / "memory",
            "§id: active-1 §rev: 2 §status: active §source: manual §confidence: high\n"
            "active"
        )
        r = client.post("/api/v1/playbook/drafts/active-1/reject", headers=_wauth())
        assert r.status_code == 400

    def test_reject_nonexistent_404(self, client, tmp_path):
        r = client.post("/api/v1/playbook/drafts/ghost/reject", headers=_wauth())
        assert r.status_code == 404


# ── S8 · Edit ───────────────────────────────────────────────────────────

class TestEdit:
    def test_edit_draft(self, client, tmp_path):
        """编辑后 body 更新 + status=active。"""
        _write_playbook(tmp_path / "memory",
            "§id: weekly-test §rev: 1 §status: draft §source: scheduler §confidence: low\n"
            "original body"
        )
        r = client.put("/api/v1/playbook/drafts/weekly-test",
                       json={"body": "edited body"}, headers=_wauth())
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["new_rev"] == 2

        path = tmp_path / "memory" / "default" / "content" / "playbook.md"
        content = path.read_text(encoding="utf-8")
        assert "§status: active" in content
        assert "edited body" in content
        assert "original body" not in content

    def test_edit_nonexistent_404(self, client, tmp_path):
        r = client.put("/api/v1/playbook/drafts/ghost",
                       json={"body": "x"}, headers=_wauth())
        assert r.status_code == 404

    def test_edit_non_draft_400(self, client, tmp_path):
        """active entry 不能 edit（必须走 accept/reject）。"""
        _write_playbook(tmp_path / "memory",
            "§id: active-1 §rev: 2 §status: active §source: manual §confidence: high\n"
            "active"
        )
        r = client.put("/api/v1/playbook/drafts/active-1",
                       json={"body": "edited"}, headers=_wauth())
        assert r.status_code == 400


# ── S9 · Count ─────────────────────────────────────────────────────────

class TestCount:
    def test_count_zero(self, client, tmp_path):
        r = client.get("/api/v1/playbook/drafts/count", headers=_wauth())
        assert r.status_code == 200
        assert r.json()["count"] == 0

    def test_count_matches(self, client, tmp_path):
        _write_playbook(tmp_path / "memory",
            "§id: d1 §rev: 1 §status: draft §source: scheduler §confidence: low\n"
            "d1\n\n"
            "§id: a1 §rev: 2 §status: active §source: manual §confidence: high\n"
            "a1\n\n"
            "§id: d2 §rev: 1 §status: draft §source: scheduler §confidence: high\n"
            "d2"
        )
        r = client.get("/api/v1/playbook/drafts/count", headers=_wauth())
        assert r.status_code == 200
        assert r.json()["count"] == 2


# ── S10 · Auth ──────────────────────────────────────────────────────────

class TestAuth:
    def test_no_token_401(self, client):
        r = client.get("/api/v1/playbook/drafts")
        assert r.status_code == 401

    def test_wrong_token_401(self, client):
        r = client.get("/api/v1/playbook/drafts",
                       headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401
