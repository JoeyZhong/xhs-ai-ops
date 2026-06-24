"""
Tests for POST /api/v1/collect/stream endpoint.

The sync worker is replaced with a fake that puts events directly into the
queue via call_soon_threadsafe, mirroring production topology without hitting
the real XHS API.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("JWT_SECRET", "test_secret_for_pytest_only_not_for_prod")
os.environ.setdefault("JWT_ALGORITHM", "HS256")

from security.jwt import encode_token
from server.main import app

TEST_JWT = encode_token("test-tenant", is_admin=False)
JWT_HEADER = {"Authorization": f"Bearer {TEST_JWT}"}


def _make_worker(*events: dict):
    """Return a sync worker function that emits the given events then stops."""
    def fake_worker(keywords, queue, loop, account_id="default", stop_event=None, **kwargs):
        for event in events:
            loop.call_soon_threadsafe(queue.put_nowait, event)
    return fake_worker


# ── Slice 4 · smoke ───────────────────────────────────────────────────────────

async def test_collect_stream_returns_event_stream_content_type(monkeypatch):
    """Endpoint responds 200 with text/event-stream content-type."""
    monkeypatch.setattr(
        "server.main.sync_collect_worker",
        _make_worker({"type": "done", "count": 0, "saved": None}),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        async with ac.stream("POST", "/api/v1/collect/stream", json={"keywords": ["kw1"]}, headers=JWT_HEADER) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            # consume stream to let generator finish cleanly
            async for raw in resp.aiter_lines():
                if "done" in raw:
                    break


# ── Slice 5 · streaming events ────────────────────────────────────────────────

async def test_collect_stream_forwards_worker_events(monkeypatch):
    """Endpoint streams progress and done events from the worker as SSE data lines."""
    fake_events = [
        {"type": "progress", "msg": "note 1", "data": {"笔记ID": "n1"}},
        {"type": "done", "count": 1, "saved": None},
    ]
    monkeypatch.setattr("server.main.sync_collect_worker", _make_worker(*fake_events))

    received: list[dict] = []
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        async with ac.stream("POST", "/api/v1/collect/stream", json={"keywords": ["kw1"]}, headers=JWT_HEADER) as resp:
            async for raw in resp.aiter_lines():
                if raw.startswith("data:"):
                    received.append(json.loads(raw[len("data:"):].strip()))
                    if received[-1].get("type") == "done":
                        break

    types = [m["type"] for m in received]
    assert "progress" in types
    assert types[-1] == "done"
    assert received[-1]["count"] == 1
