from __future__ import annotations

import uuid
from enum import Enum

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.requests import Request
from fastapi.responses import JSONResponse
from psycopg2 import DatabaseError, DataError, IntegrityError, OperationalError


class ErrorCode(str, Enum):
    """错误码常量，映射 design.md §3.5 的 12 个 code。"""
    INVALID_STATUS_TRANSITION = "invalid_status_transition"
    AUTH_REQUIRED = "auth_required"
    AUTH_INVALID = "auth_invalid"
    NOT_FOUND = "not_found"
    REV_MISMATCH = "rev_mismatch"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    VALIDATION_ERROR = "validation_error"
    TENANT_IN_BODY_FORBIDDEN = "tenant_in_body_forbidden"
    STRATEGY_MISSING_ANCHOR = "strategy_missing_anchor"
    PACKAGING_INVALID = "packaging_invalid"
    MISSING_IDEMPOTENCY_KEY = "missing_idempotency_key"
    LLM_PROVIDER_ERROR = "llm_provider_error"
    STORAGE_ERROR = "storage_error"


def error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    detail: str | None = None,
    field: str | None = None,
    current_rev: int | None = None,
) -> JSONResponse:
    """返回统一 error envelope（design.md §3.5）。"""
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "detail": detail[:500] if detail else None,
                "field": field,
                "current_rev": current_rev,
                "request_id": str(uuid.uuid4()),
            }
        },
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errs = exc.errors()
    field = ".".join(str(p) for p in errs[0]["loc"]) if errs else None
    return error_response(
        status_code=422,
        code=ErrorCode.VALIDATION_ERROR,
        message=errs[0]["msg"] if errs else "Validation failed",
        field=field,
    )


async def pg_integrity_handler(request: Request, exc: IntegrityError):
    return error_response(
        status_code=422,
        code=ErrorCode.VALIDATION_ERROR,
        message="Database constraint violated",
        detail=str(exc),
    )


async def pg_storage_handler(request: Request, exc: DatabaseError):
    return error_response(
        status_code=500,
        code=ErrorCode.STORAGE_ERROR,
        message="Internal storage error",
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(IntegrityError, pg_integrity_handler)
    app.add_exception_handler(DataError, pg_integrity_handler)
    app.add_exception_handler(OperationalError, pg_storage_handler)
