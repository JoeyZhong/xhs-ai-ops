"""
F3 Content Strategy API tests (TDD: red phase).
Run: pytest tests/test_f3_content_strategy.py -v
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from security.jwt import encode_token
from server.middleware.idempotency import clear_idempotency_caches_for_tests

os.environ.setdefault("JWT_SECRET", "test_secret_for_p2_only")
os.environ.setdefault("JWT_ALGORITHM", "HS256")

# tenant "default" → backend reads config/goals.json directly (where fixture writes)
JWT = encode_token("default")
AUTH = {"Authorization": f"Bearer {JWT}"}


def _wauth() -> dict:
    """Headers for write endpoints: auth + fresh Idempotency-Key (IdempotencyRoute)."""
    return {**AUTH, "Idempotency-Key": uuid.uuid4().hex}


def _write_settings(tmp_path: Path) -> None:
    settings = {
        "kimi_api_key": "test-key",
        "kimi_base_url": "https://api.moonshot.cn/v1",
        "kimi_model": "moonshot-v1-32k",
        "llm_provider": "mock",
    }
    (tmp_path / "settings.json").write_text(json.dumps(settings), encoding="utf-8")


def _write_goals(tmp_path: Path) -> None:
    goals_data = {
        "active_goal_id": "goal_test",
        "goals": [
            {
                "id": "goal_test",
                "name": "test",
                "objective": "test",
                "status": "active",
                "description": "test",
                "created_at": "2026-05-07",
                "target_audience": {"who": "factory owners", "pain_points": "low efficiency"},
                "brand_position": "Shenzhen vending machine operator",
                "keywords": ["vending", "location"],
                "keyword_library": ["vending machine placement"],
            }
        ],
    }
    (tmp_path / "goals.json").write_text(json.dumps(goals_data, ensure_ascii=False), encoding="utf-8")


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


# ── S1 · POST /api/v1/content/strategy ─────────────────────────────────


class TestStrategyEndpoint:
    """POST /api/v1/content/strategy — generate content strategy."""

    def test_strategy_happy_path(self, client):
        """Mock provider returns structured strategy."""
        r = client.post(
            "/api/v1/content/strategy",
            json={
                "goal_id": "goal_test",
                "keywords": ["vending", "Shenzhen"],
                "user_intent": "attract factory owners",
            },
            headers=_wauth(),
        )
        assert r.status_code == 200
        body = r.json()
        assert "strategy" in body
        s = body["strategy"]
        assert "angle" in s
        assert "hook" in s
        assert "key_points" in s
        assert isinstance(s["key_points"], list)
        assert "cta" in s

    def test_strategy_goal_not_found(self, client):
        """Unknown goal_id → 404."""
        r = client.post(
            "/api/v1/content/strategy",
            json={"goal_id": "no_such_goal", "keywords": [], "user_intent": "test"},
            headers=_wauth(),
        )
        assert r.status_code == 404

    def test_strategy_no_auth(self, client):
        """Missing auth → 401."""
        r = client.post(
            "/api/v1/content/strategy",
            json={"goal_id": "goal_test", "keywords": [], "user_intent": "test"},
        )
        assert r.status_code == 401

    def test_strategy_mock_structure(self, client):
        """Mock strategy has correct field types."""
        r = client.post(
            "/api/v1/content/strategy",
            json={"goal_id": "goal_test", "keywords": [], "user_intent": "test"},
            headers=_wauth(),
        )
        s = r.json()["strategy"]
        assert isinstance(s["angle"], str) and len(s["angle"]) > 0
        assert isinstance(s["hook"], str) and len(s["hook"]) > 0
        assert len(s["key_points"]) >= 2
        assert isinstance(s["cta"], str) and len(s["cta"]) > 0


# ── S2 · POST /api/v1/content/generate — structured strategy ────────────


class TestGenerateStructuredStrategy:
    """POST /api/v1/content/generate with dict strategy."""

    def test_generate_with_structured_strategy(self, client):
        """strategy as dict {angle, hook, key_points, cta} works."""
        r = client.post(
            "/api/v1/content/generate",
            json={
                "goal_id": "goal_test",
                "topic": "test topic",
                "strategy": {
                    "angle": "反直觉型",
                    "hook": "你绝对想不到",
                    "key_points": ["point 1", "point 2"],
                    "cta": "评论区聊聊",
                },
                "count": 1,
                "persist": False,
            },
            headers=_wauth(),
        )
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 1
        # angle from strategy dict should be used
        assert items[0]["angle"] == "反直觉型"

    def test_generate_with_string_strategy_backward_compat(self, client):
        """strategy as str still works (backward compat)."""
        r = client.post(
            "/api/v1/content/generate",
            json={
                "goal_id": "goal_test",
                "topic": "test topic",
                "strategy": "反直觉型",
                "count": 1,
                "persist": False,
            },
            headers=_wauth(),
        )
        assert r.status_code == 200
        assert len(r.json()["items"]) == 1

    def test_generate_without_strategy_default(self, client):
        """No strategy field → default empty works."""
        r = client.post(
            "/api/v1/content/generate",
            json={"goal_id": "goal_test", "topic": "test topic", "count": 1, "persist": False},
            headers=_wauth(),
        )
        assert r.status_code == 200
        assert len(r.json()["items"]) == 1
