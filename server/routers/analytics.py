"""P3.2 · Performance 回填端点。

POST /api/v1/analytics/performance
  - 录入一篇已发布笔记的真实互动数据
  - 计算 CES 写回 generated_content.meta.ces_score（OCC）
  - 更新 goals.used_angles 里对应 angle 的 last_ces / evidence_count

设计基线: openspec/changes/content-lifecycle-v2/tasks.md P3.2 + design.md §5
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from agents.used_angles import normalize_used_angles
from server.auth import AuthContext, verify_token
from server.middleware.idempotency import IdempotencyRoute
from storage.base import RevMismatch
import storage.factory


CONFIG_DIR = Path("config")

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"], route_class=IdempotencyRoute)


# CES = 点赞×1 + 收藏×1 + 评论×4 + 分享×4 + 关注×8
def _calc_ces(*, likes: int, collects: int, comments_count: int,
              shares: int, follows: int) -> int:
    return likes + collects + comments_count * 4 + shares * 4 + follows * 8


class PerformanceRequest(BaseModel):
    content_id: str
    likes: int = Field(0, ge=0)
    comments_count: int = Field(0, ge=0)
    shares: int = Field(0, ge=0)
    collects: int = Field(0, ge=0)
    follows: int = Field(0, ge=0)


def _update_used_angles(backend, tenant_id: str, angle: str, ces: int) -> None:
    """把 ces 回写到 goals.used_angles 对应 angle 的 last_ces，evidence_count +1。
    若该 angle 不在 used_angles 里则追加一条 unknown 态。"""
    if not angle:
        return
    data = backend.load_goals(tenant_id)
    changed = False
    for goal in data.get("goals", []):
        ua = normalize_used_angles(goal.get("used_angles", []))
        found = False
        for entry in ua:
            if entry["angle"] == angle:
                entry["last_ces"] = ces
                entry["evidence_count"] = int(entry.get("evidence_count", 0) or 0) + 1
                found = True
                break
        if not found:
            ua.append({"angle": angle, "status": "unknown",
                       "evidence_count": 1, "last_ces": ces})
        if ua != goal.get("used_angles"):
            goal["used_angles"] = ua
            changed = True
    if changed:
        backend.save_goals(tenant_id, data)


@router.post("/performance")
async def record_performance(
    body: PerformanceRequest,
    auth: AuthContext = Depends(verify_token),
) -> dict:
    def _run() -> dict:
        backend = storage.factory.get_backend()
        post = backend.get_generated_post(auth.tenant_id, body.content_id)
        if post is None:
            raise HTTPException(status_code=404, detail=f"content '{body.content_id}' not found")

        ces = _calc_ces(
            likes=body.likes, collects=body.collects,
            comments_count=body.comments_count, shares=body.shares,
            follows=body.follows,
        )

        # 写回 meta.ces_score（OCC，带一次 refetch 重试）
        meta = dict(post.get("meta") or {})
        meta["ces_score"] = ces
        meta["performance"] = {
            "likes": body.likes, "collects": body.collects,
            "comments_count": body.comments_count, "shares": body.shares,
            "follows": body.follows,
        }
        for _attempt in range(2):
            current = backend.get_generated_post(auth.tenant_id, body.content_id)
            if current is None:
                raise HTTPException(status_code=404, detail=f"content '{body.content_id}' not found")
            try:
                backend.update_generated_post(
                    auth.tenant_id, body.content_id,
                    expected_rev=int(current.get("rev", 0) or 0),
                    meta=meta,
                )
                break
            except RevMismatch:
                continue

        # 更新 used_angles
        angle = str(post.get("angle") or "").strip()
        _update_used_angles(backend, auth.tenant_id, angle, ces)

        return {"ok": True, "content_id": body.content_id, "ces_score": ces, "angle": angle}

    return await run_in_threadpool(_run)
