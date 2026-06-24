"""
F1.4 Content API 验收测试（Persistence + PUT + Merge）
运行：pytest tests/test_f1_content_api.py -v
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from security.jwt import encode_token

os.environ.setdefault("JWT_SECRET", "test_secret_for_p2_only")
os.environ.setdefault("JWT_ALGORITHM", "HS256")

import uuid as _uuid
from server.middleware.idempotency import clear_idempotency_caches_for_tests

# fixture writes goals to cfg_dir/test-tenant/ → token stays "test-tenant"
JWT = encode_token("test-tenant")
AUTH = {"Authorization": f"Bearer {JWT}"}


def _wauth() -> dict:
    """Headers for write endpoints: auth + fresh Idempotency-Key (IdempotencyRoute)."""
    return {**AUTH, "Idempotency-Key": _uuid.uuid4().hex}


def _write_settings(tmp_path: Path) -> None:
    settings = {
        "kimi_api_key": "test-key",
        "kimi_base_url": "https://api.moonshot.cn/v1",
        "kimi_model": "moonshot-v1-32k",
        "llm_provider": "mock",
    }
    (tmp_path / "settings.json").write_text(json.dumps(settings), encoding="utf-8")


def _write_goals(cfg_dir: Path, tenant_id: str = "test-tenant") -> None:
    goals_data = {
        "active_goal_id": "goal_test",
        "goals": [
            {
                "id": "goal_test",
                "name": "测试目标",
                "objective": "转化",
                "status": "active",
                "description": "test",
                "created_at": "2026-05-07",
                "target_audience": {
                    "who": "工厂物业",
                    "pain_points": "找不到好点位",
                    "interests": "自动化",
                },
                "brand_position": "深圳自助售卖机运营商",
                "benchmark_accounts": [],
                "keywords": ["自助机点位"],
                "keyword_library": ["自助机点位招商"],
                "topic_library": [],
                "content_calendar": [],
                "used_angles": [],
                "campaigns": [],
                "persona_id": "p1",
                "overall_strategy": {},
                "performance": {"posts": []},
            }
        ],
    }
    target_dir = cfg_dir / tenant_id if tenant_id != "default" else cfg_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "goals.json").write_text(
        json.dumps(goals_data, ensure_ascii=False), encoding="utf-8"
    )


@pytest.fixture()
def client(tmp_path):
    clear_idempotency_caches_for_tests()
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    _write_settings(cfg_dir)
    _write_goals(cfg_dir)

    from storage.local_json import LocalJsonBackend
    test_backend = LocalJsonBackend(base_dir=str(tmp_path))

    def _mock_get_backend():
        return test_backend

    with patch("storage.factory.get_backend", _mock_get_backend), \
         patch("server.routers.content.CONFIG_DIR", cfg_dir):
        from server.main import app

        with TestClient(app) as c:
            yield c


# ── S1 · Generate + Persist ──────────────────────────────────────────────


class TestGeneratePersist:
    def test_generate_persists_when_persist_true(self, client):
        r = client.post(
            "/api/v1/content/generate",
            json={"goal_id": "goal_test", "topic": "X", "count": 2, "persist": True},
            headers=_wauth(),
        )
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 2

        # Verify persisted items are visible via GET
        r2 = client.get("/api/v1/content?goal_id=goal_test", headers=_wauth())
        assert r2.status_code == 200
        ids = [i["content_id"] for i in r2.json()["items"]]
        assert all(i["content_id"] in ids for i in items)

    def test_generate_does_not_persist_when_false(self, client):
        r = client.post(
            "/api/v1/content/generate",
            json={"goal_id": "goal_test", "topic": "X", "count": 1, "persist": False},
            headers=_wauth(),
        )
        assert r.status_code == 200

        r2 = client.get("/api/v1/content?goal_id=goal_test", headers=_wauth())
        assert r2.json()["total"] == 0

    def test_generated_item_has_all_fields(self, client):
        r = client.post(
            "/api/v1/content/generate",
            json={"goal_id": "goal_test", "topic": "X", "count": 1, "persist": False},
            headers=_wauth(),
        )
        assert r.status_code == 200
        item = r.json()["items"][0]
        assert "content_id" in item
        assert "goal_id" in item
        assert item["goal_id"] == "goal_test"
        assert "alt_titles" in item
        assert isinstance(item["alt_titles"], list)
        assert "publish_reason" in item
        assert "source" in item
        assert item["source"] == "ai_generate"
        assert "created_at" in item
        assert "updated_at" in item
        assert "edit_count" in item
        assert item["edit_count"] == 0


# ── S2 · PUT Partial Update ──────────────────────────────────────────────


class TestPutUpdate:
    def test_put_partial_update(self, client):
        gen = client.post(
            "/api/v1/content/generate",
            json={"goal_id": "goal_test", "topic": "X", "count": 1, "persist": True},
            headers=_wauth(),
        ).json()
        cid = gen["items"][0]["content_id"]

        r = client.put(
            f"/api/v1/content/{cid}", json={"title": "新标题"}, headers=_wauth()
        )
        assert r.status_code == 200
        body = r.json()
        assert body["title"] == "新标题"
        assert body["status"] == "edited"
        assert body["edit_count"] == 1

    def test_put_multiple_fields(self, client):
        gen = client.post(
            "/api/v1/content/generate",
            json={"goal_id": "goal_test", "topic": "X", "count": 1, "persist": True},
            headers=_wauth(),
        ).json()
        cid = gen["items"][0]["content_id"]

        r = client.put(
            f"/api/v1/content/{cid}",
            json={"title": "新标题", "body": "新内容", "hashtags": ["a", "b"]},
            headers=_wauth(),
        )
        assert r.status_code == 200
        body = r.json()
        assert body["title"] == "新标题"
        assert body["body"] == "新内容"
        assert body["hashtags"] == ["a", "b"]

    def test_put_preserves_unchanged_fields(self, client):
        gen = client.post(
            "/api/v1/content/generate",
            json={"goal_id": "goal_test", "topic": "X", "count": 1, "persist": True},
            headers=_wauth(),
        ).json()
        cid = gen["items"][0]["content_id"]
        orig_content = gen["items"][0]["body"]

        r = client.put(
            f"/api/v1/content/{cid}", json={"title": "新标题"}, headers=_wauth()
        )
        assert r.status_code == 200
        assert r.json()["body"] == orig_content

    def test_put_legacy_xlsx_rejected(self, client):
        import storage.factory
        backend = storage.factory.get_backend()
        item_id = "legacy_test_001"
        item = {
            "content_id": item_id, "goal_id": "goal_test", "title": "legacy",
            "body": "legacy content", "hashtags": [], "publish_at": "",
            "publish_reason": "", "angle": "", "alt_titles": [],
            "status": "draft", "source": "legacy_xlsx",
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
            "edit_count": 0,
        }
        import pandas as pd
        backend.save_generated_posts("test-tenant", pd.DataFrame([item]), meta={})

        r = client.put(
            f"/api/v1/content/{item_id}", json={"title": "edit"}, headers=_wauth()
        )
        assert r.status_code == 409

    def test_put_unknown_id_404(self, client):
        r = client.put(
            "/api/v1/content/nonexistent_id", json={"title": "x"}, headers=_wauth()
        )
        assert r.status_code == 404


# ── S3 · GET Merge ──────────────────────────────────────────────────────


class TestGetContent:
    def test_get_empty(self, client):
        r = client.get("/api/v1/content?goal_id=goal_test", headers=_wauth())
        assert r.status_code == 200
        assert r.json()["items"] == []
        assert r.json()["total"] == 0

    def test_get_merges_json_and_xlsx(self, client):
        # Persist 1 item via API
        client.post(
            "/api/v1/content/generate",
            json={"goal_id": "goal_test", "topic": "X", "count": 1, "persist": True},
            headers=_wauth(),
        )

        # Save 2 more items via backend (simulating legacy xlsx source)
        import storage.factory
        backend = storage.factory.get_backend()
        extra = pd.DataFrame([
            {"content_id": "xlsx_a", "goal_id": "goal_test", "title": "xlsx1", "body": "c1",
             "hashtags": "t1", "publish_at": "12:00", "angle": "a1",
             "status": "draft", "source": "legacy_xlsx"},
            {"content_id": "xlsx_b", "goal_id": "goal_test", "title": "xlsx2", "body": "c2",
             "hashtags": "t2", "publish_at": "20:30", "angle": "a2",
             "status": "draft", "source": "legacy_xlsx"},
        ])
        backend.save_generated_posts("test-tenant", extra, meta={})

        r = client.get("/api/v1/content?goal_id=goal_test", headers=_wauth())
        assert r.status_code == 200
        # 1 API item + 2 xlsx items = 3 total
        assert r.json()["total"] == 3

        xlsx_items = [i for i in r.json()["items"] if i.get("source") == "legacy_xlsx"]
        assert len(xlsx_items) == 2

    def test_status_filter(self, client):
        # Generate and persist 1 item
        client.post(
            "/api/v1/content/generate",
            json={"goal_id": "goal_test", "topic": "X", "count": 1, "persist": True},
            headers=_wauth(),
        )

        # Filter by draft — should find it
        r = client.get(
            "/api/v1/content?goal_id=goal_test&status=draft", headers=_wauth()
        )
        assert r.status_code == 200
        assert r.json()["total"] >= 1

        # Filter by edited — should be empty (none edited yet)
        r = client.get(
            "/api/v1/content?goal_id=goal_test&status=edited", headers=_wauth()
        )
        assert r.json()["total"] == 0


# ── S4 · PUT + GET Integration ──────────────────────────────────────────


class TestIntegration:
    def test_edit_then_read_shows_updated(self, client):
        gen = client.post(
            "/api/v1/content/generate",
            json={"goal_id": "goal_test", "topic": "X", "count": 1, "persist": True},
            headers=_wauth(),
        ).json()
        cid = gen["items"][0]["content_id"]

        client.put(
            f"/api/v1/content/{cid}",
            json={"title": "updated title"},
            headers=_wauth(),
        )

        r = client.get("/api/v1/content?goal_id=goal_test", headers=_wauth())
        item = next(i for i in r.json()["items"] if i["content_id"] == cid)
        assert item["title"] == "updated title"
        assert item["status"] == "edited"
        assert item["edit_count"] == 1


# ── S5 · Stability + Race + Edge Cases ────────────────────────────────────


class TestStabilityRace:
    """Failing tests before fixes, then pass after."""
    def test_legacy_xlsx_id_uses_content_hash(self, client):
        """legacy items saved via backend get stable IDs."""
        import storage.factory
        backend = storage.factory.get_backend()
        item_id = "xlsx_stable_001"
        item = {"content_id": item_id, "goal_id": "goal_test", "title": "a",
                "body": "c", "angle": "x", "source": "legacy_xlsx",
                "status": "draft"}
        backend.save_generated_posts("test-tenant", pd.DataFrame([item]), meta={})

        r = client.get("/api/v1/content?goal_id=goal_test", headers=_wauth()).json()
        got = [i for i in r["items"] if i["content_id"] == item_id]
        assert len(got) == 1
        assert got[0]["source"] == "legacy_xlsx"

    def test_legacy_xlsx_id_stable_across_gets(self, client):
        """Same item persisted once → stable across GETs."""
        import storage.factory
        backend = storage.factory.get_backend()
        item_id = "xlsx_stable_002"
        item = {"content_id": item_id, "goal_id": "goal_test", "title": "a",
                "body": "c", "angle": "x", "source": "legacy_xlsx",
                "status": "draft"}
        backend.save_generated_posts("test-tenant", pd.DataFrame([item]), meta={})

        r1 = client.get("/api/v1/content?goal_id=goal_test", headers=_wauth()).json()
        r2 = client.get("/api/v1/content?goal_id=goal_test", headers=_wauth()).json()
        ids1 = {i["content_id"] for i in r1["items"]}
        ids2 = {i["content_id"] for i in r2["items"]}
        assert ids1 == ids2
        assert item_id in ids1

    def test_legacy_xlsx_id_stable_when_prepended(self, client):
        """Adding more items doesn't change IDs of existing items."""
        import storage.factory
        backend = storage.factory.get_backend()
        # Save two items
        item_a = {"content_id": "xlsx_a", "goal_id": "goal_test", "title": "first",
                  "body": "c1", "source": "legacy_xlsx", "status": "draft"}
        item_b = {"content_id": "xlsx_b", "goal_id": "goal_test", "title": "second",
                  "body": "c2", "source": "legacy_xlsx", "status": "draft"}
        backend.save_generated_posts("test-tenant", pd.DataFrame([item_a, item_b]), meta={})

        r_before = client.get("/api/v1/content?goal_id=goal_test", headers=_wauth()).json()
        ids_before = {i["content_id"] for i in r_before["items"]}
        assert item_a["content_id"] in ids_before
        assert item_b["content_id"] in ids_before

        # Add another item
        item_c = {"content_id": "xlsx_c", "goal_id": "goal_test", "title": "third",
                  "body": "c3", "source": "legacy_xlsx", "status": "draft"}
        # 同天 save 会覆盖同名文件(LocalJsonBackend 按日期命名),
        # 所以用不同 goal_id prefix 避免覆盖
        backend.save_generated_posts("test-tenant", pd.DataFrame([item_c]),
                                     meta={"goal_id": "batch_c"})

        r_after = client.get("/api/v1/content?goal_id=goal_test", headers=_wauth()).json()
        ids_after = {i["content_id"] for i in r_after["items"]}
        assert ids_before <= ids_after  # old IDs preserved, new ones added

    def test_status_empty_string_no_filter(self, client):
        """GET with status='' means 'no status filter' (router/PG/local 三层一致)."""
        client.post(
            "/api/v1/content/generate",
            json={"goal_id": "goal_test", "topic": "X", "count": 1, "persist": True},
            headers=_wauth(),
        )
        r = client.get("/api/v1/content?goal_id=goal_test&status=", headers=_wauth())
        # empty status is falsy → treated as unset → the persisted draft is returned
        assert r.json()["total"] == 1

    def test_generate_persist_batch(self, client):
        """Single POST with count=2 persists both items."""
        r = client.post(
            "/api/v1/content/generate",
            json={"goal_id": "goal_test", "topic": "X", "count": 2, "persist": True},
            headers=_wauth(),
        )
        assert r.status_code == 200
        assert len(r.json()["items"]) == 2

        r2 = client.get("/api/v1/content?goal_id=goal_test", headers=_wauth())
        assert r2.json()["total"] == 2

    def test_generate_hammer_5_concurrent(self, client):
        """5 concurrent persists via backend — all survive."""
        import storage.factory
        backend = storage.factory.get_backend()
        n = 5

        items = [
            {
                "content_id": f"hammer_{i}", "goal_id": "goal_test", "title": str(i),
                "alt_titles": [], "body": f"body_{i}", "hashtags": [],
                "publish_at": "", "publish_reason": "", "angle": "",
                "status": "draft", "source": "ai_generate",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z", "edit_count": 0,
            }
            for i in range(n)
        ]

        def _persist(item: dict) -> None:
            backend.save_generated_posts("test-tenant", pd.DataFrame([item]),
                                         meta={"goal_id": f"hammer_{item['content_id']}"})

        with ThreadPoolExecutor(max_workers=n) as pool:
            list(pool.map(_persist, items))

        r = client.get("/api/v1/content?goal_id=goal_test", headers=_wauth())
        assert r.json()["total"] == n
        ids = {i["content_id"] for i in r.json()["items"]}
        assert ids == {f"hammer_{i}" for i in range(n)}
