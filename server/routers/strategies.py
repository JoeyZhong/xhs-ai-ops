from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from server.auth import AuthContext, verify_token
from server.errors import ErrorCode, error_response
from server.middleware.idempotency import IdempotencyRoute
from server.middleware.tenant_guard import assert_no_tenant_in_body, fetch_current_rev
from storage.base import RevMismatch
import storage.factory

# ── Pydantic models ───────────────────────────────────────────────────────


class StrategyCreate(BaseModel):
    topic_id: str | None = None
    manual_input_hint: str | None = None
    target_reader: str | None = None
    funnel_stage: Literal["traffic", "trust", "conversion"] | None = None
    angle: str | None = None
    hook: str | None = None
    key_points: list[dict] = []
    cta: str | None = None
    avoid_points: list[dict] = []
    evidence_refs: list[dict] = []
    memory_refs: list[dict] = []
    knowledge_refs: list[dict] = []


class StrategyUpdate(BaseModel):
    topic_id: str | None = None
    manual_input_hint: str | None = None
    target_reader: str | None = None
    funnel_stage: Literal["traffic", "trust", "conversion"] | None = None
    angle: str | None = None
    hook: str | None = None
    key_points: list[dict] | None = None
    cta: str | None = None
    avoid_points: list[dict] | None = None
    evidence_refs: list[dict] | None = None
    memory_refs: list[dict] | None = None
    knowledge_refs: list[dict] | None = None
    rev: int  # required for OCC


router = APIRouter(prefix="/api/v1/strategies", route_class=IdempotencyRoute)


# ── GET 列表 ─────────────────────────────────────────────────────────────


@router.get("")
async def list_strategies(
    topic_id: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort: str = Query("-created_at"),
    auth: AuthContext = Depends(verify_token),
) -> dict:
    backend = storage.factory.get_backend()
    return await run_in_threadpool(
        backend.list_strategies,
        auth.tenant_id,
        topic_id=topic_id,
        page=page,
        page_size=page_size,
        sort=sort,
    )


@router.get("/{strategy_id}", response_model=None)
async def get_strategy(
    strategy_id: str,
    auth: AuthContext = Depends(verify_token),
):
    backend = storage.factory.get_backend()
    try:
        return await run_in_threadpool(
            backend.get_strategy, auth.tenant_id, strategy_id
        )
    except KeyError:
        return error_response(
            status_code=404,
            code=ErrorCode.NOT_FOUND,
            message=f"Strategy '{strategy_id}' not found",
        )


# ── POST 创建 ────────────────────────────────────────────────────────────


@router.post("", status_code=201, response_model=None)
async def create_strategy(
    body: StrategyCreate,
    request: Request,
    auth: AuthContext = Depends(verify_token),
) -> JSONResponse | dict:
    tenant_err = await assert_no_tenant_in_body(request)
    if tenant_err:
        return tenant_err

    # 策略必须关联 topic_id 或提供 manual_input_hint
    if body.topic_id is None and body.manual_input_hint is None:
        return error_response(
            status_code=422,
            code=ErrorCode.STRATEGY_MISSING_ANCHOR,
            message="Either topic_id or manual_input_hint must be provided",
            field="topic_id",
        )

    backend = storage.factory.get_backend()
    return await run_in_threadpool(
        backend.create_strategy,
        auth.tenant_id,
        topic_id=body.topic_id,
        manual_input_hint=body.manual_input_hint,
        target_reader=body.target_reader,
        funnel_stage=body.funnel_stage,
        angle=body.angle,
        hook=body.hook,
        key_points=body.key_points,
        cta=body.cta,
        avoid_points=body.avoid_points,
        evidence_refs=body.evidence_refs,
        memory_refs=body.memory_refs,
        knowledge_refs=body.knowledge_refs,
    )


# ── PUT 更新（OCC）───────────────────────────────────────────────────────


@router.put("/{strategy_id}", response_model=None)
async def update_strategy(
    strategy_id: str,
    body: StrategyUpdate,
    request: Request,
    auth: AuthContext = Depends(verify_token),
) -> JSONResponse | dict:
    tenant_err = await assert_no_tenant_in_body(request)
    if tenant_err:
        return tenant_err

    backend = storage.factory.get_backend()
    changes = body.model_dump(exclude={"rev"}, exclude_unset=True)
    try:
        return await run_in_threadpool(
            backend.update_strategy,
            auth.tenant_id,
            strategy_id,
            expected_rev=body.rev,
            **changes,
        )
    except RevMismatch:
        current_rev = fetch_current_rev(backend.get_strategy, auth.tenant_id, strategy_id)
        return error_response(
            status_code=409,
            code=ErrorCode.REV_MISMATCH,
            message="Revision conflict",
            current_rev=current_rev,
        )
    except KeyError:
        return error_response(
            status_code=404,
            code=ErrorCode.NOT_FOUND,
            message=f"Strategy '{strategy_id}' not found",
        )


# ── DELETE ───────────────────────────────────────────────────────────────


@router.delete("/{strategy_id}", response_model=None)
async def delete_strategy(
    strategy_id: str,
    auth: AuthContext = Depends(verify_token),
) -> JSONResponse | dict:
    backend = storage.factory.get_backend()
    try:
        await run_in_threadpool(
            backend.delete_strategy, auth.tenant_id, strategy_id
        )
        return {"deleted": True, "strategy_id": strategy_id}
    except KeyError:
        return error_response(
            status_code=404,
            code=ErrorCode.NOT_FOUND,
            message=f"Strategy '{strategy_id}' not found",
        )


