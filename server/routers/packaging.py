from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agent_tools import packaging_rules
from server.auth import AuthContext, verify_token
from server.errors import ErrorCode, error_response
from server.middleware.idempotency import IdempotencyRoute


router = APIRouter(prefix="/api/v1/packaging", route_class=IdempotencyRoute)


class PackagingRulesUpdate(BaseModel):
    rules: str


def _read_rules_response() -> dict[str, str]:
    path = packaging_rules._RULES_PATH
    stat = path.stat()
    return {
        "rules": path.read_text(encoding="utf-8"),
        "updated_at": datetime.fromtimestamp(
            stat.st_mtime,
            tz=timezone.utc,
        ).isoformat(),
    }


def _write_rules_response(rules: str) -> dict[str, str]:
    path = packaging_rules._RULES_PATH
    tmp_path = path.with_suffix(".md.tmp")
    tmp_path.write_text(rules, encoding="utf-8")
    os.replace(tmp_path, path)
    return _read_rules_response()


@router.get("/rules", response_model=None)
async def get_packaging_rules(
    auth: AuthContext = Depends(verify_token),
) -> dict[str, str]:
    return await run_in_threadpool(_read_rules_response)


@router.put("/rules", response_model=None)
async def update_packaging_rules(
    body: PackagingRulesUpdate,
    auth: AuthContext = Depends(verify_token),
) -> JSONResponse | dict[str, str]:
    if "五大爆文标题公式" not in body.rules or "CES" not in body.rules:
        return error_response(
            status_code=422,
            code=ErrorCode.PACKAGING_INVALID,
            message="Packaging rules must include 五大爆文标题公式 and CES.",
            field="rules",
        )
    return await run_in_threadpool(_write_rules_response, body.rules)
