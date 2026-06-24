from __future__ import annotations

import os
import uuid
from typing import Any

from fastapi.testclient import TestClient

from security.jwt import encode_token

os.environ.setdefault("JWT_SECRET", "test-secret-for-content-evidence")
os.environ.setdefault("JWT_ALGORITHM", "HS256")


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


def _seed_backend(tmp_path, monkeypatch, *, with_evidence: bool):
    from storage.local_json import LocalJsonBackend
    import storage.factory

    backend = LocalJsonBackend(base_dir=str(tmp_path))
    monkeypatch.setattr(storage.factory, "get_backend", lambda: backend)
    backend.save_goals("default", {"active_goal_id": "goal_001", "goals": [_GOAL_FIXTURE]})
    topic = backend.create_topic(
        "default",
        title="点位评分表",
        goal_id="goal_001",
        angle="工具型",
        funnel_stage="trust",
        source="manual",
    )
    if with_evidence:
        backend.upsert_evidence("default", {
            "source_note_id": "n-e1",
            "angle": "工具型",
            "funnel_stage": "trust",
            "hook": "先看消费时段，再谈人流",
            "key_insight": "高互动样本会用点位评分表降低招商感。",
            "ces_score": 620,
            "raw": {"title": "点位评分表"},
        })
    return backend, topic["topic_id"]


def _setup_content_router(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text('{"llm_provider":"kimi"}', encoding="utf-8")
    from server.routers import content as content_router
    monkeypatch.setattr(content_router, "CONFIG_DIR", tmp_path)


def test_prompt_context_includes_evidence_refs_for_topic_funnel(tmp_path, monkeypatch):
    backend, topic_id = _seed_backend(tmp_path, monkeypatch, with_evidence=True)

    from agent_tools.prompt_context import build_strategy_prompt_context

    ctx = build_strategy_prompt_context(
        backend=backend,
        tenant_id="default",
        goal=_GOAL_FIXTURE,
        topic_id=topic_id,
    )

    assert ctx["funnel_stage"] == "trust"
    assert len(ctx["evidence_refs"]) == 1
    assert ctx["evidence_refs"][0]["hook"] == "先看消费时段，再谈人流"


def test_strategy_prompt_injects_evidence_after_packaging(tmp_path, monkeypatch):
    _setup_content_router(tmp_path, monkeypatch)
    _backend, topic_id = _seed_backend(tmp_path, monkeypatch, with_evidence=True)
    captured: dict[str, str] = {}

    def fake_call_kimi(prompt: str, **kwargs: Any):
        captured["prompt"] = prompt
        return '{"angle":"工具型","hook":"h","key_points":["k"],"cta":"c"}', None

    monkeypatch.setattr("agent_tools.kimi.call_kimi", fake_call_kimi)

    from server.main import app
    client = TestClient(app)
    token = encode_token("default")
    response = client.post(
        "/api/v1/content/strategy",
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": f"strategy-{uuid.uuid4().hex}",
        },
        json={
            "goal_id": "goal_001",
            "keywords": ["自助机点位招商"],
            "user_intent": "找深圳工厂点位",
            "topic_id": topic_id,
        },
    )

    assert response.status_code == 200, response.text
    prompt = captured["prompt"]
    assert "── 同 funnel/同 angle 爆款样本 ──" in prompt
    assert "先看消费时段，再谈人流" in prompt
    assert prompt.index("── 包装规则") < prompt.index("── 同 funnel/同 angle 爆款样本")
    assert prompt.index("── 同 funnel/同 angle 爆款样本") < prompt.index("关键词：")


def test_generate_prompt_omits_evidence_block_when_missing(tmp_path, monkeypatch):
    _setup_content_router(tmp_path, monkeypatch)
    _backend, topic_id = _seed_backend(tmp_path, monkeypatch, with_evidence=False)
    captured: dict[str, str] = {}

    def fake_call_kimi(prompt: str, **kwargs: Any):
        captured["prompt"] = prompt
        return '[{"title":"t","body":"b","hashtags":["h"],"publish_at":"12:00","angle":"工具型"}]', None

    monkeypatch.setattr("agent_tools.kimi.call_kimi", fake_call_kimi)

    from server.main import app
    client = TestClient(app)
    token = encode_token("default")
    response = client.post(
        "/api/v1/content/generate",
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": f"generate-{uuid.uuid4().hex}",
        },
        json={
            "goal_id": "goal_001",
            "topic": "点位评分表",
            "strategy": {"angle": "工具型"},
            "count": 1,
            "persist": False,
            "topic_id": topic_id,
        },
    )

    assert response.status_code == 200, response.text
    assert "── 同 funnel/同 angle 爆款样本 ──" not in captured["prompt"]
