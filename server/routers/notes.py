from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool

from server.auth import AuthContext, verify_token
import storage.factory

router = APIRouter(prefix="/api/v1/notes", tags=["notes"])

_CES_WEIGHTS = {
    "点赞数": 1,
    "收藏数": 1,
    "评论数": 4,
    "分享数": 4,
    "关注数": 8,
}


def _calc_ces(row: dict) -> int:
    def _to_int(v) -> int:
        if v is None or (isinstance(v, float) and v != v):
            return 0
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0
    return sum(_to_int(row.get(col, 0)) * w for col, w in _CES_WEIGHTS.items())


def _dedup_key(record: dict) -> str:
    for col in ("笔记ID", "note_id", "id"):
        v = record.get(col)
        if v is None:
            continue
        s = str(v).strip()
        if s and s.lower() != "nan":
            return s
    title = record.get("标题") or record.get("笔记标题") or ""
    if isinstance(title, float):
        title = ""
    return str(title).strip() or f"_ces{record.get('ces_score', 0)}_{record.get('点赞数', 0)}"


@router.get("")
async def list_notes(
    goal_id: str = Query("default"),
    auth: AuthContext = Depends(verify_token),
) -> dict:
    def _run():
        backend = storage.factory.get_backend()
        since = datetime(2000, 1, 1)  # 全量查询
        goal_id_param = goal_id if goal_id and goal_id != "default" else None
        df = backend.list_collected_data(auth.tenant_id, since=since, goal_id=goal_id_param)
        if df.empty:
            return {"notes": [], "total": 0}

        records = df.to_dict("records")
        seen: dict[str, dict[str, Any]] = {}
        for record in records:
            record["ces_score"] = _calc_ces(record)
            key = _dedup_key(record)
            seen[key] = record

        notes = list(seen.values())
        return {"notes": notes, "total": len(notes)}

    return await run_in_threadpool(_run)
