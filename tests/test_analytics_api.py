"""P3.2 · performance 回填端点测试（local backend 真跑）。

POST /api/v1/analytics/performance
  body { content_id, likes, comments_count, shares, collects, follows }
  - 计算 CES = likes + collects + comments*4 + shares*4 + follows*8
  - 写回 generated_content.meta.ces_score（OCC）
  - 更新 goals.used_angles 里对应 angle 的 last_ces / evidence_count
  - 走 IdempotencyRoute + JWT
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from security.jwt import encode_token

JWT = encode_token("default")
AUTH = {"Authorization": f"Bearer {JWT}"}


def _wauth() -> dict:
    return {**AUTH, "Idempotency-Key": uuid.uuid4().hex}


@pytest.fixture()
def client(tmp_path):
    from server.middleware.idempotency import clear_idempotency_caches_for_tests
    clear_idempotency_caches_for_tests()

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "settings.json").write_text(
        json.dumps({"llm_provider": "mock"}), encoding="utf-8"
    )
    goals = {
        "active_goal_id": "g1",
        "goals": [{
            "id": "g1", "name": "t", "status": "active",
            "used_angles": [
                {"angle": "反直觉型", "status": "unknown", "evidence_count": 0, "last_ces": None},
            ],
        }],
    }
    (cfg_dir / "goals.json").write_text(json.dumps(goals, ensure_ascii=False), encoding="utf-8")

    from storage.local_json import LocalJsonBackend
    backend = LocalJsonBackend(base_dir=str(tmp_path))
    # 预置一篇 generated post（角度=反直觉型）
    backend.save_generated_posts("default", pd.DataFrame([{
        "content_id": "c1", "goal_id": "g1", "title": "x", "body": "b",
        "angle": "反直觉型", "status": "draft",
    }]), meta={"goal_id": "g1"})

    def _mock_backend():
        return backend

    with patch("storage.factory.get_backend", _mock_backend), \
         patch("server.routers.analytics.CONFIG_DIR", cfg_dir):
        from server.main import app
        with TestClient(app) as c:
            c._backend = backend  # type: ignore[attr-defined]
            yield c


def test_performance_computes_ces(client):
    r = client.post("/api/v1/analytics/performance", headers=_wauth(), json={
        "content_id": "c1", "likes": 10, "collects": 5,
        "comments_count": 3, "shares": 2, "follows": 1,
    })
    assert r.status_code == 200, r.text
    # CES = 10 + 5 + 3*4 + 2*4 + 1*8 = 43
    assert r.json()["ces_score"] == 43


def test_performance_writes_back_to_post(client):
    client.post("/api/v1/analytics/performance", headers=_wauth(), json={
        "content_id": "c1", "likes": 10, "collects": 5,
        "comments_count": 3, "shares": 2, "follows": 1,
    })
    post = client._backend.get_generated_post("default", "c1")
    assert post["meta"]["ces_score"] == 43


def test_performance_updates_used_angles(client):
    client.post("/api/v1/analytics/performance", headers=_wauth(), json={
        "content_id": "c1", "likes": 100, "collects": 0,
        "comments_count": 0, "shares": 0, "follows": 0,
    })
    goals = client._backend.load_goals("default")
    ua = goals["goals"][0]["used_angles"]
    entry = next(e for e in ua if e["angle"] == "反直觉型")
    assert entry["last_ces"] == 100
    assert entry["evidence_count"] == 1


def test_performance_unknown_content_404(client):
    r = client.post("/api/v1/analytics/performance", headers=_wauth(), json={
        "content_id": "nope", "likes": 1, "collects": 0,
        "comments_count": 0, "shares": 0, "follows": 0,
    })
    assert r.status_code == 404


def test_performance_requires_idempotency_key(client):
    r = client.post("/api/v1/analytics/performance", headers=AUTH, json={
        "content_id": "c1", "likes": 1, "collects": 0,
        "comments_count": 0, "shares": 0, "follows": 0,
    })
    assert r.status_code == 428


def test_performance_requires_auth(client):
    r = client.post("/api/v1/analytics/performance", json={
        "content_id": "c1", "likes": 1, "collects": 0,
        "comments_count": 0, "shares": 0, "follows": 0,
    })
    assert r.status_code == 401


def test_performance_second_submit_updates_latest(client):
    client.post("/api/v1/analytics/performance", headers=_wauth(), json={
        "content_id": "c1", "likes": 10, "collects": 0,
        "comments_count": 0, "shares": 0, "follows": 0,
    })
    client.post("/api/v1/analytics/performance", headers=_wauth(), json={
        "content_id": "c1", "likes": 50, "collects": 0,
        "comments_count": 0, "shares": 0, "follows": 0,
    })
    post = client._backend.get_generated_post("default", "c1")
    assert post["meta"]["ces_score"] == 50  # latest wins
