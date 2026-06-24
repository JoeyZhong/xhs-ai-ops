"""Orchestrator 主助手 API（V1.3 MVP）。

薄 HTTP 层：鉴权 / 幂等 / 参数。业务逻辑在 agents/orchestrator.py。
执行复用既有 HermesMaster.submit_dag + GET /api/v1/dag/{id}（不新增进度端点）。
"""
from __future__ import annotations

import asyncio
import json
import threading
import uuid

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from agents import orchestrator as orch
from agents.orchestrator_agent import run_turn
from server.auth import AuthContext, verify_token
from server.errors import ErrorCode, error_response
from server.middleware.idempotency import IdempotencyRoute
import storage.factory


router = APIRouter(prefix="/api/v1/orchestrator", tags=["orchestrator"],
                   route_class=IdempotencyRoute)


class ConverseRequest(BaseModel):
    message: str
    goal_id: str | None = None
    session_id: str | None = None


class ConfirmRequest(BaseModel):
    session_id: str
    plan_card_decision: str  # approve | reject


class DecisionRequest(BaseModel):
    session_id: str
    card_id: str
    decision: str  # approve | reject


_SESSION_VIEW = ("session_id", "status", "goal_id", "messages",
                 "trace", "pending", "decision_cards", "dag_id")


def _view(sess: dict) -> dict:
    return {k: sess.get(k) for k in _SESSION_VIEW}


def sync_orchestrator_worker(*, body: ConverseRequest, tenant_id: str,
                             queue: asyncio.Queue,
                             loop: asyncio.AbstractEventLoop,
                             stop_event: threading.Event) -> None:
    """Run the blocking coordination loop in a worker thread and emit SSE events."""
    seq = 0

    def emit(event: dict) -> None:
        nonlocal seq
        if stop_event.is_set():
            return
        out = dict(event)
        try:
            seq = max(seq, int(out["seq"]))
        except (KeyError, TypeError, ValueError):
            seq += 1
            out["seq"] = seq
        loop.call_soon_threadsafe(queue.put_nowait, out)

    try:
        backend = storage.factory.get_backend()
        run_turn(
            backend=backend,
            tenant_id=tenant_id,
            message=body.message,
            session_id=body.session_id,
            goal_id=body.goal_id,
            emit=emit,
        )
    except Exception as exc:
        if not stop_event.is_set():
            emit({"type": "error", "message": str(exc)})
            emit({"type": "done", "status": "done"})


async def orchestrator_event_generator(queue: asyncio.Queue,
                                       stop_event: threading.Event):
    """Yield queued orchestrator events as SSE payloads and signal disconnect."""
    try:
        while True:
            msg = await queue.get()
            yield {"data": json.dumps(msg, ensure_ascii=False)}
            if msg.get("type") == "done":
                break
    finally:
        stop_event.set()


@router.post("/converse")
async def converse(body: ConverseRequest,
                   auth: AuthContext = Depends(verify_token)) -> dict:
    def _run() -> dict:
        backend = storage.factory.get_backend()
        events: list[dict] = []
        return run_turn(
            backend=backend,
            tenant_id=auth.tenant_id,
            message=body.message,
            goal_id=body.goal_id,
            session_id=body.session_id,
            emit=events.append,
        )

    return await run_in_threadpool(_run)


@router.post("/converse/stream")
async def converse_stream(body: ConverseRequest,
                          auth: AuthContext = Depends(verify_token)) -> EventSourceResponse:
    """Stream one orchestrator coordination turn as SSE events."""
    queue: asyncio.Queue = asyncio.Queue()
    stop_event = threading.Event()
    loop = asyncio.get_running_loop()

    loop.run_in_executor(
        None,
        lambda: sync_orchestrator_worker(
            body=body,
            tenant_id=auth.tenant_id,
            queue=queue,
            loop=loop,
            stop_event=stop_event,
        ),
    )

    return EventSourceResponse(orchestrator_event_generator(queue, stop_event))


