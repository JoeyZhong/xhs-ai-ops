"""Tests for Content Loop P0 — packaging rules loader."""
from __future__ import annotations

from pathlib import Path

import pytest


def test_load_packaging_rules_returns_nonempty_markdown():
    from agent_tools.packaging_rules import load_packaging_rules
    text = load_packaging_rules()
    assert isinstance(text, str)
    assert "五大爆文标题公式" in text
    assert "CES" in text
    assert "反直觉型" in text


def test_load_packaging_rules_reflects_file_mtime_changes(tmp_path, monkeypatch):
    """改文件后再次读取必须拿到新内容（基于 mtime 而非永久缓存）。"""
    from agent_tools import packaging_rules as pr

    fake = tmp_path / "packaging_rules.md"
    fake.write_text("V1 内容", encoding="utf-8")
    monkeypatch.setattr(pr, "_RULES_PATH", fake)
    pr.load_packaging_rules.cache_clear()  # 清测试前的缓存

    assert pr.load_packaging_rules() == "V1 内容"

    # 改文件，模拟运营人编辑后保存
    import time; time.sleep(0.01)
    fake.write_text("V2 内容", encoding="utf-8")
    # 触发 mtime 变化的失效
    assert pr.load_packaging_rules() == "V2 内容"


# ── prompt_context 聚合器 ──────────────────────────────────────

_GOAL_FIXTURE = {
    "id": "goal_001",
    "brand_position": "深圳本土自助售卖机运营商",
    "target_audience": {"who": "工厂老板", "pain_points": "闲置场地无收益"},
    "keywords": ["自助机点位招商"],
    "overall_strategy": {
        "core_message": "用闲置场地换被动收入",
        "content_funnel": {
            "top_30pct": "借餐饮选址高流量词引入泛流量",
            "mid_40pct": "行业干货建立专业信任",
            "bottom_30pct": "深圳本地化招商直接触达",
        },
    },
}


class _FakeBackend:
    def __init__(self, topic=None):
        self._topic = topic

    def get_topic(self, tenant_id, topic_id):
        if self._topic and self._topic["topic_id"] == topic_id:
            return dict(self._topic)
        raise KeyError(topic_id)


def test_prompt_context_no_topic_id_falls_back_to_no_funnel():
    from agent_tools.prompt_context import build_strategy_prompt_context
    ctx = build_strategy_prompt_context(
        backend=_FakeBackend(), tenant_id="t1", goal=_GOAL_FIXTURE, topic_id=None,
    )
    assert ctx["funnel_stage"] is None
    assert ctx["funnel_strategy_text"] == ""
    assert ctx["core_message"] == "用闲置场地换被动收入"
    assert "反直觉型" in ctx["packaging_rules"]


def test_prompt_context_with_topic_traffic_picks_top_30pct():
    from agent_tools.prompt_context import build_strategy_prompt_context
    topic = {"topic_id": "topic_x", "funnel_stage": "traffic"}
    ctx = build_strategy_prompt_context(
        backend=_FakeBackend(topic), tenant_id="t1", goal=_GOAL_FIXTURE, topic_id="topic_x",
    )
    assert ctx["funnel_stage"] == "traffic"
    assert ctx["funnel_strategy_text"] == "借餐饮选址高流量词引入泛流量"


def test_prompt_context_with_topic_trust_picks_mid_40pct():
    from agent_tools.prompt_context import build_strategy_prompt_context
    topic = {"topic_id": "topic_y", "funnel_stage": "trust"}
    ctx = build_strategy_prompt_context(
        backend=_FakeBackend(topic), tenant_id="t1", goal=_GOAL_FIXTURE, topic_id="topic_y",
    )
    assert ctx["funnel_stage"] == "trust"
    assert ctx["funnel_strategy_text"] == "行业干货建立专业信任"


def test_prompt_context_with_topic_conversion_picks_bottom_30pct():
    from agent_tools.prompt_context import build_strategy_prompt_context
    topic = {"topic_id": "topic_z", "funnel_stage": "conversion"}
    ctx = build_strategy_prompt_context(
        backend=_FakeBackend(topic), tenant_id="t1", goal=_GOAL_FIXTURE, topic_id="topic_z",
    )
    assert ctx["funnel_stage"] == "conversion"
    assert ctx["funnel_strategy_text"] == "深圳本地化招商直接触达"


def test_prompt_context_topic_not_found_is_graceful():
    """topic_id 传错时不爆，回退为无 funnel 模式（仍带 overall + packaging）。"""
    from agent_tools.prompt_context import build_strategy_prompt_context
    ctx = build_strategy_prompt_context(
        backend=_FakeBackend(), tenant_id="t1", goal=_GOAL_FIXTURE, topic_id="nonexistent",
    )
    assert ctx["funnel_stage"] is None
    assert ctx["funnel_strategy_text"] == ""
    assert ctx["core_message"] == "用闲置场地换被动收入"


def test_prompt_context_missing_overall_strategy_is_graceful():
    from agent_tools.prompt_context import build_strategy_prompt_context
    goal_minimal = {"id": "g0", "brand_position": "x", "target_audience": {}, "keywords": []}
    ctx = build_strategy_prompt_context(
        backend=_FakeBackend(), tenant_id="t1", goal=goal_minimal, topic_id=None,
    )
    assert ctx["funnel_stage"] is None
    assert ctx["funnel_strategy_text"] == ""
    assert ctx["core_message"] == ""
    assert "反直觉型" in ctx["packaging_rules"]


# ── /content/strategy prompt 注入验证 ─────────────────────────

