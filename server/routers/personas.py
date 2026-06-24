from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from server.auth import AuthContext, verify_token

CONFIG_DIR = Path("config")

router = APIRouter(prefix="/api/v1/personas", tags=["personas"])


# ── 文件级 helpers（personas.json multi-container；Protocol 暂无 load_personas 方法）──

def _load() -> dict:
    return json.loads((CONFIG_DIR / "personas.json").read_text(encoding="utf-8"))


def _save(data: dict) -> None:
    (CONFIG_DIR / "personas.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


class PersonaCreate(BaseModel):
    nickname: str
    background: str = ""
    style_notes: str = ""
    tone: str = ""
    system_prompt: str = ""


@router.get("")
async def list_personas(auth: AuthContext = Depends(verify_token)) -> dict:
    return await run_in_threadpool(_load)


@router.post("", status_code=201)
async def create_persona(body: PersonaCreate, auth: AuthContext = Depends(verify_token)) -> dict:
    data = await run_in_threadpool(_load)
    new_persona: dict = {
        "id": f"p_{uuid.uuid4().hex[:8]}",
        "nickname": body.nickname,
        "background": body.background,
        "style_notes": body.style_notes,
        "tone": body.tone,
        "system_prompt": body.system_prompt,
        "created_at": "",
    }
    data["personas"].append(new_persona)
    await run_in_threadpool(_save, data)
    return new_persona


@router.put("/{persona_id}")
async def update_persona(
    persona_id: str,
    body: dict[str, Any] = Body(...),
    auth: AuthContext = Depends(verify_token),
) -> dict:
    data = await run_in_threadpool(_load)
    for i, p in enumerate(data["personas"]):
        if p["id"] == persona_id:
            p.update(body)
            data["personas"][i] = p
            await run_in_threadpool(_save, data)
            return p
    raise HTTPException(status_code=404, detail=f"persona '{persona_id}' not found")


@router.post("/{persona_id}/activate")
async def activate_persona(persona_id: str, auth: AuthContext = Depends(verify_token)) -> dict:
    data = await run_in_threadpool(_load)
    ids = [p["id"] for p in data["personas"]]
    if persona_id not in ids:
        raise HTTPException(status_code=404, detail=f"persona '{persona_id}' not found")
    data["active_id"] = persona_id
    await run_in_threadpool(_save, data)
    return {"active_id": persona_id}
