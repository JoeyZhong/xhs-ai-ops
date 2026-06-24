from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from agent_tools.intel_evidence import (
    ANGLE_ENUM,
    FUNNEL_ENUM,
    extract_evidence_from_storage,
)
from server.auth import AuthContext, verify_token
from server.middleware.idempotency import IdempotencyRoute
import storage.factory


CONFIG_DIR = Path("config")

router = APIRouter(prefix="/api/v1/intel", tags=["intel"], route_class=IdempotencyRoute)


class EvidenceExtractRequest(BaseModel):
    ces_threshold: int | None = Field(None, ge=0)
    batch_size: int = Field(10, ge=1, le=10)


def _default_ces_threshold() -> int:
    try:
        settings = json.loads((CONFIG_DIR / "settings.json").read_text(encoding="utf-8"))
        return int((settings.get("ces_thresholds") or {}).get("evidence_extraction_min", 250))
    except Exception:
        return 250


@router.post("/evidence/extract")
async def extract_evidence(
    body: EvidenceExtractRequest,
    auth: AuthContext = Depends(verify_token),
) -> dict:
    def _run() -> dict:
        backend = storage.factory.get_backend()
        threshold = body.ces_threshold if body.ces_threshold is not None else _default_ces_threshold()
        return extract_evidence_from_storage(
            tenant_id=auth.tenant_id,
            storage=backend,
            ces_threshold=float(threshold),
            batch_size=body.batch_size,
        )

    return await run_in_threadpool(_run)


@router.get("/evidence")
async def list_evidence(
    angle: str | None = Query(None),
    funnel_stage: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    auth: AuthContext = Depends(verify_token),
) -> dict:
    def _run() -> dict:
        if angle is not None and angle not in ANGLE_ENUM:
            return {"items": [], "total": 0}
        if funnel_stage is not None and funnel_stage not in FUNNEL_ENUM:
            return {"items": [], "total": 0}

        backend = storage.factory.get_backend()
        all_items = backend.list_evidence(
            auth.tenant_id,
            angle=angle,
            funnel_stage=funnel_stage,
            limit=100000,
        )
        return {"items": all_items[offset:offset + limit], "total": len(all_items)}

    return await run_in_threadpool(_run)
