"""
Tests for sync_collect_worker — behaviour through public interface only.

Each test runs the worker in an executor (matching production topology) and
reads from the asyncio.Queue until "done".  No threading.Event, no timers,
no internal assertions.
"""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from server.stream_utils import sync_collect_worker
from storage.local_json import LocalJsonBackend


# ── helpers ───────────────────────────────────────────────────────────────────

def _note(note_id: str, title: str) -> dict:
    return {
        "id": note_id,
        "note_card": {
            "display_title": title,
            "type": "normal",
            "user": {"nick_name": "u1", "user_id": "uid1"},
            "interact_info": {
                "liked_count": "10",
                "collected_count": "5",
                "comment_count": "2",
                "shared_count": "1",
            },
            "cover": {"url_default": ""},
            "corner_tag_info": [],
        },
    }


async def _drain(queue: asyncio.Queue, timeout: float = 5.0) -> list[dict]:
    """Read from queue until a 'done' message arrives."""
    msgs: list[dict] = []
    async with asyncio.timeout(timeout):
        while True:
            msg = await queue.get()
            msgs.append(msg)
            if msg["type"] == "done":
                break
    return msgs


async def _run(keywords, queue, stop_event=None, account_id="default", **patches):
    """Run sync_collect_worker in an executor while draining the queue concurrently."""
    loop = asyncio.get_running_loop()
    if stop_event is None:
        stop_event = threading.Event()

    async def _worker():
        await loop.run_in_executor(
            None,
            lambda: sync_collect_worker(keywords, queue, loop, account_id, stop_event),
        )

    _, messages = await asyncio.gather(_worker(), _drain(queue))
    return messages


# ── Slice 1 · happy path ──────────────────────────────────────────────────────

async def test_one_progress_event_per_note(tmp_path, monkeypatch):
    """Worker emits exactly one progress event per collected note."""
    monkeypatch.setattr("storage.factory.get_backend",
                        lambda: LocalJsonBackend(base_dir=str(tmp_path)))
    queue: asyncio.Queue = asyncio.Queue()

    with (
        patch("server.stream_utils.get_cookie", return_value="ck"),
        patch("server.stream_utils.XHS_Apis") as MockApi,
    ):
        MockApi.return_value.search_some_note.return_value = (
            True, "ok", [_note("n1", "T1"), _note("n2", "T2")]
        )
        messages = await _run(["kw1"], queue)

    progress = [m for m in messages if m["type"] == "progress"]
    assert len(progress) == 2
    assert progress[0]["data"]["笔记ID"] == "n1"
    assert progress[1]["data"]["笔记ID"] == "n2"


async def test_done_event_carries_count_and_excel_is_saved(tmp_path, monkeypatch):
    """Done event has correct count and Excel file is written to disk."""
    monkeypatch.setattr("storage.factory.get_backend",
                        lambda: LocalJsonBackend(base_dir=str(tmp_path)))
    queue: asyncio.Queue = asyncio.Queue()

    with (
        patch("server.stream_utils.get_cookie", return_value="ck"),
        patch("server.stream_utils.XHS_Apis") as MockApi,
    ):
        MockApi.return_value.search_some_note.return_value = (
            True, "ok", [_note("n1", "T1"), _note("n2", "T2")]
        )
        messages = await _run(["kw1"], queue)

    done = next(m for m in messages if m["type"] == "done")
    assert done["count"] == 2
    assert done["saved"] is not None
    assert Path(done["saved"]).exists()
    assert Path(done["saved"]).suffix == ".xlsx"


# ── Slice 2 · API fallback ────────────────────────────────────────────────────

async def test_fallback_event_emitted_when_api_fails(tmp_path, monkeypatch):
    """When API returns failure, worker emits fallback event and uses browser."""
    monkeypatch.setattr("storage.factory.get_backend",
                        lambda: LocalJsonBackend(base_dir=str(tmp_path)))
    queue: asyncio.Queue = asyncio.Queue()

    with (
        patch("server.stream_utils.get_cookie", return_value="ck"),
        patch("server.stream_utils.XHS_Apis") as MockApi,
        patch("server.stream_utils.search_notes") as mock_browser,
    ):
        MockApi.return_value.search_some_note.return_value = (False, "rate limited", [])
        mock_browser.return_value = (True, "ok", [_note("n1", "T1")], None)
        messages = await _run(["kw1"], queue)

    types = [m["type"] for m in messages]
    assert "fallback" in types
    # browser fallback succeeded → 1 progress event
    assert len([m for m in messages if m["type"] == "progress"]) == 1


# ── Slice 3 · stop_event ─────────────────────────────────────────────────────

async def test_stop_event_halts_after_first_keyword_and_saves_partial(tmp_path, monkeypatch):
    """Pre-set stop_event stops loop between keywords; partial data still saved."""
    monkeypatch.setattr("storage.factory.get_backend",
                        lambda: LocalJsonBackend(base_dir=str(tmp_path)))
    queue: asyncio.Queue = asyncio.Queue()
    stop_event = threading.Event()
    stop_event.set()  # already set → wait() returns True immediately

    with (
        patch("server.stream_utils.get_cookie", return_value="ck"),
        patch("server.stream_utils.XHS_Apis") as MockApi,
    ):
        MockApi.return_value.search_some_note.side_effect = [
            (True, "ok", [_note("n1", "T1"), _note("n2", "T2")]),  # kw1
            (True, "ok", [_note("n3", "T3")]),                      # kw2 — never reached
        ]
        messages = await _run(["kw1", "kw2"], queue, stop_event=stop_event)

    note_ids = {m["data"]["笔记ID"] for m in messages if m["type"] == "progress"}
    assert "n1" in note_ids
    assert "n2" in note_ids
    assert "n3" not in note_ids  # kw2 was skipped

    done = next(m for m in messages if m["type"] == "done")
    assert done["count"] == 2
    assert Path(done["saved"]).exists()
