"""Orchestrator HTTP route tests.

P2.2 rewires /converse to the coordination-loop run_turn fallback. Legacy
plan/confirm and decision endpoints are still covered with seeded sessions so
they do not depend on the old one-shot planner state machine.
"""
from __future__ import annotations

import os
import uuid

from fastapi.testclient import TestClient

from security.jwt import encode_token

os.environ.setdefault("JWT_SECRET", "test-secret-orchestrator")
os.environ.setdefault("JWT_ALGORITHM", "HS256")


def _seed(tmp_path, monkeypatch):
    from storage.local_json import LocalJsonBackend
    import storage.factory

    backend = LocalJsonBackend(base_dir=str(tmp_path))
    monkeypatch.setattr(storage.factory, "get_backend", lambda *a, **k: backend)
    backend.save_goals(
        "default",
        {
            "active_goal_id": "goal_001",
            "goals": [{"id": "goal_001", "name": "B端点位招商"}],
        },
    )
    return backend


def _client():
    from server.main import app
    return TestClient(app)


def _H(tenant_id: str = "default"):
    tok = encode_token(tenant_id)
    return {"Authorization": f"Bearer {tok}", "Idempotency-Key": uuid.uuid4().hex}


def _seed_planned_session(backend, tenant_id: str = "default", session_id: str = "os-planned"):
    return backend.create_session(
        tenant_id,
        session_id=session_id,
        goal_id="goal_001",
        status="planned",
        messages=[{"role": "user", "content": "规划本周内容"}],
        proposed_plan=[
            {"id": "task-1", "type": "intel", "prompt": "采集深圳工厂物业笔记", "blocked_by": []},
            {"id": "task-2", "type": "analyst", "prompt": "分析高 CES 共性", "blocked_by": ["task-1"]},
        ],
        decision_cards=[
            {
                "card_id": "dc-plan",
                "kind": "plan_approval",
                "title": "确认计划",
                "detail": "确认后提交 DAG",
                "options": ["approve", "reject"],
                "status": "pending",
            }
        ],
    )


def test_converse_non_streaming_fallback_returns_session_view(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    def fake_run_turn(*, backend, tenant_id, message, session_id, goal_id, emit, **_kwargs):
        captured.update(
            backend=backend,
            tenant_id=tenant_id,
            message=message,
            session_id=session_id,
            goal_id=goal_id,
        )
        final = {"type": "final", "seq": 1, "summary": "建议先采集再生成"}
        emit(final)
        return {
            "session_id": "os-fallback",
            "status": "done",
            "goal_id": goal_id,
            "messages": [{"role": "user", "content": message}],
            "trace": [final, {"type": "done", "seq": 2, "status": "done"}],
            "pending": None,
            "decision_cards": [],
            "dag_id": None,
        }

    monkeypatch.setattr("server.routers.orchestrator.run_turn", fake_run_turn)

    c = _client()
    r = c.post(
        "/api/v1/orchestrator/converse",
        headers=_H("tenant-route"),
        json={"message": "规划本周内容", "goal_id": "goal_001", "session_id": "os-existing"},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "done"
    assert [event["type"] for event in body["trace"]] == ["final", "done"]
    assert body["pending"] is None
    assert captured["tenant_id"] == "tenant-route"
    assert captured["session_id"] == "os-existing"


def test_get_session_recovers_trace_and_pending(tmp_path, monkeypatch):
    backend = _seed(tmp_path, monkeypatch)
    question = {"kind": "question", "question": "主推园区物业还是写字楼行政？"}
    backend.create_session(
        "default",
        session_id="os-awaiting",
        goal_id="goal_001",
        status="awaiting_user",
        messages=[{"role": "user", "content": "帮我搞批内容"}],
        trace=[
            {"type": "thinking", "seq": 1, "summary": "信息不足"},
            {"type": "awaiting_user", "seq": 2, "question": question["question"]},
        ],
        pending=question,
    )

    c = _client()
    r = c.get(
        "/api/v1/orchestrator/session/os-awaiting",
        headers={"Authorization": f"Bearer {encode_token('default')}"},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "awaiting_user"
    assert [event["type"] for event in body["trace"]] == ["thinking", "awaiting_user"]
    assert body["pending"] == question
    assert body["decision_cards"] == []
    assert body["dag_id"] is None


def test_confirm_dispatches_seeded_plan_via_submit_dag(tmp_path, monkeypatch):
    backend = _seed(tmp_path, monkeypatch)
    _seed_planned_session(backend)

    class _StubMaster:
        def __init__(self, *a, **k): pass
        def submit_dag(self, *a, **k): return []

    monkeypatch.setattr("agents.master.HermesMaster", _StubMaster)

    c = _client()
    r = c.post(
        "/api/v1/orchestrator/plan/confirm",
        headers=_H(),
        json={"session_id": "os-planned", "plan_card_decision": "approve"},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "dispatched"
    assert body["dag_id"].startswith("dag-")


def test_confirm_reject_cancels_seeded_plan(tmp_path, monkeypatch):
    backend = _seed(tmp_path, monkeypatch)
    _seed_planned_session(backend)

    c = _client()
    r = c.post(
        "/api/v1/orchestrator/plan/confirm",
        headers=_H(),
        json={"session_id": "os-planned", "plan_card_decision": "reject"},
    )

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"


def test_dispatched_state_recovers_after_refresh(tmp_path, monkeypatch):
    backend = _seed(tmp_path, monkeypatch)
    _seed_planned_session(backend)

    class _StubMaster:
        def __init__(self, *a, **k): pass
        def submit_dag(self, *a, **k): return []

    monkeypatch.setattr("agents.master.HermesMaster", _StubMaster)

    c = _client()
    dag_id = c.post(
        "/api/v1/orchestrator/plan/confirm",
        headers=_H(),
        json={"session_id": "os-planned", "plan_card_decision": "approve"},
    ).json()["dag_id"]

    r = c.get(
        "/api/v1/orchestrator/session/os-planned",
        headers={"Authorization": f"Bearer {encode_token('default')}"},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "dispatched"
    assert body["dag_id"] == dag_id
    assert body["trace"] == []
    assert body["pending"] is None
    assert any(
        card["kind"] == "plan_approval" and card["status"] == "approved"
        for card in body["decision_cards"]
    )


def test_decision_updates_card(tmp_path, monkeypatch):
    backend = _seed(tmp_path, monkeypatch)
    _seed_planned_session(backend)

    c = _client()
    r = c.post(
        "/api/v1/orchestrator/decision",
        headers=_H(),
        json={"session_id": "os-planned", "card_id": "dc-plan", "decision": "approve"},
    )

    assert r.status_code == 200, r.text
    cards = r.json()["decision_cards"]
    assert any(card["card_id"] == "dc-plan" and card["status"] == "approved" for card in cards)


def test_cross_tenant_session_isolation(tmp_path, monkeypatch):
    backend = _seed(tmp_path, monkeypatch)
    backend.create_session("default", session_id="os-isolated", status="done")

    c = _client()
    r = c.get(
        "/api/v1/orchestrator/session/os-isolated",
        headers={"Authorization": f"Bearer {encode_token('tenant-other')}"},
    )

    assert r.status_code == 404
