"""
F3 API 验收测试（TDD RED→GREEN）
运行：pytest tests/test_f3_api.py -v
"""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from security.jwt import encode_token

os.environ.setdefault("JWT_SECRET", "test_secret_for_p2_only")
os.environ.setdefault("JWT_ALGORITHM", "HS256")

import uuid as _uuid
from server.middleware.idempotency import clear_idempotency_caches_for_tests

# fixture writes goals to cfg_dir/goals.json (default path) → token "default"
JWT = encode_token("default")
AUTH = {"Authorization": f"Bearer {JWT}"}


def _wauth() -> dict:
    """Headers for write endpoints: auth + fresh Idempotency-Key (IdempotencyRoute)."""
    return {**AUTH, "Idempotency-Key": _uuid.uuid4().hex}


@pytest.fixture()
def client(tmp_path):
    clear_idempotency_caches_for_tests()
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    settings = {
        "kimi_api_key": "test-key",
        "kimi_base_url": "https://api.moonshot.cn/v1",
        "kimi_model": "moonshot-v1-32k",
        "llm_provider": "mock",
    }
    (cfg_dir / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

    goals_data = {
        "active_goal_id": "goal_test",
        "goals": [{
            "id": "goal_test", "name": "测试目标", "objective": "转化",
            "status": "active", "description": "test", "created_at": "2026-05-07",
            "target_audience": {"who": "工厂物业", "pain_points": "找不到好点位", "interests": "自动化"},
            "brand_position": "深圳自助售卖机运营商", "benchmark_accounts": [],
            "keywords": ["自助机点位"], "keyword_library": ["自助机点位招商"],
            "topic_library": [], "content_calendar": [], "used_angles": [],
            "campaigns": [], "persona_id": "p1", "overall_strategy": {},
            "performance": {"posts": []},
        }],
    }
    (cfg_dir / "goals.json").write_text(json.dumps(goals_data, ensure_ascii=False), encoding="utf-8")

    from storage.local_json import LocalJsonBackend
    test_backend = LocalJsonBackend(base_dir=str(tmp_path))

    def _mock_get_backend():
        return test_backend

    with patch("storage.factory.get_backend", _mock_get_backend), \
         patch("server.routers.goals.CONFIG_DIR", cfg_dir), \
         patch("server.routers.settings.CONFIG_DIR", cfg_dir), \
         patch("server.routers.topics.CONFIG_DIR", cfg_dir), \
         patch("server.routers.content.CONFIG_DIR", cfg_dir):
        from server.main import app
        with TestClient(app) as c:
            yield c


# ── S1 · Topics API ──────────────────────────────────────────────────────────

class TestTopicsAPI:
    def test_generate_topics_ok(self, client):
        r = client.post("/api/v1/topics/generate",
                        json={"goal_id": "goal_test"},
                        headers=_wauth())
        assert r.status_code == 200
        data = r.json()
        assert "topics" in data
        assert isinstance(data["topics"], list)
        assert len(data["topics"]) > 0

    def test_generate_topics_not_found(self, client):
        r = client.post("/api/v1/topics/generate",
                        json={"goal_id": "ghost"},
                        headers=_wauth())
        assert r.status_code == 404

    def test_generate_topics_count(self, client):
        r = client.post("/api/v1/topics/generate",
                        json={"goal_id": "goal_test", "count": 3},
                        headers=_wauth())
        assert r.status_code == 200
        assert len(r.json()["topics"]) <= 5  # mock 返回固定数量


# ── S2 · Content Generate API ────────────────────────────────────────────────

class TestContentAPI:
    def test_generate_content_ok(self, client):
        r = client.post("/api/v1/content/generate",
                        json={"goal_id": "goal_test", "topic": "测试选题", "strategy": "反直觉型"},
                        headers=_wauth())
        assert r.status_code == 200
        data = r.json()
        assert "items" in data
        assert isinstance(data["items"], list)
        assert len(data["items"]) > 0
        item = data["items"][0]
        assert "title" in item
        assert "body" in item

    def test_generate_content_goal_not_found(self, client):
        r = client.post("/api/v1/content/generate",
                        json={"goal_id": "ghost", "topic": "test", "strategy": "test"},
                        headers=_wauth())
        assert r.status_code == 404

    def test_list_content_empty(self, client):
        """无 xlsx 时返回空列表，不报错。"""
        r = client.get("/api/v1/content", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["items"] == []
        assert r.json()["total"] == 0
