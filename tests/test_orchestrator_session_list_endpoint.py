"""Session list + delete endpoints (chat history drawer)."""
from __future__ import annotations

import os
import uuid

from httpx import ASGITransport, AsyncClient

os.environ.setdefault("JWT_SECRET", "test-secret-session-list")
os.environ.setdefault("JWT_ALGORITHM", "HS256")

import storage.factory
from security.jwt import encode_token
from server.main import app
from storage.local_json import LocalJsonBackend


def _auth(tenant_id: str = "default") -> dict[str, str]:
    return {"Authorization": f"Bearer {encode_token(tenant_id)}"}


def _auth_mutating(tenant_id: str = "default") -> dict[str, str]:
    # IdempotencyRoute 要求变更类方法（含 DELETE）带 Idempotency-Key（前端 writeRequest 也是这么做）。
    return {**_auth(tenant_id), "Idempotency-Key": uuid.uuid4().hex}


async def test_list_sessions_endpoint_derives_title_and_filters(tmp_path, monkeypatch):
    backend = LocalJsonBackend(base_dir=str(tmp_path))
    # 真 run_turn 用 LLM 格式存 messages（key='content'）——标题派生必须认这个。
    backend.create_session(
        "default", session_id="os-a", goal_id="goal_001", status="done",
        messages=[{"role": "user", "content": "帮我策划自助机点位招商选题"},
                  {"role": "assistant", "content": "好的"}])
    backend.create_session(
        "default", session_id="os-b", goal_id="goal_002", status="thinking",
        messages=[{"role": "user", "content": "别的目标"}])
    monkeypatch.setattr(storage.factory, "get_backend", lambda: backend)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/orchestrator/sessions?goal_id=goal_001", headers=_auth())

    assert resp.status_code == 200
    sessions = resp.json()["sessions"]
    assert len(sessions) == 1
    item = sessions[0]
    assert item["session_id"] == "os-a"
    assert item["title"] == "帮我策划自助机点位招商选题"
    assert item["status"] == "done"
    assert item["message_count"] == 2


async def test_list_sessions_title_falls_back_to_trace(tmp_path, monkeypatch):
    """messages 为空但 trace 有 user_message 时，标题回退到 trace。"""
    backend = LocalJsonBackend(base_dir=str(tmp_path))
    sess = backend.create_session("default", session_id="os-t", goal_id="goal_001",
                                  status="thinking", messages=[])
    backend.update_session("default", "os-t", expected_rev=sess["rev"],
                           trace=[{"type": "user_message", "content": "只在trace里的问题"}])
    monkeypatch.setattr(storage.factory, "get_backend", lambda: backend)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/orchestrator/sessions?goal_id=goal_001", headers=_auth())

    assert resp.status_code == 200
    assert resp.json()["sessions"][0]["title"] == "只在trace里的问题"


async def test_delete_session_endpoint(tmp_path, monkeypatch):
    backend = LocalJsonBackend(base_dir=str(tmp_path))
    backend.create_session("default", session_id="os-del", goal_id="goal_001",
                           messages=[{"role": "user", "text": "删我"}])
    monkeypatch.setattr(storage.factory, "get_backend", lambda: backend)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        ok = await client.delete("/api/v1/orchestrator/session/os-del", headers=_auth_mutating())
        missing = await client.delete("/api/v1/orchestrator/session/os-nope", headers=_auth_mutating())

    assert ok.status_code == 200 and ok.json()["deleted"] is True
    assert missing.status_code == 404
    assert backend.get_session("default", "os-del") is None
