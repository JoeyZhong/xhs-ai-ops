from __future__ import annotations

import base64
import hashlib
import json
import uuid
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from fastapi.security import HTTPBearer

from agent_tools.idempotency import IdempotencyCache
from server.auth import AuthContext, verify_token


IDEMPOTENCY_HEADER = "Idempotency-Key"
WRITE_METHODS = frozenset({"POST", "PUT", "DELETE"})
HTTP_IDEMPOTENCY_TOOL = "http.idempotency"

_AUTH_BEARER = HTTPBearer(auto_error=False)
_CACHES: dict[str, IdempotencyCache] = {}
_SKIP_REPLAY_HEADERS = {
    "content-length",
    "transfer-encoding",
    "connection",
    "date",
    "server",
}


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    detail: str | None = None,
    field: str | None = None,
    current_rev: int | None = None,
) -> JSONResponse:
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


async def _auth_context(request: Request) -> AuthContext:
    credentials = await _AUTH_BEARER(request)
    auth = await verify_token(request, credentials)
    request.state.auth_context = auth
    return auth


def _get_cache(tenant_id: str) -> IdempotencyCache:
    cache = _CACHES.get(tenant_id)
    if cache is None:
        cache = IdempotencyCache(tenant_id)
        _CACHES[tenant_id] = cache
    return cache


def _canonical_body(body: bytes, content_type: str) -> Any:
    if not body:
        return None
    if "json" in content_type.lower():
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
    return {"base64": base64.b64encode(body).decode("ascii")}


def _json_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _payload_hash(request: Request, body: bytes, tenant_id: str) -> str:
    return _json_hash(
        {
            "method": request.method.upper(),
            "path": request.url.path,
            "query": sorted(request.query_params.multi_items()),
            "body": _canonical_body(
                body,
                request.headers.get("content-type", ""),
            ),
            "tenant_id": tenant_id,
        }
    )


def _storage_key(idempotency_key: str, tenant_id: str) -> str:
    return _json_hash(
        {
            "kind": "http-idempotency-v1",
            "idempotency_key": idempotency_key,
            "tenant_id": tenant_id,
        }
    )


def _serialize_response(response: Response) -> dict[str, Any] | None:
    body = getattr(response, "body", None)
    if body is None:
        return None
    headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower() not in _SKIP_REPLAY_HEADERS
    }
    return {
        "status_code": response.status_code,
        "headers": headers,
        "body_b64": base64.b64encode(body).decode("ascii"),
    }


def _replay_response(cached_response: dict[str, Any]) -> Response:
    return Response(
        content=base64.b64decode(cached_response.get("body_b64", "")),
        status_code=int(cached_response["status_code"]),
        headers=dict(cached_response.get("headers") or {}),
    )


class IdempotencyRoute(APIRoute):
    """Route-level Idempotency-Key wrapper for authenticated write endpoints."""

    def get_route_handler(self):
        original_route_handler = super().get_route_handler()

        async def idempotent_route_handler(request: Request) -> Response:
            if request.method.upper() not in WRITE_METHODS:
                return await original_route_handler(request)

            auth = await _auth_context(request)
            idempotency_key = request.headers.get(IDEMPOTENCY_HEADER, "").strip()
            if not idempotency_key:
                return _error_response(
                    status_code=428,
                    code="missing_idempotency_key",
                    message="Idempotency-Key header is required for write endpoints.",
                    field=IDEMPOTENCY_HEADER,
                )

            body = await request.body()
            payload_hash = _payload_hash(request, body, auth.tenant_id)
            cache_key = _storage_key(idempotency_key, auth.tenant_id)
            cache = _get_cache(auth.tenant_id)
            cached = cache.get(cache_key)

            if cached is not None:
                data = cached.get("data") or {}
                if data.get("payload_hash") != payload_hash:
                    return _error_response(
                        status_code=409,
                        code="idempotency_conflict",
                        message=(
                            "Idempotency-Key was reused with a different "
                            "request payload."
                        ),
                        field=IDEMPOTENCY_HEADER,
                    )
                return _replay_response(data["response"])

            response = await original_route_handler(request)
            if 200 <= response.status_code < 300:
                replayable_response = _serialize_response(response)
                if replayable_response is not None:
                    cache.set(
                        cache_key,
                        {
                            "ok": True,
                            "data": {
                                "payload_hash": payload_hash,
                                "response": replayable_response,
                            },
                        },
                        HTTP_IDEMPOTENCY_TOOL,
                    )
            return response

        return idempotent_route_handler


def clear_idempotency_caches_for_tests() -> None:
    for cache in _CACHES.values():
        cache.clear()
    _CACHES.clear()

