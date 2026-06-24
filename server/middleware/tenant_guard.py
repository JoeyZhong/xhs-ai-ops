from __future__ import annotations

import json
from typing import Callable

from fastapi.requests import Request
from fastapi.responses import JSONResponse

from server.errors import ErrorCode, error_response


async def assert_no_tenant_in_body(request: Request) -> JSONResponse | None:
    """Return 422 if request body contains tenant_id."""
    ct = (request.headers.get("content-type") or "").lower()
    if "json" not in ct:
        return None
    try:
        raw = await request.json()
    except (json.JSONDecodeError, RuntimeError, UnicodeDecodeError):
        return None
    if isinstance(raw, dict) and "tenant_id" in raw:
        return error_response(
            status_code=422,
            code=ErrorCode.TENANT_IN_BODY_FORBIDDEN,
            message="tenant_id must not be present in request body",
            field="tenant_id",
        )
    return None


def fetch_current_rev(get_fn: Callable, tenant_id: str, obj_id: str) -> int | None:
    """Query current rev for the error envelope after a RevMismatch."""
    try:
        return get_fn(tenant_id, obj_id).get("rev")
    except KeyError:
        return None
