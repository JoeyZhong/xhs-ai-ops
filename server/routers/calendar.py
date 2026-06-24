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


class CalendarItemCreate(BaseModel):
    topic_id: str | None = None
    content_id: str | None = None
    scheduled_date: str  # YYYY-MM-DD
    scheduled_time: str | None = None
    funnel_stage: Literal["traffic", "trust", "conversion"] | None = None


class CalendarItemUpdate(BaseModel):
    topic_id: str | None = None
    content_id: str | None = None
    scheduled_date: str | None = None
    scheduled_time: str | None = None
    funnel_stage: Literal["traffic", "trust", "conversion"] | None = None
    status: Literal[
        "planned", "drafted", "scheduled", "published", "cancelled"
    ] | None = None
    rev: int  # required for OCC


router = APIRouter(prefix="/api/v1/calendar", route_class=IdempotencyRoute)


# ── GET 列表 ─────────────────────────────────────────────────────────────


@router.get("", response_model=None)
async def list_calendar_items(
    date_from: str | None = Query(None, alias="from"),
    date_to: str | None = Query(None, alias="to"),
    status: str | None = Query(None),
    include_deleted: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort: str = Query("scheduled_date"),
    auth: AuthContext = Depends(verify_token),
) -> JSONResponse | dict:
    backend = storage.factory.get_backend()
    return await run_in_threadpool(
        backend.list_calendar_items,
        auth.tenant_id,
        date_from=date_from,
        date_to=date_to,
        status=status,
        include_deleted=include_deleted,
        page=page,
        page_size=page_size,
        sort=sort,
    )


@router.get("/{calendar_item_id}", response_model=None)
async def get_calendar_item(
    calendar_item_id: str,
    auth: AuthContext = Depends(verify_token),
) -> JSONResponse | dict:
    backend = storage.factory.get_backend()
    try:
        return await run_in_threadpool(
            backend.get_calendar_item, auth.tenant_id, calendar_item_id
        )
    except KeyError:
        return error_response(
            status_code=404,
            code=ErrorCode.NOT_FOUND,
            message=f"Calendar item '{calendar_item_id}' not found",
        )


# ── POST 创建 ────────────────────────────────────────────────────────────


@router.post("", status_code=201, response_model=None)
async def create_calendar_item(
    body: CalendarItemCreate,
    request: Request,
    auth: AuthContext = Depends(verify_token),
) -> JSONResponse | dict:
    tenant_err = await assert_no_tenant_in_body(request)
    if tenant_err:
        return tenant_err

    backend = storage.factory.get_backend()
    return await run_in_threadpool(
        backend.create_calendar_item,
        auth.tenant_id,
        scheduled_date=body.scheduled_date,
        scheduled_time=body.scheduled_time,
        topic_id=body.topic_id,
        funnel_stage=body.funnel_stage,
        content_id=body.content_id,
    )


# ── PUT 更新（OCC）───────────────────────────────────────────────────────


@router.put("/{calendar_item_id}", response_model=None)
async def update_calendar_item(
    calendar_item_id: str,
    body: CalendarItemUpdate,
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
            backend.update_calendar_item,
            auth.tenant_id,
            calendar_item_id,
            expected_rev=body.rev,
            **changes,
        )
    except RevMismatch:
        current_rev = fetch_current_rev(backend.get_calendar_item, auth.tenant_id, calendar_item_id)
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
            message=f"Calendar item '{calendar_item_id}' not found",
        )


# ── DELETE（软删除 / 硬删除）─────────────────────────────────────────────


@router.delete("/{calendar_item_id}", response_model=None)
async def delete_calendar_item(
    calendar_item_id: str,
    rev: int = Query(..., description="Expected rev for OCC"),
    mode: Literal["soft", "hard"] = Query("soft"),
    auth: AuthContext = Depends(verify_token),
) -> JSONResponse | dict:
    backend = storage.factory.get_backend()
    try:
        result = await run_in_threadpool(
            backend.delete_calendar_item,
            auth.tenant_id,
            calendar_item_id,
            expected_rev=rev,
            mode=mode,
        )
        return result
    except RevMismatch:
        current_rev = fetch_current_rev(backend.get_calendar_item, auth.tenant_id, calendar_item_id)
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
            message=f"Calendar item '{calendar_item_id}' not found",
        )


# ── Helpers ──────────────────────────────────────────────────────────────
