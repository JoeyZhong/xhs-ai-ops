from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import pytest


os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("JWT_SECRET", "test-secret-for-packaging-e2e")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("MASTER_ENCRYPTION_KEY", "test-master-key-32-bytes-min-len!")


_REQUIRED_TITLE = "\u4e94\u5927\u7206\u6587\u6807\u9898\u516c\u5f0f"

_BASELINE_RULES = (
    f"# {_REQUIRED_TITLE}\n\n"
    "baseline packaging rules\n\n"
    "## CES\n\n"
    "CES = likes + saves + comments * 4 + shares * 4 + follows * 8\n"
)

_GOAL_FIXTURE = {
    "id": "goal_001",
    "brand_position": "Shenzhen vending machine operator",
    "target_audience": {
        "who": "factory and office property managers",
        "pain_points": "idle spaces need non-rent income",
    },
    "keywords": ["vending machine site"],
    "overall_strategy": {
        "core_message": "turn idle space into passive operating income",
        "content_funnel": {
            "top_30pct": "traffic content",
            "mid_40pct": "trust-building content",
            "bottom_30pct": "conversion content",
        },
    },
}


@pytest.fixture(autouse=True)
def _clear_global_caches():
    from agent_tools import packaging_rules
    from server.middleware.idempotency import clear_idempotency_caches_for_tests

    clear_idempotency_caches_for_tests()
    packaging_rules.load_packaging_rules.cache_clear()
    yield
    clear_idempotency_caches_for_tests()
    packaging_rules.load_packaging_rules.cache_clear()


@pytest.fixture
def app_client_with_mock_kimi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings = tmp_path / "settings.json"
    settings.write_text('{"llm_provider": "kimi"}', encoding="utf-8")

    rules_path = tmp_path / "packaging_rules.md"
    rules_path.write_text(_BASELINE_RULES, encoding="utf-8")

    from agent_tools import packaging_rules
    from server.routers import content as content_router

    monkeypatch.setattr(packaging_rules, "_RULES_PATH", rules_path)
    packaging_rules.load_packaging_rules.cache_clear()
    monkeypatch.setattr(content_router, "CONFIG_DIR", tmp_path)

    captured: dict[str, str] = {}

    def fake_call_kimi(prompt: str, max_tokens: int = 1500, json_mode: bool = False):
        captured["prompt"] = prompt
        return (
            '{"angle":"tool","hook":"h","key_points":["k"],"cta":"c"}',
            None,
        )

    monkeypatch.setattr("agent_tools.kimi.call_kimi", fake_call_kimi)

    from storage.local_json import LocalJsonBackend
    import storage.factory

    backend = LocalJsonBackend(base_dir=str(tmp_path))
    monkeypatch.setattr(storage.factory, "get_backend", lambda: backend)
    backend.save_goals(
        "default",
        {
            "active_goal_id": "goal_001",
            "goals": [_GOAL_FIXTURE],
        },
    )
    topic = backend.create_topic(
        "default",
        title="site scoring",
        goal_id="goal_001",
        angle="tool",
        funnel_stage="trust",
        source="manual",
    )

    from fastapi.testclient import TestClient
    from security.jwt import encode_token
    from server.main import app

    token = encode_token("default")
    client = TestClient(app)
    return client, token, topic["topic_id"], captured, rules_path


def test_editing_packaging_rules_reflects_in_strategy_prompt(app_client_with_mock_kimi):
    from agent_tools import packaging_rules

    client, token, topic_id, captured, _rules_path = app_client_with_mock_kimi
    marker = f"packaging-e2e-marker-{uuid.uuid4().hex}"
    new_rules = (
        f"# {_REQUIRED_TITLE}\n\n"
        "1. counter-intuitive title pattern\n"
        f"2. {marker}\n\n"
        "## CES\n\n"
        "CES = likes + saves + comments * 4 + shares * 4 + follows * 8\n"
    )

    assert packaging_rules.load_packaging_rules() == _BASELINE_RULES
    time.sleep(0.02)

    put_resp = client.put(
        "/api/v1/packaging/rules",
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": f"packaging-put-{uuid.uuid4().hex}",
        },
        json={"rules": new_rules},
    )

    assert put_resp.status_code == 200, put_resp.text
    assert put_resp.json()["rules"] == new_rules
    assert packaging_rules.load_packaging_rules() == new_rules

    strategy_resp = client.post(
        "/api/v1/content/strategy",
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": f"strategy-{uuid.uuid4().hex}",
        },
        json={
            "goal_id": "goal_001",
            "keywords": ["site scoring"],
            "user_intent": "build trust",
            "topic_id": topic_id,
        },
    )

    assert strategy_resp.status_code == 200, strategy_resp.text
    assert marker in captured["prompt"]
