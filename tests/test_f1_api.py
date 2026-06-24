"""
F1 API 验收测试（TDD RED→GREEN）
运行：pytest tests/test_f1_api.py -v
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

# tenant "default" → backend reads config/*.json directly (where fixtures write)
JWT = encode_token("default")
AUTH = {"Authorization": f"Bearer {JWT}"}


def _wauth() -> dict:
    """Headers for write endpoints: auth + fresh Idempotency-Key (IdempotencyRoute)."""
    return {**AUTH, "Idempotency-Key": _uuid.uuid4().hex}


# ── fixture：每次测试用独立 tmp config 目录 ──────────────────────────────────

@pytest.fixture()
def client(tmp_path):
    """创建隔离 backend，返回 TestClient。"""
    clear_idempotency_caches_for_tests()
    # 准备 tmp config/data 目录
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    # settings.json（kimi 端点仍直接读 Path("config")，不改）
    settings = {
        "kimi_api_key": "test-key",
        "kimi_base_url": "https://api.moonshot.cn/v1",
        "kimi_model": "moonshot-v1-32k",
        "llm_provider": "mock",
    }
    (cfg_dir / "settings.json").write_text(
        json.dumps(settings), encoding="utf-8"
    )
    # goals.json（backend.load_goals 读这里）
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
                "target_audience": {"who": "a", "pain_points": "b", "interests": "c"},
                "brand_position": "test brand",
                "benchmark_accounts": [],
                "keywords": ["kw1"],
                "keyword_library": [],
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
    (cfg_dir / "goals.json").write_text(
        json.dumps(goals_data, ensure_ascii=False), encoding="utf-8"
    )
    # personas.json（multi-container，context.py _load_active_persona 直接读文件）
    personas_data = {
        "active_id": "p1",
        "personas": [
            {
                "id": "p1",
                "nickname": "测试账号",
                "background": "test",
                "style_notes": "",
                "tone": "",
                "system_prompt": "",
                "created_at": "2026-05-07",
            }
        ],
    }
    (cfg_dir / "personas.json").write_text(
        json.dumps(personas_data, ensure_ascii=False), encoding="utf-8"
    )

    from storage.local_json import LocalJsonBackend
    from storage.factory import _BACKEND, _LAST_TYPE
    import storage.factory as _factory
    test_backend = LocalJsonBackend(base_dir=str(tmp_path))

    def _mock_get_backend():
        return test_backend

    with patch("storage.factory.get_backend", _mock_get_backend), \
         patch("server.routers.goals.CONFIG_DIR", cfg_dir), \
         patch("server.routers.personas.CONFIG_DIR", cfg_dir), \
         patch("server.routers.settings.CONFIG_DIR", cfg_dir), \
         patch("agents.context.CONFIG_DIR", cfg_dir):
        from server.main import app
        with TestClient(app) as c:
            yield c


# ── S1 · Auth 中间件 ─────────────────────────────────────────────────────────

class TestAuth:
    def test_health_no_token(self, client):
        """health 端点公开，无 token 也能访问。"""
        r = client.get("/api/v1/health")
        assert r.status_code == 200

    def test_goals_no_token_401(self, client):
        r = client.get("/api/v1/goals")
        assert r.status_code == 401

    def test_goals_wrong_token_401(self, client):
        r = client.get("/api/v1/goals", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401

    def test_goals_correct_token_200(self, client):
        r = client.get("/api/v1/goals", headers=_wauth())
        assert r.status_code == 200


# ── S2 · Goals API ──────────────────────────────────────────────────────────

class TestGoals:
    def test_list_goals(self, client):
        r = client.get("/api/v1/goals", headers=_wauth())
        assert r.status_code == 200
        data = r.json()
        assert "goals" in data
        assert data["active_goal_id"] == "goal_test"
        assert len(data["goals"]) == 1

    def test_get_goal_found(self, client):
        r = client.get("/api/v1/goals/goal_test", headers=_wauth())
        assert r.status_code == 200
        assert r.json()["id"] == "goal_test"

    def test_get_goal_not_found(self, client):
        r = client.get("/api/v1/goals/nonexistent", headers=_wauth())
        assert r.status_code == 404

    def test_create_goal(self, client):
        payload = {
            "name": "新目标",
            "objective": "品牌",
            "description": "desc",
        }
        r = client.post("/api/v1/goals", json=payload, headers=_wauth())
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "新目标"
        assert "id" in body

    def test_create_goal_missing_name(self, client):
        """name 是必填 → 422"""
        r = client.post("/api/v1/goals", json={"objective": "test"}, headers=_wauth())
        assert r.status_code == 422

    def test_create_goal_full_fields(self, client):
        """创建后返回含 id 的完整对象，且持久化后可读取"""
        payload = {
            "name": "完整测试",
            "objective": "B端转化",
            "description": "详细说明",
        }
        r = client.post("/api/v1/goals", json=payload, headers=_wauth())
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "完整测试"
        assert body["objective"] == "B端转化"
        assert body["description"] == "详细说明"
        assert body["status"] == "active"
        assert "id" in body
        # 持久化验证：GET 列表包含新目标
        r2 = client.get("/api/v1/goals", headers=_wauth())
        ids = [g["id"] for g in r2.json()["goals"]]
        assert body["id"] in ids

    def test_update_goal(self, client):
        r = client.put(
            "/api/v1/goals/goal_test",
            json={"name": "更新后名称", "keywords": ["新关键词"]},
            headers=_wauth(),
        )
        assert r.status_code == 200
        assert r.json()["name"] == "更新后名称"
        assert "新关键词" in r.json()["keywords"]

    def test_update_goal_not_found(self, client):
        r = client.put("/api/v1/goals/ghost", json={"name": "x"}, headers=_wauth())
        assert r.status_code == 404

    def test_generate_strategy_ok(self, client):
        """mock provider → 返回包含三层漏斗的策略。"""
        r = client.post("/api/v1/goals/goal_test/strategy/generate", headers=_wauth())
        assert r.status_code == 200
        body = r.json()
        assert "strategy" in body
        s = body["strategy"]
        assert "core_message" in s
        assert "content_funnel" in s
        assert "top_30pct" in s["content_funnel"]
        assert "mid_40pct" in s["content_funnel"]
        assert "bottom_30pct" in s["content_funnel"]

    def test_generate_strategy_not_found(self, client):
        r = client.post("/api/v1/goals/ghost/strategy/generate", headers=_wauth())
        assert r.status_code == 404


# ── S3 · Personas API ───────────────────────────────────────────────────────

class TestPersonas:
    def test_list_personas(self, client):
        r = client.get("/api/v1/personas", headers=_wauth())
        assert r.status_code == 200
        data = r.json()
        assert "personas" in data
        assert data["active_id"] == "p1"

    def test_create_persona(self, client):
        r = client.post(
            "/api/v1/personas",
            json={"nickname": "新账号", "background": "bg", "style_notes": ""},
            headers=_wauth(),
        )
        assert r.status_code == 201
        assert r.json()["nickname"] == "新账号"

    def test_update_persona(self, client):
        r = client.put(
            "/api/v1/personas/p1",
            json={"nickname": "更新账号"},
            headers=_wauth(),
        )
        assert r.status_code == 200
        assert r.json()["nickname"] == "更新账号"

    def test_activate_persona(self, client):
        r = client.post("/api/v1/personas/p1/activate", headers=_wauth())
        assert r.status_code == 200
        assert r.json()["active_id"] == "p1"

    def test_activate_not_found(self, client):
        r = client.post("/api/v1/personas/ghost/activate", headers=_wauth())
        assert r.status_code == 404


# ── S4 · Notes API ──────────────────────────────────────────────────────────

class TestNotes:
    def test_notes_empty(self, client):
        """无 xlsx 时返回空列表，不报错。"""
        r = client.get("/api/v1/notes", headers=_wauth())
        assert r.status_code == 200
        assert r.json()["notes"] == []
        assert r.json()["total"] == 0

    def test_notes_ces_score_formula(self, client):
        """CES 计算正确：1L+1C+4Cm+4S+8F。"""
        import pandas as pd
        import storage.factory
        backend = storage.factory.get_backend()
        df = pd.DataFrame([{
            "note_id": "ces_test_note",
            "goal_id": "test_goal",
            "keyword": "ces_test",
            "标题": "测试笔记",
            "点赞数": 10, "收藏数": 5, "评论数": 3,
            "分享数": 2, "关注数": 1,
        }])
        backend.save_collected_data("default", "ces_test", df)

        r = client.get("/api/v1/notes?goal_id=default", headers=_wauth())
        assert r.status_code == 200
        notes = r.json()["notes"]
        assert len(notes) == 1
        # CES = 10*1 + 5*1 + 3*4 + 2*4 + 1*8 = 10+5+12+8+8 = 43
        assert notes[0]["ces_score"] == 43

    def test_notes_handles_nan_interaction_columns(self, client):
        """模拟 xlsx 中未公开互动数为 NaN → _calc_ces 不抛 500。"""
        import pandas as pd
        import storage.factory
        backend = storage.factory.get_backend()
        # JWT tenant="default"，数据写入对应的租户目录
        tenant_dir = backend._data_path("default")
        df = pd.DataFrame([
            {"笔记ID": "n1", "点赞数": 100, "收藏数": 20,
             "评论数": 5, "分享数": 2},
            {"笔记ID": "n2", "点赞数": float("nan"), "收藏数": float("nan"),
             "评论数": float("nan"), "分享数": float("nan")},
        ])
        xlsx_path = tenant_dir / "spider_xhs_采集结果_20260525_999999.xlsx"
        df.to_excel(xlsx_path, index=False, sheet_name="采集数据")

        r = client.get("/api/v1/notes?goal_id=", headers=_wauth())
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 2
        # NaN 行的 ces_score 应为 0
        nan_row = next(n for n in body["notes"] if n["笔记ID"] == "n2")
        assert nan_row["ces_score"] == 0


# ── S5 · Settings API ───────────────────────────────────────────────────────

class TestSettings:
    def test_kimi_test_no_real_call(self, client):
        """mock provider → kimi test 端点返回 200 或合理错误。"""
        r = client.get("/api/v1/settings/kimi/test", headers=_wauth())
        # mock provider 无真实 key，可能返回 200 ok:false 或直接 200 ok:true
        assert r.status_code == 200
        assert "ok" in r.json()

    def test_save_kimi_key(self, client):
        r = client.post(
            "/api/v1/settings/kimi",
            json={"api_key": "sk-new-key", "model": "moonshot-v1-8k"},
            headers=_wauth(),
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_cookie_status_no_db(self, client):
        """无 cookies.db 时返回 valid=False，不崩。"""
        r = client.get("/api/v1/settings/cookie/status", headers=_wauth())
        assert r.status_code == 200
        body = r.json()
        assert "valid" in body

    def test_save_cookie(self, client):
        r = client.post(
            "/api/v1/settings/cookie",
            json={"account_id": "test_account", "cookie": "session=abc123"},
            headers=_wauth(),
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True
