from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from security.jwt import encode_token
from server.middleware.idempotency import clear_idempotency_caches_for_tests

os.environ.setdefault("JWT_SECRET", "test-secret-for-intel-router")
os.environ.setdefault("JWT_ALGORITHM", "HS256")


class _FakeIntelBackend:
    def __init__(self) -> None:
        self.evidence: dict[str, dict] = {}
        self.collected = pd.DataFrame([
            {
                "note_id": "n-hot",
                "title": "人流大不等于点位好",
                "content": "真实复盘：一个看似黄金的写字楼点位反而亏钱。",
                "ces_score": 520,
                "funnel_stage": "trust",
            },
            {
                "note_id": "n-cold",
                "title": "低互动笔记",
                "content": "互动太低，不该提取。",
                "ces_score": 12,
                "funnel_stage": "traffic",
            },
        ])

    def list_collected_data(self, tenant_id: str, since: datetime, goal_id=None):
        return self.collected.copy()

    def list_evidence(self, tenant_id: str, *, angle=None, funnel_stage=None, limit=3):
        items = list(self.evidence.values())
        if angle is not None:
            items = [i for i in items if i.get("angle") == angle]
        if funnel_stage is not None:
            items = [i for i in items if i.get("funnel_stage") == funnel_stage]
        items.sort(key=lambda x: -(x.get("ces_score") or 0))
        return items[:limit]

    def upsert_evidence(self, tenant_id: str, evidence: dict) -> dict:
        row = {**evidence, "tenant_id": tenant_id}
        self.evidence[row["source_note_id"]] = row
        return dict(row)


@pytest.fixture
def intel_client(tmp_path, monkeypatch):
    clear_idempotency_caches_for_tests()
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps({"ces_thresholds": {"evidence_extraction_min": 250}}),
        encoding="utf-8",
    )

    backend = _FakeIntelBackend()
    monkeypatch.setattr("storage.factory.get_backend", lambda: backend)

    def fake_call_kimi(prompt: str, **kwargs: Any):
        return json.dumps([
            {
                "source_note_id": "n-hot",
                "angle": "反直觉型",
                "funnel_stage": "trust",
                "hook": "人流大，不代表售货机就能赚钱",
                "key_insight": "高互动内容会把点位选择从人流量拉回消费时段和补货成本。",
            }
        ], ensure_ascii=False), None

    monkeypatch.setattr("agent_tools.kimi.call_kimi", fake_call_kimi)

    from server.routers import intel as intel_router
    monkeypatch.setattr(intel_router, "CONFIG_DIR", tmp_path)

    from server.main import app
    token = encode_token("tenant-a")
    return TestClient(app), {"Authorization": f"Bearer {token}"}, backend


def test_extract_endpoint_uses_jwt_and_idempotency_route(intel_client):
    client, headers, backend = intel_client
    response = client.post(
        "/api/v1/intel/evidence/extract",
        headers={**headers, "Idempotency-Key": f"intel-{uuid.uuid4().hex}"},
        json={"batch_size": 10},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["extracted_count"] == 1
    assert body["skipped_count"] == 1
    assert body["errors"] == []
    assert "n-hot" in backend.evidence


def test_extract_endpoint_requires_idempotency_key(intel_client):
    client, headers, _backend = intel_client
    response = client.post(
        "/api/v1/intel/evidence/extract",
        headers=headers,
        json={"batch_size": 10},
    )

    assert response.status_code == 428
    assert response.json()["error"]["code"] == "missing_idempotency_key"


def test_list_endpoint_filters_evidence(intel_client):
    client, headers, backend = intel_client
    backend.upsert_evidence("tenant-a", {
        "source_note_id": "n1",
        "angle": "工具型",
        "funnel_stage": "trust",
        "hook": "评分表先行",
        "key_insight": "工具型样本",
        "ces_score": 410,
        "raw": {},
    })
    backend.upsert_evidence("tenant-a", {
        "source_note_id": "n2",
        "angle": "反直觉型",
        "funnel_stage": "traffic",
        "hook": "别只看人流",
        "key_insight": "traffic 样本",
        "ces_score": 500,
        "raw": {},
    })

    response = client.get(
        "/api/v1/intel/evidence",
        headers=headers,
        params={"funnel_stage": "trust", "limit": 10},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["source_note_id"] == "n1"
