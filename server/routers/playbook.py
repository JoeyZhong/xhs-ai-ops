"""
P3.2.D6-D8 · Playbook Draft Review API.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from typing import Optional

from server.auth import AuthContext, verify_token

router = APIRouter(prefix="/api/v1/playbook", tags=["playbook"])


# ── 数据模型 ─────────────────────────────────────────────────────────────

class DraftItem(BaseModel):
    id: str
    body: str
    status: str
    source: str
    confidence: str
    rev: int

class DraftListResponse(BaseModel):
    items: list[DraftItem]
    total: int

class CountResponse(BaseModel):
    count: int

class EditRequest(BaseModel):
    body: str


# ── MemoryLayer lazy 初始化 ────────────────────────────────────────────

def _get_memory_layer():
    from agents.memory import MemoryLayer  # noqa: PLC0415
    from storage.factory import get_backend  # noqa: PLC0415
    backend = get_backend()
    return MemoryLayer(storage=backend)


def _parse_entries(mem, tenant_id: str):
    from agents.memory import parse_entries  # noqa: PLC0415
    content = mem.read(tenant_id, "content", "playbook.md") or ""
    return parse_entries(content)


def _find_entry(mem, entry_id: str, tenant_id: str):
    from agents.memory import Entry  # noqa: PLC0415
    header, entries = _parse_entries(mem, tenant_id)
    entry = entries.get(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"entry '{entry_id}' not found")
    return entry, header, entries


# ── GET /drafts ─────────────────────────────────────────────────────────

@router.get("/drafts", response_model=DraftListResponse)
async def list_drafts(auth: AuthContext = Depends(verify_token)) -> dict:
    def _run() -> dict:
        mem = _get_memory_layer()
        header, entries = _parse_entries(mem, auth.tenant_id)  # noqa: F841
        drafts = [
            DraftItem(
                id=eid, body=e.body, status=e.status,
                source=e.source, confidence=e.confidence, rev=e.rev,
            )
            for eid, e in entries.items()
            if e.status == "draft"
        ]
        return {"items": [d.model_dump() for d in drafts], "total": len(drafts)}

    return await run_in_threadpool(_run)


# ── POST /drafts/{id}/accept ────────────────────────────────────────────

@router.post("/drafts/{entry_id}/accept")
async def accept_draft(entry_id: str, auth: AuthContext = Depends(verify_token)) -> dict:
    def _run() -> dict:
        mem = _get_memory_layer()
        entry, header, entries = _find_entry(mem, entry_id, auth.tenant_id)

        if entry.status != "draft":
            raise HTTPException(
                status_code=400,
                detail=f"entry '{entry_id}' status is '{entry.status}', expected 'draft'",
            )

        new_rev = mem.replace_entry(
            auth.tenant_id, "content", "playbook.md",
            entry_id, entry.body, "analyst",
            expected_rev=entry.rev,
            entry_meta={"status": "active", "source": entry.source, "confidence": entry.confidence},
        )
        return {"ok": True, "entry_id": entry_id, "new_rev": new_rev}

    return await run_in_threadpool(_run)


# ── POST /drafts/{id}/reject ────────────────────────────────────────────

@router.post("/drafts/{entry_id}/reject")
async def reject_draft(entry_id: str, auth: AuthContext = Depends(verify_token)) -> dict:
    def _run() -> dict:
        mem = _get_memory_layer()
        entry, header, entries = _find_entry(mem, entry_id, auth.tenant_id)

        if entry.status != "draft":
            raise HTTPException(
                status_code=400,
                detail=f"entry '{entry_id}' status is '{entry.status}', expected 'draft'",
            )

        new_rev = mem.replace_entry(
            auth.tenant_id, "content", "playbook.md",
            entry_id, entry.body, "analyst",
            expected_rev=entry.rev,
            entry_meta={"status": "rejected", "source": entry.source, "confidence": entry.confidence},
        )
        return {"ok": True, "entry_id": entry_id, "new_rev": new_rev}

    return await run_in_threadpool(_run)


# ── PUT /drafts/{id} ───────────────────────────────────────────────────

@router.put("/drafts/{entry_id}")
async def edit_draft(entry_id: str, body: EditRequest,
                     auth: AuthContext = Depends(verify_token)) -> dict:
    def _run() -> dict:
        mem = _get_memory_layer()
        entry, header, entries = _find_entry(mem, entry_id, auth.tenant_id)

        if entry.status != "draft":
            raise HTTPException(
                status_code=400,
                detail=f"entry '{entry_id}' status is '{entry.status}', expected 'draft'",
            )

        new_rev = mem.replace_entry(
            auth.tenant_id, "content", "playbook.md",
            entry_id, body.body, "analyst",
            expected_rev=entry.rev,
            entry_meta={"status": "active", "source": "manual", "confidence": entry.confidence},
        )
        return {"ok": True, "entry_id": entry_id, "new_rev": new_rev}

    return await run_in_threadpool(_run)


# ── GET /drafts/count ──────────────────────────────────────────────────

@router.get("/drafts/count", response_model=CountResponse)
async def count_drafts(auth: AuthContext = Depends(verify_token)) -> dict:
    def _run() -> dict:
        mem = _get_memory_layer()
        header, entries = _parse_entries(mem, auth.tenant_id)  # noqa: F841
        count = sum(
            1 for e in entries.values()
            if e.status == "draft"
        )
        return {"count": count}

    return await run_in_threadpool(_run)
