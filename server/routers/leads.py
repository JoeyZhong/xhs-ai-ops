"""
Leads router — 线索雷达收件箱（lead-intent-radar V1）。

Endpoints:
  GET   /api/v1/leads                列表（goal_id/status/trigger 过滤，新鲜度×匹配度排序）
  GET   /api/v1/leads/stats          收件箱度量（今日合格 / 待处理 / 本周沟通机会 / 本周成交）
  GET   /api/v1/leads/{lead_id}      详情
  PUT   /api/v1/leads/{lead_id}      OCC 更新（草稿/状态/outcome）
  POST  /api/v1/leads/{lead_id}/touch  人工通过=标记触达（+可选 outcome，北极星）
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
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

import agent_tools  # noqa: F401  触发工具自注册（outreach.send 等）
from agent_tools import registry
from agent_tools.registry import ToolContext


# ── Pydantic models ───────────────────────────────────────────────────────

class LeadUpdate(BaseModel):
    draft_text: str | None = None
    check_lure_pass: bool | None = None
    check_dup_pass: bool | None = None
    lead_status: Literal[
        "detected", "qualified", "drafted", "pending", "touched", "skipped"
    ] | None = None
    outcome: Literal["replied", "converted"] | None = None
    rev: int  # required for OCC


class TouchRequest(BaseModel):
    outcome: Literal["replied", "converted"] | None = None  # 沟通机会/成交
    rev: int


class SendRequest(BaseModel):
    # 一键发送（V2）。OCC 由 outreach.send 工具内部管理，故 rev 可选。
    account_id: str | None = None


class ScanRequest(BaseModel):
    goal_id: str
    limit_per_keyword: int = 20
    min_match: int = 50


class ScanResponse(BaseModel):
    ok: bool
    error: str | None = None
    stats: dict | None = None
    created_lead_ids: list[str] = []


router = APIRouter(prefix="/api/v1/leads", route_class=IdempotencyRoute)


# ── GET list ─────────────────────────────────────────────────────────────

@router.get("")
async def list_leads(
    goal_id: str | None = Query(None),
    status: str | None = Query(None),
    trigger_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    auth: AuthContext = Depends(verify_token),
) -> dict:
    backend = storage.factory.get_backend()

    def _load() -> dict:
        items = backend.list_leads(
            auth.tenant_id,
            goal_id=goal_id,
            lead_status=status,
            trigger_type=trigger_type,
            limit=limit,
        )
        return {"items": items, "total": len(items)}

    return await run_in_threadpool(_load)


# ── GET stats（度量，必须在 /{lead_id} 之前声明）───────────────────────────

@router.get("/stats")
async def lead_stats(
    goal_id: str | None = Query(None),
    auth: AuthContext = Depends(verify_token),
) -> dict:
    backend = storage.factory.get_backend()

    def _compute() -> dict:
        rows = backend.list_leads(auth.tenant_id, goal_id=goal_id, limit=1000)
        now = datetime.now(timezone.utc)
        today = now.date().isoformat()
        week_ago = (now - timedelta(days=7)).isoformat()

        pending = sum(1 for r in rows
                      if r.get("lead_status") in ("qualified", "drafted", "pending"))
        today_qualified = sum(1 for r in rows
                              if str(r.get("created_at", ""))[:10] == today)
        # 北极星：本周产生沟通机会（回复/加联系）/ 成交
        week_opportunities = sum(
            1 for r in rows
            if r.get("outcome") in ("replied", "converted")
            and str(r.get("updated_at", "")) >= week_ago
        )
        week_conversions = sum(
            1 for r in rows
            if r.get("outcome") == "converted"
            and str(r.get("updated_at", "")) >= week_ago
        )
        return {
            "pending": pending,
            "today_qualified": today_qualified,
            "week_opportunities": week_opportunities,   # 北极星
            "week_conversions": week_conversions,
        }

    return await run_in_threadpool(_compute)


# ── GET detail ───────────────────────────────────────────────────────────

@router.get("/{lead_id}")
async def get_lead(
    lead_id: str,
    auth: AuthContext = Depends(verify_token),
) -> dict:
    backend = storage.factory.get_backend()
    row = await run_in_threadpool(backend.get_lead, auth.tenant_id, lead_id)
    if row is None:
        return error_response(
            status_code=404,
            code=ErrorCode.NOT_FOUND,
            message=f"Lead '{lead_id}' not found",
        )
    return row


# ── PUT update (OCC) ────────────────────────────────────────────────────

@router.put("/{lead_id}")
async def update_lead(
    lead_id: str,
    body: LeadUpdate,
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
            backend.update_lead,
            auth.tenant_id,
            lead_id,
            expected_rev=body.rev,
            **changes,
        )
        return result
    except RevMismatch:
        current_rev = fetch_current_rev(backend.get_lead, auth.tenant_id, lead_id)
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
            message=f"Lead '{lead_id}' not found",
        )


# ── POST touch（人工通过=标记触达 + 可选 outcome）──────────────────────────

@router.post("/{lead_id}/touch")
async def touch_lead(
    lead_id: str,
    body: TouchRequest,
    request: Request,
    auth: AuthContext = Depends(verify_token),
) -> dict:
    tenant_err = await assert_no_tenant_in_body(request)
    if tenant_err:
        return tenant_err

    backend = storage.factory.get_backend()
    changes = {
        "lead_status": "touched",
        "touched_at": datetime.now(timezone.utc).isoformat(),
    }
    if body.outcome:
        changes["outcome"] = body.outcome
    try:
        result = await run_in_threadpool(
            backend.update_lead,
            auth.tenant_id,
            lead_id,
            expected_rev=body.rev,
            **changes,
        )
        return result
    except RevMismatch:
        current_rev = fetch_current_rev(backend.get_lead, auth.tenant_id, lead_id)
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
            message=f"Lead '{lead_id}' not found",
        )


# ── POST send（小红书半自动一键发送，V2）─────────────────────────────────────
# 业务态（dryrun/blocked_checks/engine_not_ready/rate_limited/source_unsupported/sent）
# 一律走 200 + status 字段，由前端按状态渲染；仅"lead 不存在/存储异常"才 4xx/5xx。

@router.post("/{lead_id}/send")
async def send_lead(
    lead_id: str,
    body: SendRequest,
    request: Request,
    auth: AuthContext = Depends(verify_token),
) -> dict:
    tenant_err = await assert_no_tenant_in_body(request)
    if tenant_err:
        return tenant_err

    backend = storage.factory.get_backend()
    ctx = ToolContext(tenant_id=auth.tenant_id, storage=backend, audit=None)
    args = {"lead_id": lead_id}
    if body.account_id:
        args["account_id"] = body.account_id

    def _invoke() -> dict:
        return registry.invoke("outreach.send", args, ctx)

    res = await run_in_threadpool(_invoke)
    if not res.get("ok"):
        err = res.get("error", "send failed")
        if "not found" in err:
            return error_response(status_code=404, code=ErrorCode.NOT_FOUND, message=err)
        return error_response(status_code=422, code=ErrorCode.VALIDATION_ERROR, message=err)
    return res["data"]


# ── POST scan（手动触发一次雷达扫描，V2）────────────────────────────────────

@router.post("/scan")
async def trigger_scan(
    body: ScanRequest,
    auth: AuthContext = Depends(verify_token),
) -> dict:
    from agents.lead_radar import scan_goal
    import os  # noqa: PLC0415

    # 手动扫描 = 真实采集：默认走 native（与定时雷达 _radar_scan 一致）。
    # 不设则 _get_collector() 退回库级 fixture 默认（假数据）。
    # operator 可用 env XHS_COLLECTOR 显式覆盖——setdefault 不覆盖已设值。
    os.environ.setdefault("XHS_COLLECTOR", "native")

    backend = storage.factory.get_backend()

    def _run() -> dict:
        return scan_goal(
            auth.tenant_id,
            body.goal_id,
            storage=backend,
            limit_per_keyword=body.limit_per_keyword,
            min_match=body.min_match,
        )

    result = await run_in_threadpool(_run)
    if not result.get("ok"):
        return error_response(
            status_code=422,
            code=ErrorCode.VALIDATION_ERROR,
            message=result.get("error", "scan failed"),
        )
    return result