@router.post("/plan/confirm")
async def plan_confirm(body: ConfirmRequest,
                       auth: AuthContext = Depends(verify_token)):
    backend = storage.factory.get_backend()
    tid = auth.tenant_id
    sess = orch.get_session(backend, tid, body.session_id)
    if sess is None:
        return error_response(status_code=404, code=ErrorCode.NOT_FOUND,
                              message=f"session '{body.session_id}' not found")

    if body.plan_card_decision == "reject":
        await run_in_threadpool(orch.mark_cancelled, backend, tid, sess)
        return {"session_id": sess["session_id"], "status": "cancelled"}

    if body.plan_card_decision != "approve":
        return error_response(status_code=422, code=ErrorCode.VALIDATION_ERROR,
                              message="plan_card_decision must be 'approve' or 'reject'",
                              field="plan_card_decision")

    if sess.get("status") != "planned" or not sess.get("proposed_plan"):
        return error_response(status_code=422, code=ErrorCode.INVALID_STATUS_TRANSITION,
                              message="no planned plan to confirm in this session")

    nodes = orch.plan_nodes(sess)
    dag_id = f"dag-{uuid.uuid4().hex[:8]}"

    async def _run_dag():
        from agents.master import HermesMaster
        master = HermesMaster(tenant_id=tid)
        await run_in_threadpool(master.submit_dag, nodes, dag_id, tenant_id=tid)

    await run_in_threadpool(orch.mark_dispatched, backend, tid, sess, dag_id)
    asyncio.create_task(_run_dag())
    return {"session_id": sess["session_id"], "status": "dispatched", "dag_id": dag_id}


@router.post("/decision")
async def decision(body: DecisionRequest,
                   auth: AuthContext = Depends(verify_token)):
    backend = storage.factory.get_backend()
    tid = auth.tenant_id
    sess = orch.get_session(backend, tid, body.session_id)
    if sess is None:
        return error_response(status_code=404, code=ErrorCode.NOT_FOUND,
                              message=f"session '{body.session_id}' not found")
    if body.decision not in ("approve", "reject"):
        return error_response(status_code=422, code=ErrorCode.VALIDATION_ERROR,
                              message="decision must be 'approve' or 'reject'",
                              field="decision")
    updated = await run_in_threadpool(
        orch.set_card_decision, backend, tid, sess, body.card_id, body.decision)
    if updated is None:
        return error_response(status_code=404, code=ErrorCode.NOT_FOUND,
                              message=f"card '{body.card_id}' not found")
    return {"session_id": updated["session_id"], "decision_cards": updated["decision_cards"]}


@router.get("/session/{session_id}")
async def get_session(session_id: str,
                      auth: AuthContext = Depends(verify_token)):
    def _load():
        backend = storage.factory.get_backend()
        return orch.get_session(backend, auth.tenant_id, session_id)

    sess = await run_in_threadpool(_load)
    if sess is None:
        return error_response(status_code=404, code=ErrorCode.NOT_FOUND,
                              message=f"session '{session_id}' not found")
    return _view(sess)


def _derive_title(sess: dict) -> str:
    """会话标题 = 首条用户提问（strip + 截 40 字）；无则空串。

    兼容两处来源：
      - messages 里第一条 role==user（真 run_turn 用 LLM 格式 key='content'，
        旧 service/测试用 key='text'，两者都认）；
      - 回退 trace 里第一条 user_message 事件（key='content'）。
    """
    for m in sess.get("messages") or []:
        if isinstance(m, dict) and m.get("role") == "user":
            text = (m.get("content") or m.get("text") or "").strip()
            if text:
                return text[:40]
    for ev in sess.get("trace") or []:
        if isinstance(ev, dict) and ev.get("type") == "user_message":
            text = (ev.get("content") or "").strip()
            if text:
                return text[:40]
    return ""


def _session_list_item(sess: dict) -> dict:
    msgs = sess.get("messages") or []
    return {
        "session_id": sess.get("session_id"),
        "goal_id": sess.get("goal_id"),
        "title": _derive_title(sess),
        "status": sess.get("status"),
        "updated_at": sess.get("updated_at"),
        "message_count": len(msgs),
    }


@router.get("/sessions")
async def list_sessions(goal_id: str | None = None, limit: int = 20,
                        auth: AuthContext = Depends(verify_token)) -> dict:
    def _load():
        backend = storage.factory.get_backend()
        return backend.list_sessions(auth.tenant_id, goal_id=goal_id, limit=limit)

    sessions = await run_in_threadpool(_load)
    return {"sessions": [_session_list_item(s) for s in sessions]}


@router.delete("/session/{session_id}")
async def delete_session(session_id: str,
                         auth: AuthContext = Depends(verify_token)):
    def _delete():
        backend = storage.factory.get_backend()
        return backend.delete_session(auth.tenant_id, session_id)

    deleted = await run_in_threadpool(_delete)
    if not deleted:
        return error_response(status_code=404, code=ErrorCode.NOT_FOUND,
                              message=f"session '{session_id}' not found")
    return {"deleted": True}
