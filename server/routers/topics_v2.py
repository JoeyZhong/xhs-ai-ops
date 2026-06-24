from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from server.auth import AuthContext, verify_token
from server.errors import ErrorCode, error_response
from server.middleware.idempotency import IdempotencyRoute
from server.middleware.tenant_guard import assert_no_tenant_in_body, fetch_current_rev
from storage.base import RevMismatch
import storage.factory

# ── Pydantic models ───────────────────────────────────────────────────────


class TopicCreate(BaseModel):
    title: str
    goal_id: str | None = None
    persona_id: str | None = None
    angle: str | None = None
    funnel_stage: Literal["traffic", "trust", "conversion"] | None = None
    source: Literal["ai", "manual", "market_insight", "memory"] = "manual"
    source_refs: list[dict] = []


class TopicUpdate(BaseModel):
    title: str | None = None
    goal_id: str | None = None
    persona_id: str | None = None
    angle: str | None = None
    funnel_stage: Literal["traffic", "trust", "conversion"] | None = None
    source: Literal["ai", "manual", "market_insight", "memory"] | None = None
    source_refs: list[dict] | None = None
    status: Literal[
        "idea", "planned", "drafting", "drafted",
        "scheduled", "published", "archived"
    ] | None = None
    rev: int  # required for OCC


router = APIRouter(prefix="/api/v1/topics", route_class=IdempotencyRoute)


# ── GET 列表 ─────────────────────────────────────────────────────────────


@router.get("")
async def list_topics(
    goal_id: str | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort: str = Query("-updated_at"),
    auth: AuthContext = Depends(verify_token),
) -> dict:
    backend = storage.factory.get_backend()
    return await run_in_threadpool(
        backend.list_topics,
        auth.tenant_id,
        goal_id=goal_id,
        status=status,
        page=page,
        page_size=page_size,
        sort=sort,
    )


@router.get("/{topic_id}", response_model=None)
async def get_topic(
    topic_id: str,
    auth: AuthContext = Depends(verify_token),
):
    backend = storage.factory.get_backend()
    try:
        return await run_in_threadpool(backend.get_topic, auth.tenant_id, topic_id)
    except KeyError:
        return error_response(
            status_code=404,
            code=ErrorCode.NOT_FOUND,
            message=f"Topic '{topic_id}' not found",
        )


# ── POST 创建 ────────────────────────────────────────────────────────────


@router.post("", status_code=201, response_model=None)
async def create_topic(
    body: TopicCreate,
    request: Request,
    auth: AuthContext = Depends(verify_token),
):
    # 拒绝 body 中的 tenant_id
    tenant_err = await assert_no_tenant_in_body(request)
    if tenant_err:
        return tenant_err

    backend = storage.factory.get_backend()
    return await run_in_threadpool(
        backend.create_topic,
        auth.tenant_id,
        title=body.title,
        goal_id=body.goal_id,
        persona_id=body.persona_id,
        angle=body.angle,
        funnel_stage=body.funnel_stage,
        source=body.source,
        source_refs=body.source_refs,
    )


# ── PUT 更新（OCC）───────────────────────────────────────────────────────


@router.put("/{topic_id}", response_model=None)
async def update_topic(
    topic_id: str,
    body: TopicUpdate,
    request: Request,
    auth: AuthContext = Depends(verify_token),
):
    tenant_err = await assert_no_tenant_in_body(request)
    if tenant_err:
        return tenant_err

    backend = storage.factory.get_backend()
    changes = body.model_dump(exclude={"rev"}, exclude_unset=True)
    try:
        return await run_in_threadpool(
            backend.update_topic,
            auth.tenant_id,
            topic_id,
            expected_rev=body.rev,
            **changes,
        )
    except RevMismatch:
        current_rev = fetch_current_rev(backend.get_topic, auth.tenant_id, topic_id)
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
            message=f"Topic '{topic_id}' not found",
        )


# ── DELETE 归档 ──────────────────────────────────────────────────────────


@router.delete("/{topic_id}", response_model=None)
async def delete_topic(
    topic_id: str,
    rev: int = Query(..., description="Expected rev for OCC"),
    auth: AuthContext = Depends(verify_token),
):
    backend = storage.factory.get_backend()
    try:
        result = await run_in_threadpool(
            backend.delete_topic,
            auth.tenant_id,
            topic_id,
            expected_rev=rev,
        )
        return {"topic_id": topic_id, "status": result.get("status", "archived")}
    except RevMismatch:
        current_rev = fetch_current_rev(backend.get_topic, auth.tenant_id, topic_id)
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
            message=f"Topic '{topic_id}' not found",
        )


