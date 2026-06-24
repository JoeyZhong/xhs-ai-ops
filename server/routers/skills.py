"""
Skills Hub REST API.
所有阻塞 IO 必须用 run_in_threadpool 包裹。
鉴权：所有 endpoint Depends(verify_token) 拿 tenant_id；
通用池写还需 is_admin claim（MVP 阶段 is_admin=False）。
"""
from __future__ import annotations

import io
import posixpath
import zipfile
from typing import Optional

import yaml
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from server.auth import AuthContext, verify_token
from storage.base import RevMismatch
from storage.factory import get_backend


router = APIRouter(prefix="/api/v1", tags=["skills"])


# ── Pydantic models ─────────────────────────────────────────────────────

class SkillSummary(BaseModel):
    id: str
    name: str
    description: str
    suggested_for: list[str] = []
    owner: str          # "universal" | "mine"
    source_skill_id: Optional[str] = None
    version: str
    rev: int
    tenant_id: Optional[str] = None


class SkillDetail(SkillSummary):
    body: str
    status: str
    created_at: str
    updated_at: str


class SkillCreateRequest(BaseModel):
    name: str
    description: str
    body: str
    suggested_for: list[str] = []
    owner: str = "mine"  # "universal" 需 is_admin


class SkillUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    body: Optional[str] = None
    suggested_for: Optional[list[str]] = None
    expected_rev: int    # OCC


class ForkRequest(BaseModel):
    name: Optional[str] = None  # 默认 = 源 name + " (fork)"


class EquipRequest(BaseModel):
    skill_id: str


# ── 辅助 ─────────────────────────────────────────────────────────────────

SUMMARY_FIELDS = {"id", "name", "description", "suggested_for", "owner",
                  "source_skill_id", "version", "rev"}


def _to_summary(skill: dict, auth: AuthContext) -> SkillSummary:
    """将 backend dict 转为 SkillSummary。"""
    is_universal = skill.get("tenant_id") is None
    return SkillSummary(
        id=skill["id"],
        name=skill["name"],
        description=skill["description"],
        suggested_for=skill.get("suggested_for", []),
        owner="universal" if is_universal else "mine",
        source_skill_id=skill.get("source_skill_id"),
        version=skill.get("version", "1.0.0"),
        rev=int(skill.get("rev", 0) or 0),
    )


def _to_detail(skill: dict, auth: AuthContext) -> SkillDetail:
    """将 backend dict 转为 SkillDetail。"""
    is_universal = skill.get("tenant_id") is None

    def _str(v):
        if v is None:
            return ""
        if isinstance(v, str):
            return v
        return str(v)

    return SkillDetail(
        id=skill["id"],
        name=skill["name"],
        description=skill["description"],
        suggested_for=skill.get("suggested_for", []),
        owner="universal" if is_universal else "mine",
        source_skill_id=skill.get("source_skill_id"),
        version=skill.get("version", "1.0.0"),
        rev=int(skill.get("rev", 0) or 0),
        body=skill.get("body", ""),
        status=skill.get("status", "active"),
        created_at=_str(skill.get("created_at")),
        updated_at=_str(skill.get("updated_at")),
    )


def _check_skill_visible(backend, skill_id: str,
                         tenant_id: str) -> dict:
    """校验 skill 对调用者可见，返回 skill dict 或抛 404。"""
    try:
        skill = backend.get_skill(skill_id, tenant_id)
        # 通用池所有人都可见；私有只有 owner
        tid = skill.get("tenant_id")
        if tid is not None and tid != tenant_id:
            raise HTTPException(status_code=404, detail="skill not found")
        return skill
    except KeyError:
        raise HTTPException(status_code=404, detail="skill not found")


def _check_skill_writable(backend, skill_id: str,
                          tenant_id: str, is_admin: bool) -> dict:
    """校验 skill 可被当前用户写入。抛 403/404。"""
    try:
        skill = backend.get_skill(skill_id, tenant_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="skill not found")

    tid = skill.get("tenant_id")
    if tid is None and not is_admin:
        raise HTTPException(status_code=403,
                            detail="superadmin required to write universal pool")
    if tid is not None and tid != tenant_id:
        raise HTTPException(status_code=404, detail="skill not found")
    return skill


# ── Zip import helpers ───────────────────────────────────────────────────

ALLOWED_SKILL_ROLES = {"intel", "content", "analyst"}
MAX_SKILL_ZIP_BYTES = 2 * 1024 * 1024


def _safe_zip_name(name: str) -> bool:
    normalized = posixpath.normpath(name.replace("\\", "/"))
    if normalized.startswith("../") or normalized == "..":
        return False
    if normalized.startswith("/"):
        return False
    if ":" in normalized.split("/")[0]:
        return False
    return True


