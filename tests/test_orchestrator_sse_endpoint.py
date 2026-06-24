"""P2.2 tests · orchestrator coordination SSE endpoint."""
from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid

import pytest

from httpx import ASGITransport, AsyncClient

os.environ.setdefault("JWT_SECRET", "test-secret-orchestrator-sse")
os.environ.setdefault("JWT_ALGORITHM", "HS256")

from security.jwt import encode_token
from server.main import app


def _headers() -> dict[str, str]:
    return {"Idempotency-Key": uuid.uuid4().hex}


def _token(tenant_id: str = "default") -> str:
    return encode_token(tenant_id)


async def _sse_events(resp) -> list[dict]:
    events: list[dict] = []
    async for raw in resp.aiter_lines():
        if not raw.startswith("data:"):
            continue
        event = json.loads(raw[len("data:"):].strip())
        events.append(event)
        if event.get("type") == "done":
            break
    return events


async def test_orchestrator_stream_forwards_run_turn_events(monkeypatch):
    """POST /converse/stream streams coordination events in contract order."""
    captured: dict[str, object] = {}

    def fake_worker(*, body, tenant_id, queue, loop, stop_event):
        captured["tenant_id"] = tenant_id
        captured["body_tenant"] = getattr(body, "tenant_id", None)
        for event in [
            {"type": "thinking", "seq": 1, "summary": "分析意图"},
            {"type": "subagent_start", "seq": 2, "archetype": "intel", "task": "采集"},
            {"type": "subagent_result", "seq": 3, "archetype": "intel", "ok": True, "summary": "有机会"},
            {"type": "final", "seq": 4, "summary": "建议先切深圳工厂物业"},
            {"type": "done", "seq": 5, "status": "done"},
        ]:
            loop.call_soon_threadsafe(queue.put_nowait, event)

    monkeypatch.setattr("server.routers.orchestrator.sync_orchestrator_worker", fake_worker)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        async with ac.stream(
            "POST",
            f"/api/v1/orchestrator/converse/stream?token={_token('tenant-from-token')}",
            headers=_headers(),
            json={"message": "规划深圳工厂物业内容", "goal_id": "goal_001"},
        ) as resp:
            assert resp.status_code == 200, await resp.aread()
            assert "text/event-stream" in resp.headers["content-type"]
            events = await _sse_events(resp)

    assert [event["type"] for event in events] == [
        "thinking",
        "subagent_start",
        "subagent_result",
        "final",
        "done",
    ]
    assert captured["tenant_id"] == "tenant-from-token"
    assert captured["body_tenant"] is None


async def test_orchestrator_stream_sets_stop_event_on_disconnect(monkeypatch):
    """The SSE generator finalizer sets stop_event when closed before done."""
    from server.routers.orchestrator import orchestrator_event_generator

    queue: asyncio.Queue = asyncio.Queue()
    stop_event = threading.Event()
    await queue.put({"type": "thinking", "seq": 1, "summary": "先想"})

    gen = orchestrator_event_generator(queue, stop_event)
    first = await gen.__anext__()
    assert json.loads(first["data"])["type"] == "thinking"

    await gen.aclose()
    assert stop_event.is_set()


