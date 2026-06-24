from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from server.auth import AuthContext, verify_token

CONFIG_DIR = Path("config")

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


def _load_settings() -> dict:
    return json.loads((CONFIG_DIR / "settings.json").read_text(encoding="utf-8"))


def _save_settings(data: dict) -> None:
    (CONFIG_DIR / "settings.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── Kimi（全局 LLM 配置，不按 tenant 隔离） ─────────────────────────────────

@router.get("/kimi/test")
async def test_kimi(_: AuthContext = Depends(verify_token)) -> dict:
    def _test():
        settings = _load_settings()
        provider = settings.get("llm_provider", "kimi")
        if provider == "mock":
            return {"ok": True, "message": "mock provider"}
        try:
            from agent_tools.kimi import call_kimi
            call_kimi("ping", max_tokens=1)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    return await run_in_threadpool(_test)


class KimiSaveRequest(BaseModel):
    api_key: str
    model: str = ""
    base_url: str = ""


@router.post("/kimi")
async def save_kimi(body: KimiSaveRequest, _: AuthContext = Depends(verify_token)) -> dict:
    def _save():
        settings = _load_settings()
        settings["kimi_api_key"] = body.api_key
        settings["kimi_model"] = body.model
        settings["kimi_base_url"] = body.base_url
        _save_settings(settings)
        return {"ok": True}

    return await run_in_threadpool(_save)


# ── Cookie（per-tenant，走 cookie_manager） ──────────────────────────────

@router.get("/cookie/status")
async def cookie_status(auth: AuthContext = Depends(verify_token)) -> dict:
    def _check():
        from storage import cookie_manager  # noqa: PLC0415
        accounts = cookie_manager.list_accounts(tenant_id=auth.tenant_id)
        return {"valid": len(accounts) > 0, "count": len(accounts)}

    return await run_in_threadpool(_check)


class CookieSaveRequest(BaseModel):
    account_id: str
    cookie: str


@router.post("/cookie")
async def save_cookie(body: CookieSaveRequest, auth: AuthContext = Depends(verify_token)) -> dict:
    def _save():
        from storage import cookie_manager  # noqa: PLC0415
        cookie_manager.save_cookie(
            account_id=body.account_id,
            cookie_str=body.cookie,
            tenant_id=auth.tenant_id,
        )
        return {"ok": True}

    return await run_in_threadpool(_save)