def _find_skill_md(zf: zipfile.ZipFile) -> str:
    names = [n for n in zf.namelist() if not n.endswith("/")]
    for name in names:
        if not _safe_zip_name(name):
            raise HTTPException(status_code=400, detail="unsafe zip path")

    candidates = [n for n in names if n.replace("\\", "/").split("/")[-1] == "SKILL.md"]
    if not candidates:
        raise HTTPException(status_code=400, detail="SKILL.md not found in zip")
    if "SKILL.md" in candidates:
        return "SKILL.md"

    top_levels = {n.replace("\\", "/").split("/")[0] for n in names}
    if len(top_levels) == 1 and len(candidates) == 1:
        return candidates[0]

    raise HTTPException(status_code=400, detail="zip must contain one SKILL.md")


def _parse_skill_markdown(text: str) -> dict:
    # Normalize line endings and strip BOM
    text = text.lstrip("﻿").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        raise HTTPException(status_code=400, detail="SKILL.md missing YAML frontmatter")
    try:
        _, frontmatter, body = text.split("---", 2)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid SKILL.md frontmatter")

    try:
        meta = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError:
        raise HTTPException(status_code=400, detail="invalid SKILL.md YAML")

    name = str(meta.get("name") or "").strip()
    description = str(meta.get("description") or "").strip()
    version = str(meta.get("version") or "1.0.0").strip()
    suggested_for = meta.get("suggested_for") or []

    if not name:
        raise HTTPException(status_code=400, detail="frontmatter.name is required")
    if not description:
        raise HTTPException(status_code=400, detail="frontmatter.description is required")
    if not isinstance(suggested_for, list):
        raise HTTPException(status_code=400, detail="frontmatter.suggested_for must be a list")

    roles = [str(r).strip() for r in suggested_for if str(r).strip()]
    invalid = [r for r in roles if r not in ALLOWED_SKILL_ROLES]
    if invalid:
        raise HTTPException(status_code=400, detail=f"invalid suggested_for role: {invalid[0]}")

    return {
        "name": name,
        "description": description,
        "version": version,
        "suggested_for": roles,
        "body": text,
    }


def _read_skill_from_zip(raw: bytes) -> dict:
    if len(raw) > MAX_SKILL_ZIP_BYTES:
        raise HTTPException(status_code=400, detail="zip too large")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            skill_name = _find_skill_md(zf)
            text = zf.read(skill_name).decode("utf-8")
            # Collect extras (non-SKILL.md text files)
            extras = {}
            prefix = posixpath.dirname(skill_name)
            for name in zf.namelist():
                if name.endswith("/"):
                    continue
                if name.replace("\\", "/").split("/")[-1] == "SKILL.md":
                    continue
                rel = name[len(prefix) + 1:] if prefix else name
                if rel and len(rel) < 200:
                    try:
                        extras[rel] = zf.read(name).decode("utf-8")
                    except UnicodeDecodeError:
                        pass  # skip binary files
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="invalid zip file")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="SKILL.md must be UTF-8")
    parsed = _parse_skill_markdown(text)
    parsed["extras"] = extras
    return parsed


# ── Endpoints ───────────────────────────────────────────────────────────

@router.get("/skills", response_model=list[SkillSummary])
async def list_skills(
    owner: str = Query("all", pattern="^(universal|mine|all)$"),
    suggested_for: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100),
    cursor: Optional[str] = None,
    auth: AuthContext = Depends(verify_token),
) -> list[SkillSummary]:
    def _run() -> list[SkillSummary]:
        backend = get_backend()
        skills = backend.list_skills(
            tenant_id=auth.tenant_id if owner in ("mine", "all") else None,
            owner=owner,
            suggested_for=suggested_for,
            limit=limit,
            cursor=cursor,
        )
        return [_to_summary(s, auth) for s in skills]
    return await run_in_threadpool(_run)


@router.get("/skills/{skill_id}", response_model=SkillDetail)
async def get_skill(skill_id: str, auth: AuthContext = Depends(verify_token)) -> SkillDetail:
    def _run() -> SkillDetail:
        backend = get_backend()
        skill = _check_skill_visible(backend, skill_id, auth.tenant_id)
        return _to_detail(skill, auth)
    return await run_in_threadpool(_run)


@router.post("/skills", response_model=SkillDetail, status_code=201)
async def create_skill(req: SkillCreateRequest,
                       auth: AuthContext = Depends(verify_token)) -> SkillDetail:
    if req.owner == "universal" and not auth.is_admin:
        raise HTTPException(status_code=403,
                            detail="superadmin required to write universal pool")

    def _run() -> SkillDetail:
        backend = get_backend()
        tenant_id = None if req.owner == "universal" else auth.tenant_id
        skill = backend.create_skill(
            tenant_id=tenant_id,
            name=req.name,
            description=req.description,
            body=req.body,
            suggested_for=req.suggested_for,
        )
        return _to_detail(skill, auth)
    return await run_in_threadpool(_run)