async def test_converse_non_streaming_fallback_uses_run_turn(monkeypatch):
    """POST /converse remains as a non-streaming fallback over run_turn."""
    received_events: list[dict] = []
    captured: dict[str, object] = {}

    def fake_run_turn(*, backend, tenant_id, message, session_id, goal_id, emit, **_kwargs):
        captured.update(
            backend=backend,
            tenant_id=tenant_id,
            message=message,
            session_id=session_id,
            goal_id=goal_id,
        )
        event = {"type": "final", "seq": 1, "summary": "非流式完成"}
        emit(event)
        received_events.append(event)
        return {
            "session_id": "os-fallback",
            "status": "done",
            "goal_id": goal_id,
            "messages": [{"role": "user", "content": message}],
            "trace": [event, {"type": "done", "seq": 2, "status": "done"}],
            "pending": None,
            "decision_cards": [],
            "dag_id": None,
        }

    monkeypatch.setattr("server.routers.orchestrator.run_turn", fake_run_turn)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/orchestrator/converse",
            headers={"Authorization": f"Bearer {_token('tenant-fallback')}", **_headers()},
            json={"message": "规划内容", "goal_id": "goal_001", "session_id": "os-existing"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "done"
    assert body["trace"][0]["type"] == "final"
    assert captured["tenant_id"] == "tenant-fallback"
    assert captured["session_id"] == "os-existing"
    assert received_events == [body["trace"][0]]


async def test_orchestrator_worker_error_events_include_seq(monkeypatch):
    """Worker-level fallback errors still obey the SSE event shape contract."""
    from server.routers.orchestrator import ConverseRequest, sync_orchestrator_worker

    def boom_run_turn(**_kwargs):
        raise RuntimeError("协调失败")

    monkeypatch.setattr("server.routers.orchestrator.run_turn", boom_run_turn)

    queue: asyncio.Queue = asyncio.Queue()
    stop_event = threading.Event()
    sync_orchestrator_worker(
        body=ConverseRequest(message="开始"),
        tenant_id="tenant-error",
        queue=queue,
        loop=asyncio.get_running_loop(),
        stop_event=stop_event,
    )
    await asyncio.sleep(0)

    error = await queue.get()
    done = await queue.get()
    assert error["type"] == "error"
    assert error["seq"] == 1
    assert done == {"type": "done", "status": "done", "seq": 2}


async def test_get_session_returns_trace_and_pending_after_awaiting_user(tmp_path, monkeypatch):
    """GET /session/{id} restores trace + pending after an awaiting_user pause."""
    from storage.local_json import LocalJsonBackend
    import storage.factory

    backend = LocalJsonBackend(base_dir=str(tmp_path))
    monkeypatch.setattr(storage.factory, "get_backend", lambda *a, **k: backend)

    question = {"kind": "question", "question": "主推园区还是写字楼？"}
    backend.create_session(
        "tenant-recover",
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

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/orchestrator/session/os-awaiting",
            headers={"Authorization": f"Bearer {_token('tenant-recover')}"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "awaiting_user"
    assert [event["type"] for event in body["trace"]] == ["thinking", "awaiting_user"]
    assert body["pending"] == question


async def test_real_pause_path_emits_done_terminator(tmp_path, monkeypatch):
    """Real run_turn pause path must emit done terminator (§B/§C — hotfix)."""
    from types import SimpleNamespace

    from storage.local_json import LocalJsonBackend
    import storage.factory
    from server.routers.orchestrator import (
        ConverseRequest,
        sync_orchestrator_worker,
    )

    # ── fake LLM: returns ask_user on first call ──
    question = "主推园区还是写字楼？"

    def fake_llm(**kwargs):
        tc = SimpleNamespace(
            id="call_ask",
            function=SimpleNamespace(
                name="ask_user",
                arguments=json.dumps({"question": question}),
            ),
        )
        msg = SimpleNamespace(
            content="信息不足，需要确认方向。",
            tool_calls=[tc],
        )
        return msg, None, 100

    monkeypatch.setattr("agents.orchestrator_agent.call_kimi_with_tools_stream", fake_llm)

    # ── real backend ──
    backend = LocalJsonBackend(base_dir=str(tmp_path))
    monkeypatch.setattr(storage.factory, "get_backend", lambda *a, **k: backend)

    # ── run sync_orchestrator_worker with real run_turn ──
    queue: asyncio.Queue = asyncio.Queue()
    stop_event = threading.Event()
    loop = asyncio.get_running_loop()

    await loop.run_in_executor(
        None,
        lambda: sync_orchestrator_worker(
            body=ConverseRequest(message="规划一下内容方向", goal_id="goal_001"),
            tenant_id="tenant-pause-test",
            queue=queue,
            loop=loop,
            stop_event=stop_event,
        ),
    )

    # ── collect events (timeout-protected) ──
    events = []
    while True:
        try:
            ev = await asyncio.wait_for(queue.get(), timeout=5.0)
            events.append(ev)
            if ev.get("type") == "done":
                break
        except asyncio.TimeoutError:
            pytest.fail("SSE generator hung — done event never emitted on pause path")

    # ── assert event sequence ends with done ──
    types = [ev["type"] for ev in events]
    assert "awaiting_user" in types, f"expected awaiting_user in events: {types}"
    assert types[-1] == "done", f"expected done as final event, got {types}"
    assert events[-1]["status"] == "awaiting_user"

    # ── assert stored session ──
    sessions = backend.list_sessions("tenant-pause-test", limit=1)
    assert len(sessions) == 1
    sess = sessions[0]
    assert sess["status"] == "awaiting_user"
    assert sess["pending"] == {"kind": "question", "question": question}
    trace_types = [ev["type"] for ev in (sess.get("trace") or [])]
    assert "awaiting_user" in trace_types
    assert "done" in trace_types, f"trace missing done terminator: {trace_types}"
