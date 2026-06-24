"""
Drafts router — content draft lifecycle management (design.md §3.3 rows 14-18).

Endpoints:
  GET    /api/v1/drafts
  GET    /api/v1/drafts/{content_id}
  PUT    /api/v1/drafts/{content_id}
  POST   /api/v1/drafts/{content_id}/duplicate
  POST   /api/v1/drafts/{content_id}/schedule
  POST   /api/v1/drafts/{content_id}/reject
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

import pandas as pd
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


class DraftUpdate(BaseModel):
    title: str | None = None
    body: str | None = None
    hashtags: list[str] | None = None
    publish_at: str | None = None
    status: Literal[
        "draft", "edited", "scheduled", "published", "rejected"
    ] | None = None
    topic_id: str | None = None
    strategy_id: str | None = None
    calendar_item_id: str | None = None
    knowledge_refs: list[dict] | None = None
    memory_refs: list[dict] | None = None
    rev: int  # required for OCC


class DuplicateRequest(BaseModel):
    title_suffix: str | None = None


class ScheduleRequest(BaseModel):
    scheduled_date: str  # YYYY-MM-DD
    scheduled_time: str | None = None
    funnel_stage: Literal["traffic", "trust", "conversion"] | None = None


class RejectRequest(BaseModel):
    reason: str | None = None


router = APIRouter(prefix="/api/v1/drafts", route_class=IdempotencyRoute)

EPOCH = datetime(2000, 1, 1)


# ── Helpers ──────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_post(row: dict) -> dict:
    """Normalise a generated_content row for API response."""
    for lst_field in ("hashtags", "knowledge_refs", "memory_refs"):
        raw = row.get(lst_field)
        if isinstance(raw, list):
            row[lst_field] = raw
        elif raw is None:
            row[lst_field] = [] if lst_field == "hashtags" else []
        # keep as-is for PG JSONB arrays already deserialized
    return row


# ── GET list ─────────────────────────────────────────────────────────────


@router.get("")
async def list_drafts(
    goal_id: str | None = Query(None),
    persona_id: str | None = Query(None),
    status: str | None = Query(None),
    topic_id: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort: str = Query("-updated_at"),
    auth: AuthContext = Depends(verify_token),
) -> dict:
    backend = storage.factory.get_backend()

    def _load() -> dict:
        df = backend.list_generated_posts(
            auth.tenant_id,
            since=EPOCH,
            topic_id=topic_id,
            status=status,
        )
        if df.empty:
            return {"items": [], "total": 0, "page": page, "page_size": page_size, "has_more": False}

        items: list[dict[str, Any]] = df.to_dict("records")

        # client-side filters not supported by backend
        if goal_id:
            items = [i for i in items if str(i.get("goal_id", "")) == goal_id]
        if persona_id:
            items = [i for i in items if str(i.get("persona_id", "")) == persona_id]
        if date_from:
            items = [i for i in items if str(i.get("created_at", "")) >= date_from]
        if date_to:
            items = [i for i in items if str(i.get("created_at", "")) <= date_to]

        items = [_parse_post(i) for i in items]

        total = len(items)
        start = (page - 1) * page_size
        end = start + page_size
        return {
            "items": items[start:end],
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_more": end < total,
        }

    return await run_in_threadpool(_load)


# ── GET detail ───────────────────────────────────────────────────────────


@router.get("/{content_id}")
async def get_draft(
    content_id: str,
    auth: AuthContext = Depends(verify_token),
) -> dict:
    backend = storage.factory.get_backend()
    row = await run_in_threadpool(backend.get_generated_post, auth.tenant_id, content_id)
    if row is None:
        return error_response(
            status_code=404,
            code=ErrorCode.NOT_FOUND,
            message=f"Draft '{content_id}' not found",
        )
    return _parse_post(row)


# ── PUT update (OCC) ────────────────────────────────────────────────────


@router.put("/{content_id}")
async def update_draft(
    content_id: str,
    body: DraftUpdate,
    request: Request,
    auth: AuthContext = Depends(verify_token),
) -> dict:
    tenant_err = await assert_no_tenant_in_body(request)
    if tenant_err:
        return tenant_err

    backend = storage.factory.get_backend()
    changes = body.model_dump(exclude={"rev"}, exclude_unset=True)
    try:
        result = await run_in_threadpool(
            backend.update_generated_post,
            auth.tenant_id,
            content_id,
            expected_rev=body.rev,
            **changes,
        )
        return _parse_post(result)
    except RevMismatch:
        current_rev = fetch_current_rev(
            backend.get_generated_post, auth.tenant_id, content_id
        )
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
            message=f"Draft '{content_id}' not found",
        )


# ── POST duplicate ──────────────────────────────────────────────────────


@router.post("/{content_id}/duplicate", status_code=201)
async def duplicate_draft(
    content_id: str,
    body: DuplicateRequest,
    request: Request,
    auth: AuthContext = Depends(verify_token),
) -> dict:
    tenant_err = await assert_no_tenant_in_body(request)
    if tenant_err:
        return tenant_err

    backend = storage.factory.get_backend()

    def _run() -> dict:
        original = backend.get_generated_post(auth.tenant_id, content_id)
        if original is None:
            raise KeyError(content_id)

        new_id = str(uuid.uuid4())[:8]
        title_suffix = body.title_suffix or "（副本）"
        new_meta = dict(original.get("meta") or {})
        new_meta["duplicated_from"] = content_id

        row = {
            "content_id": new_id,
            "goal_id": original.get("goal_id"),
            "title": (original.get("title") or "") + title_suffix,
            "body": original.get("body", ""),
            "hashtags": (original.get("hashtags") or []) if isinstance(original.get("hashtags"), list) else [],
            "publish_at": original.get("publish_at", ""),
            "status": "draft",
            "topic_id": original.get("topic_id"),
            "strategy_id": original.get("strategy_id"),
            "calendar_item_id": None,
            "knowledge_refs": (original.get("knowledge_refs") or []) if isinstance(original.get("knowledge_refs"), list) else [],
            "memory_refs": (original.get("memory_refs") or []) if isinstance(original.get("memory_refs"), list) else [],
            "meta": new_meta,
        }
        df = pd.DataFrame([row])
        backend.save_generated_posts(auth.tenant_id, df, meta={"_source": "duplicate"})

        saved = backend.get_generated_post(auth.tenant_id, new_id)
        return _parse_post(saved) if saved else row

    try:
        return await run_in_threadpool(_run)
    except KeyError:
        return error_response(
            status_code=404,
            code=ErrorCode.NOT_FOUND,
            message=f"Draft '{content_id}' not found",
        )


# ── POST schedule ───────────────────────────────────────────────────────


@router.post("/{content_id}/schedule", status_code=201)
async def schedule_draft(
    content_id: str,
    body: ScheduleRequest,
    request: Request,
    auth: AuthContext = Depends(verify_token),
) -> dict:
    tenant_err = await assert_no_tenant_in_body(request)
    if tenant_err:
        return tenant_err

    backend = storage.factory.get_backend()

    def _run() -> dict:
        # 1. Fetch current draft to get rev
        original = backend.get_generated_post(auth.tenant_id, content_id)
        if original is None:
            raise KeyError(content_id)

        original_rev = original.get("rev", 1)

        # 2. Update draft status → scheduled
        updated = backend.update_generated_post(
            auth.tenant_id,
            content_id,
            expected_rev=original_rev,
            status="scheduled",
        )

        # 3. Create calendar item linked to this content
        calendar_item = backend.create_calendar_item(
            auth.tenant_id,
            scheduled_date=body.scheduled_date,
            scheduled_time=body.scheduled_time,
            funnel_stage=body.funnel_stage,
            content_id=content_id,
        )

        return {
            "draft": _parse_post(updated),
            "calendar_item": calendar_item,
        }

    try:
        return await run_in_threadpool(_run)
    except RevMismatch:
        current_rev = fetch_current_rev(
            backend.get_generated_post, auth.tenant_id, content_id
        )
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
            message=f"Draft '{content_id}' not found",
        )


# ── POST reject ─────────────────────────────────────────────────────────


@router.post("/{content_id}/reject")
async def reject_draft(
    content_id: str,
    body: RejectRequest,
    request: Request,
    auth: AuthContext = Depends(verify_token),
) -> dict:
    tenant_err = await assert_no_tenant_in_body(request)
    if tenant_err:
        return tenant_err

    backend = storage.factory.get_backend()

    def _run() -> dict:
        original = backend.get_generated_post(auth.tenant_id, content_id)
        if original is None:
            raise KeyError(content_id)

        original_rev = original.get("rev", 1)
        changes = {"status": "rejected"}
        if body.reason:
            changes["meta"] = dict(original.get("meta") or {})
            changes["meta"]["reject_reason"] = body.reason

        updated = backend.update_generated_post(
            auth.tenant_id,
            content_id,
            expected_rev=original_rev,
            **changes,
        )
        return _parse_post(updated)

    try:
        return await run_in_threadpool(_run)
    except RevMismatch:
        current_rev = fetch_current_rev(
            backend.get_generated_post, auth.tenant_id, content_id
        )
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
            message=f"Draft '{content_id}' not found",
        )