@router.put("/skills/{skill_id}", response_model=SkillDetail)
async def update_skill(skill_id: str, req: SkillUpdateRequest,
                       auth: AuthContext = Depends(verify_token)) -> SkillDetail:
    def _run() -> SkillDetail:
        backend = get_backend()
        _check_skill_writable(backend, skill_id, auth.tenant_id, auth.is_admin)

        changes = {}
        if req.name is not None:
            changes["name"] = req.name
        if req.description is not None:
            changes["description"] = req.description
        if req.body is not None:
            changes["body"] = req.body
        if req.suggested_for is not None:
            changes["suggested_for"] = req.suggested_for

        try:
            skill = backend.update_skill(
                skill_id, auth.tenant_id,
                expected_rev=req.expected_rev,
                **changes,
            )
        except RevMismatch:
            raise HTTPException(status_code=409, detail="stale rev, refetch")
        return _to_detail(skill, auth)
    return await run_in_threadpool(_run)


@router.delete("/skills/{skill_id}")
async def delete_skill(skill_id: str,
                       auth: AuthContext = Depends(verify_token)) -> dict:
    def _run() -> dict:
        backend = get_backend()
        _check_skill_writable(backend, skill_id, auth.tenant_id, auth.is_admin)
        try:
            unequipped = backend.delete_skill(skill_id, auth.tenant_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="skill not found")
        return {"deleted": True, "unequipped_from": unequipped}
    return await run_in_threadpool(_run)


@router.post("/skills/{skill_id}/fork", response_model=SkillDetail, status_code=201)
async def fork_skill(skill_id: str, req: ForkRequest,
                     auth: AuthContext = Depends(verify_token)) -> SkillDetail:
    def _run() -> SkillDetail:
        backend = get_backend()
        source = _check_skill_visible(backend, skill_id, auth.tenant_id)

        name = req.name or f"{source['name']} (fork)"
        new_skill = backend.create_skill(
            tenant_id=auth.tenant_id,
            name=name,
            description=source.get("description", ""),
            body=source.get("body", ""),
            suggested_for=source.get("suggested_for", []),
            source_skill_id=source["id"],
        )

        # 源在通用池时：auto-equip 到 suggested_for 对应 role
        if source.get("tenant_id") is None:
            for role in source.get("suggested_for", []):
                if role in ("intel", "content", "analyst"):
                    backend.equip(auth.tenant_id, role, new_skill["id"])

        return _to_detail(new_skill, auth)
    return await run_in_threadpool(_run)


@router.post("/skills/import", response_model=SkillDetail, status_code=201)
async def import_skill_zip(
    file: UploadFile = File(...),
    owner: str = Form("mine"),
    auto_equip: bool = Form(False),
    auth: AuthContext = Depends(verify_token),
) -> SkillDetail:
    if owner not in ("mine", "universal"):
        raise HTTPException(status_code=400, detail="owner must be mine or universal")
    if owner == "universal" and not auth.is_admin:
        raise HTTPException(status_code=403, detail="superadmin required to write universal pool")

    raw = await file.read()
    parsed = _read_skill_from_zip(raw)

    def _run() -> SkillDetail:
        backend = get_backend()
        tenant_id = None if owner == "universal" else auth.tenant_id
        skill = backend.create_skill(
            tenant_id=tenant_id,
            name=parsed["name"],
            description=parsed["description"],
            body=parsed["body"],
            suggested_for=parsed["suggested_for"],
            extras=parsed.get("extras", {}),
        )
        if auto_equip:
            for role in parsed["suggested_for"]:
                backend.equip(auth.tenant_id, role, skill["id"])
        return _to_detail(skill, auth)

    return await run_in_threadpool(_run)


@router.get("/agents/{role}/equipment", response_model=list[SkillSummary])
async def list_equipment(role: str,
                         auth: AuthContext = Depends(verify_token)) -> list[SkillSummary]:
    if role not in ("intel", "content", "analyst"):
        raise HTTPException(status_code=404, detail=f"unknown agent role: {role}")

    def _run() -> list[SkillSummary]:
        backend = get_backend()
        equipped = backend.list_equipment(auth.tenant_id, role)
        return [SkillSummary(**s) for s in equipped]
    return await run_in_threadpool(_run)


@router.post("/agents/{role}/equipment", status_code=201)
async def equip_skill(role: str, req: EquipRequest,
                      auth: AuthContext = Depends(verify_token)) -> dict:
    if role not in ("intel", "content", "analyst"):
        raise HTTPException(status_code=404, detail=f"unknown agent role: {role}")

    def _run() -> dict:
        backend = get_backend()
        # 先校验 skill 可见
        _check_skill_visible(backend, req.skill_id, auth.tenant_id)
        backend.equip(auth.tenant_id, role, req.skill_id)
        return {"equipped": True, "skill_id": req.skill_id}
    return await run_in_threadpool(_run)


@router.delete("/agents/{role}/equipment/{skill_id}")
async def unequip_skill(role: str, skill_id: str,
                        auth: AuthContext = Depends(verify_token)) -> dict:
    if role not in ("intel", "content", "analyst"):
        raise HTTPException(status_code=404, detail=f"unknown agent role: {role}")

    def _run() -> dict:
        backend = get_backend()
        backend.unequip(auth.tenant_id, role, skill_id)
        return {"unequipped": True, "skill_id": skill_id}
    return await run_in_threadpool(_run)