@pytest.fixture
def app_client_with_mock_kimi(tmp_path, monkeypatch):
    """挂起 FastAPI app + mock Kimi + goal+topic 灌入 LocalJsonBackend。"""
    import os
    os.environ.setdefault("STORAGE_BACKEND", "local")
    os.environ.setdefault("JWT_SECRET", "test-secret-for-prompt-loop-tests")
    os.environ.setdefault("MASTER_ENCRYPTION_KEY", "test-master-key-32-bytes-min-len!")

    # 改 settings.json 路径指向 tmp
    settings = tmp_path / "settings.json"
    settings.write_text('{"llm_provider": "kimi"}', encoding="utf-8")

    from server.routers import content as content_router
    from server.routers import goals as goals_router
    monkeypatch.setattr(content_router, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(goals_router, "CONFIG_DIR", tmp_path)

    # mock call_kimi 让它捕获 prompt 并返回固定 JSON
    captured = {}
    def fake_call_kimi(prompt, max_tokens=1500, json_mode=False):
        captured["prompt"] = prompt
        return ('{"angle":"反直觉型","hook":"h","key_points":["k"],"cta":"c"}', None)
    monkeypatch.setattr("agent_tools.kimi.call_kimi", fake_call_kimi)

    # 灌 goal + topic
    from storage.local_json import LocalJsonBackend
    import storage.factory
    backend = LocalJsonBackend(base_dir=str(tmp_path))
    monkeypatch.setattr(storage.factory, "get_backend", lambda: backend)

    backend.save_goals("default", {
        "active_goal_id": "goal_001",
        "goals": [_GOAL_FIXTURE],
    })
    topic = backend.create_topic(
        "default", title="点位评分表", goal_id="goal_001",
        angle="工具型", funnel_stage="trust", source="manual",
    )

    from fastapi.testclient import TestClient
    from server.main import app
    from security.jwt import encode_token
    tok = encode_token("default")
    client = TestClient(app)

    return client, tok, topic["topic_id"], captured


def test_strategy_endpoint_injects_funnel_text_for_trust_topic(app_client_with_mock_kimi):
    import uuid
    client, tok, topic_id, captured = app_client_with_mock_kimi
    r = client.post(
        "/api/v1/content/strategy",
        headers={"Authorization": f"Bearer {tok}", "Idempotency-Key": str(uuid.uuid4())},
        json={"goal_id": "goal_001", "keywords": ["k"], "user_intent": "u", "topic_id": topic_id},
    )
    assert r.status_code == 200, r.text
    assert "行业干货建立专业信任" in captured["prompt"]  # mid_40pct 选中
    assert "用闲置场地换被动收入" in captured["prompt"]  # core_message 注入
    assert "反直觉型" in captured["prompt"]              # packaging 注入


def test_strategy_endpoint_without_topic_id_still_injects_overall_and_packaging(app_client_with_mock_kimi):
    import uuid
    client, tok, _topic_id, captured = app_client_with_mock_kimi
    r = client.post(
        "/api/v1/content/strategy",
        headers={"Authorization": f"Bearer {tok}", "Idempotency-Key": str(uuid.uuid4())},
        json={"goal_id": "goal_001", "keywords": ["k"], "user_intent": "u"},
    )
    assert r.status_code == 200
    assert "用闲置场地换被动收入" in captured["prompt"]
    assert "反直觉型" in captured["prompt"]
    # 没有 topic 时不应该硬塞某一层 funnel 字符串
    assert "行业干货建立专业信任" not in captured["prompt"]


def test_generate_endpoint_injects_funnel_text_for_traffic_topic(tmp_path, monkeypatch):
    import os, uuid
    os.environ.setdefault("STORAGE_BACKEND", "local")
    os.environ.setdefault("JWT_SECRET", "test-secret-for-prompt-loop-tests")
    os.environ.setdefault("MASTER_ENCRYPTION_KEY", "test-master-key-32-bytes-min-len!")

    settings = tmp_path / "settings.json"
    settings.write_text('{"llm_provider": "kimi"}', encoding="utf-8")
    from server.routers import content as content_router
    monkeypatch.setattr(content_router, "CONFIG_DIR", tmp_path)

    captured = {}
    def fake_call_kimi(prompt, max_tokens=3000, json_mode=False):
        captured["prompt"] = prompt
        return ('[{"title":"t","body":"b","hashtags":["h"],"publish_at":"12:00","angle":"反直觉型"}]', None)
    monkeypatch.setattr("agent_tools.kimi.call_kimi", fake_call_kimi)

    from storage.local_json import LocalJsonBackend
    import storage.factory
    backend = LocalJsonBackend(base_dir=str(tmp_path))
    monkeypatch.setattr(storage.factory, "get_backend", lambda: backend)
    backend.save_goals("default", {"active_goal_id": "goal_001", "goals": [_GOAL_FIXTURE]})
    topic = backend.create_topic(
        "default", title="餐饮选址", goal_id="goal_001",
        angle="本地汇总型", funnel_stage="traffic", source="manual",
    )

    from fastapi.testclient import TestClient
    from server.main import app
    from security.jwt import encode_token
    tok = encode_token("default")
    client = TestClient(app)

    r = client.post(
        "/api/v1/content/generate",
        headers={"Authorization": f"Bearer {tok}", "Idempotency-Key": str(uuid.uuid4())},
        json={
            "goal_id": "goal_001", "topic": "餐饮选址技巧", "strategy": {"angle": "反直觉型"},
            "count": 1, "persist": False, "topic_id": topic["topic_id"],
        },
    )
    assert r.status_code == 200, r.text
    assert "借餐饮选址高流量词引入泛流量" in captured["prompt"]
    assert "用闲置场地换被动收入" in captured["prompt"]
    assert "反直觉型" in captured["prompt"]
