#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_content_lifecycle.py — 内容生命周期闭环验收（PRD §12 用例 1-5）

使用方式:
    python verify_content_lifecycle.py

退出码:
    0 = 全部通过
    1 = 有失败项

5 个用例:
  1. Topic → strategy → content → draft 落地
  2. Calendar item → content → calendar_item_id 回写
  3. 草稿 OCC 编辑冲突（409 + current_rev）
  4. 草稿复制（新 id + status=draft）
  5. Calendar 软删除（list 过滤）

约束:
  - TestClient + monkeypatch 后端（MockLifecycleBackend）
  - Kimi 调用全程 monkeypatch
  - 不依赖真 PG / 不依赖外网
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from typing import Any
from unittest.mock import patch

os.environ.setdefault("JWT_SECRET", "test_secret_for_verify_not_for_prod")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# Must happen after JWT env vars
from security.jwt import encode_token
from server.main import app
from server.middleware.idempotency import clear_idempotency_caches_for_tests
from storage.base import RevMismatch
import pandas as pd


# ── Results helpers (style aligned with verify_phase5_p0.py) ──────────────

_results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    mark = "[+]" if condition else "[X]"
    line = f"  {mark} {status}  {name}"
    if detail:
        line += f"  <- {detail}"
    print(line)
    _results.append((name, condition, detail))
    return condition


def section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'-'*60}")


def summary() -> bool:
    total = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = total - passed
    print(f"\n{'='*60}")
    print(f"  结果：{passed}/{total} 通过")
    if failed:
        print(f"  失败 {failed} 项")
        for name, ok, d in _results:
            if not ok:
                print(f"    [X] {name}" + (f": {d}" if d else ""))
    else:
        print("  全部通过")
    print('=' * 60)
    return failed == 0


# ── Mock kimi response ───────────────────────────────────────────────────

_MOCK_KIMI_RESPONSE = json.dumps([
    {
        "title": "测试标题：选址秘诀",
        "body": "在深圳做了4年自助售货机，管理235台设备。" * 30,
        "hashtags": ["选址", "自助售货机"],
        "publish_at": "12:00",
        "angle": "反直觉型",
    },
    {
        "title": "月入3万的真实记录",
        "body": "从1台到235台，我用4年时间证明了这件事。" * 30,
        "hashtags": ["收益", "创业"],
        "publish_at": "20:30",
        "angle": "数字清单型",
    },
    {
        "title": "学校点位谈判技巧",
        "body": "和学校谈自助售货机点位，这3个坑千万别踩。" * 30,
        "hashtags": ["谈判", "学校"],
        "publish_at": "12:00",
        "angle": "工具型",
    },
], ensure_ascii=False)


def _mock_kimi(prompt: str, system: str = "", **kwargs: Any) -> tuple[str, str | None]:
    """Stub for call_kimi — returns valid JSON array, never errors."""
    return _MOCK_KIMI_RESPONSE, None


# ── Auth constants ───────────────────────────────────────────────────────

TENANT_ID = "test-tenant"
TENANT_ID_B = "other-tenant"
JWT = encode_token(TENANT_ID)
JWT_B = encode_token(TENANT_ID_B)
HEADERS = {"Authorization": f"Bearer {JWT}"}
HEADERS_B = {"Authorization": f"Bearer {JWT_B}"}


# ── Mock backend ─────────────────────────────────────────────────────────

class MockLifecycleBackend:
    """Unified in-memory mock for all storage operations needed across 5 UCs."""

    def __init__(self) -> None:
        self.topics: dict[str, dict] = {}
        self.strategies: dict[str, dict] = {}
        self.calendar_items: dict[str, dict] = {}
        self.posts: dict[str, dict] = {}

    def clear(self) -> None:
        """Reset all in-memory state — call between use cases."""
        self.topics.clear()
        self.strategies.clear()
        self.calendar_items.clear()
        self.posts.clear()

    def _ts(self) -> str:
        return "2026-05-27T00:00:00Z"

    # ── Topics ──────────────────────────────────────────────────────────

    def create_topic(
        self,
        tenant_id: str,
        *,
        title: str,
        goal_id: str | None = None,
        persona_id: str | None = None,
        angle: str | None = None,
        funnel_stage: str | None = None,
        source: str = "manual",
        source_refs: list[dict] | None = None,
    ) -> dict:
        tid = f"topic_{uuid.uuid4().hex[:8]}"
        rec: dict[str, Any] = {
            "topic_id": tid, "tenant_id": tenant_id, "goal_id": goal_id,
            "persona_id": persona_id, "title": title, "angle": angle,
            "funnel_stage": funnel_stage, "source": source,
            "source_refs": source_refs or [], "status": "idea",
            "created_by": "user", "rev": 1,
            "created_at": self._ts(), "updated_at": self._ts(),
        }
        self.topics[tid] = rec
        return rec

    def get_topic(self, tenant_id: str, topic_id: str) -> dict:
        rec = self.topics.get(topic_id)
        if rec is None or rec["tenant_id"] != tenant_id:
            raise KeyError(topic_id)
        return rec

    def list_topics(
        self,
        tenant_id: str,
        *,
        goal_id: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
        sort: str = "-updated_at",
    ) -> dict:
        items = [t for t in self.topics.values() if t["tenant_id"] == tenant_id]
        if goal_id:
            items = [t for t in items if t.get("goal_id") == goal_id]
        if status:
            items = [t for t in items if t.get("status") == status]
        total = len(items)
        start = (page - 1) * page_size
        return {
            "items": items[start: start + page_size], "total": total,
            "page": page, "page_size": page_size,
            "has_more": (start + page_size < total),
        }

    def update_topic(
        self,
        tenant_id: str,
        topic_id: str,
        *,
        expected_rev: int,
        **changes: Any,
    ) -> dict:
        rec = self.topics.get(topic_id)
        if rec is None or rec["tenant_id"] != tenant_id:
            raise KeyError(topic_id)
        if rec["rev"] != expected_rev:
            raise RevMismatch()
        rec.update(changes)
        rec["rev"] += 1
        rec["updated_at"] = self._ts()
        return rec

    def delete_topic(self, tenant_id: str, topic_id: str, expected_rev: int) -> dict:
        rec = self.topics.get(topic_id)
        if rec is None or rec["tenant_id"] != tenant_id:
            raise KeyError(topic_id)
        if rec["rev"] != expected_rev:
            raise RevMismatch()
        rec["status"] = "archived"
        rec["rev"] += 1
        rec["updated_at"] = self._ts()
        return rec

    # ── Strategies ──────────────────────────────────────────────────────

    def create_strategy(
        self,
        tenant_id: str,
        *,
        topic_id: str | None = None,
        manual_input_hint: str | None = None,
        target_reader: str | None = None,
        funnel_stage: str | None = None,
        angle: str | None = None,
        hook: str | None = None,
        key_points: list[dict] | None = None,
        cta: str | None = None,
        avoid_points: list[dict] | None = None,
        evidence_refs: list[dict] | None = None,
        memory_refs: list[dict] | None = None,
        knowledge_refs: list[dict] | None = None,
    ) -> dict:
        sid = f"strat_{uuid.uuid4().hex[:8]}"
        rec: dict[str, Any] = {
            "strategy_id": sid, "tenant_id": tenant_id, "topic_id": topic_id,
            "manual_input_hint": manual_input_hint, "target_reader": target_reader,
            "funnel_stage": funnel_stage, "angle": angle, "hook": hook,
            "key_points": key_points or [], "cta": cta,
            "avoid_points": avoid_points or [], "evidence_refs": evidence_refs or [],
            "memory_refs": memory_refs or [], "knowledge_refs": knowledge_refs or [],
            "created_by": "user", "rev": 1, "created_at": self._ts(),
            "updated_at": self._ts(),
        }
        self.strategies[sid] = rec
        return rec

    def get_strategy(self, tenant_id: str, strategy_id: str) -> dict:
        rec = self.strategies.get(strategy_id)
        if rec is None or rec["tenant_id"] != tenant_id:
            raise KeyError(strategy_id)
        return rec

    def list_strategies(
        self,
        tenant_id: str,
        *,
        topic_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
        sort: str = "-created_at",
    ) -> dict:
        items = [s for s in self.strategies.values() if s["tenant_id"] == tenant_id]
        if topic_id:
            items = [s for s in items if s.get("topic_id") == topic_id]
        total = len(items)
        start = (page - 1) * page_size
        return {
            "items": items[start: start + page_size], "total": total,
            "page": page, "page_size": page_size,
            "has_more": (start + page_size < total),
        }

    def update_strategy(
        self,
        tenant_id: str,
        strategy_id: str,
        *,
        expected_rev: int,
        **changes: Any,
    ) -> dict:
        rec = self.strategies.get(strategy_id)
        if rec is None or rec["tenant_id"] != tenant_id:
            raise KeyError(strategy_id)
        if rec["rev"] != expected_rev:
            raise RevMismatch()
        rec.update(changes)
        rec["rev"] += 1
        rec["updated_at"] = self._ts()
        return rec

    def delete_strategy(self, tenant_id: str, strategy_id: str) -> None:
        rec = self.strategies.get(strategy_id)
        if rec is None or rec["tenant_id"] != tenant_id:
            raise KeyError(strategy_id)
        del self.strategies[strategy_id]

    # ── Calendar ────────────────────────────────────────────────────────

    def create_calendar_item(
        self,
        tenant_id: str,
        *,
        scheduled_date: str,
        scheduled_time: str | None = None,
        topic_id: str | None = None,
        funnel_stage: str | None = None,
        content_id: str | None = None,
    ) -> dict:
        cid = f"cal_{uuid.uuid4().hex[:8]}"
        rec: dict[str, Any] = {
            "calendar_item_id": cid, "tenant_id": tenant_id, "topic_id": topic_id,
            "content_id": content_id, "scheduled_date": scheduled_date,
            "scheduled_time": scheduled_time, "funnel_stage": funnel_stage,
            "status": "planned", "delete_mode": "soft", "deleted_at": None,
            "created_by": "user", "rev": 1, "created_at": self._ts(),
            "updated_at": self._ts(),
        }
        self.calendar_items[cid] = rec
        return rec

    def get_calendar_item(self, tenant_id: str, cal_id: str) -> dict:
        rec = self.calendar_items.get(cal_id)
        if rec is None or rec["tenant_id"] != tenant_id:
            raise KeyError(cal_id)
        return rec

    def list_calendar_items(
        self,
        tenant_id: str,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        status: str | None = None,
        include_deleted: bool = False,
        page: int = 1,
        page_size: int = 20,
        sort: str = "scheduled_date",
    ) -> dict:
        items = [i for i in self.calendar_items.values() if i["tenant_id"] == tenant_id]
        if not include_deleted:
            items = [i for i in items if i.get("deleted_at") is None]
        if date_from:
            items = [i for i in items if i.get("scheduled_date", "") >= date_from]
        if date_to:
            items = [i for i in items if i.get("scheduled_date", "") <= date_to]
        if status:
            items = [i for i in items if i.get("status") == status]
        total = len(items)
        start = (page - 1) * page_size
        return {
            "items": items[start: start + page_size], "total": total,
            "page": page, "page_size": page_size,
            "has_more": (start + page_size < total),
        }

    def update_calendar_item(
        self,
        tenant_id: str,
        cal_id: str,
        *,
        expected_rev: int,
        **changes: Any,
    ) -> dict:
        rec = self.calendar_items.get(cal_id)
        if rec is None or rec["tenant_id"] != tenant_id:
            raise KeyError(cal_id)
        if rec["rev"] != expected_rev:
            raise RevMismatch()
        rec.update(changes)
        rec["rev"] += 1
        rec["updated_at"] = self._ts()
        return rec

    def delete_calendar_item(
        self,
        tenant_id: str,
        cal_id: str,
        expected_rev: int,
        *,
        mode: str = "soft",
    ) -> dict:
        rec = self.calendar_items.get(cal_id)
        if rec is None or rec["tenant_id"] != tenant_id:
            raise KeyError(cal_id)
        if rec["rev"] != expected_rev:
            raise RevMismatch()
        if mode == "hard":
            del self.calendar_items[cal_id]
            return {"deleted": True}
        rec["status"] = "cancelled"
        rec["deleted_at"] = self._ts()
        rec["rev"] += 1
        rec["updated_at"] = self._ts()
        return {
            "calendar_item_id": cal_id, "status": "cancelled",
            "deleted_at": rec["deleted_at"], "rev": rec["rev"],
        }

    # ── Generated posts (drafts) ────────────────────────────────────────

    def save_generated_posts(
        self, tenant_id: str, df: pd.DataFrame, meta: dict | None = None
    ) -> str:
        for _, row in df.iterrows():
            rec = row.to_dict()
            cid = rec.get("content_id")
            if cid:
                if "rev" not in rec or rec["rev"] is None:
                    rec["rev"] = 1
                self.posts[cid] = rec
        return "mock_save_path"

    def get_generated_post(self, tenant_id: str, content_id: str) -> dict | None:
        rec = self.posts.get(content_id)
        if rec is None:
            return None
        return rec

    def update_generated_post(
        self,
        tenant_id: str,
        content_id: str,
        *,
        expected_rev: int,
        **changes: Any,
    ) -> dict:
        rec = self.posts.get(content_id)
        if rec is None:
            raise KeyError(content_id)
        cur_rev = rec.get("rev", 1)
        if cur_rev != expected_rev:
            raise RevMismatch()
        rec.update(changes)
        rec["rev"] = cur_rev + 1
        rec["updated_at"] = self._ts()
        return rec

    def list_generated_posts(
        self,
        tenant_id: str,
        *,
        since=None,
        topic_id: str | None = None,
        strategy_id: str | None = None,
        calendar_item_id: str | None = None,
        status: str | None = None,
    ) -> pd.DataFrame:
        items = list(self.posts.values())
        if topic_id:
            items = [p for p in items if p.get("topic_id") == topic_id]
        if strategy_id:
            items = [p for p in items if p.get("strategy_id") == strategy_id]
        if calendar_item_id:
            items = [p for p in items if p.get("calendar_item_id") == calendar_item_id]
        if status:
            items = [p for p in items if p.get("status") == status]
        cols = [
            "content_id", "goal_id", "title", "body", "hashtags", "publish_at",
            "status", "meta", "created_at", "updated_at", "topic_id",
            "strategy_id", "calendar_item_id", "knowledge_refs", "memory_refs", "rev",
        ]
        records = [{c: p.get(c) for c in cols} for p in items]
        return pd.DataFrame(records)

    # ── Goals (needed by content/generate) ──────────────────────────────

    def load_goals(self, tenant_id: str) -> dict:
        return {
            "goals": [
                {
                    "id": "goal_001",
                    "title": "测试运营目标",
                    "brand_position": "深圳自助售卖机运营商",
                    "target_audience": {
                        "who": "工厂/写字楼/学校",
                        "pain_points": "点位难找",
                    },
                    "keywords": ["自助机", "点位", "招商"],
                }
            ]
        }

    def save_goals(self, tenant_id: str, data: dict) -> None:
        pass


# ── Fixture helpers ───────────────────────────────────────────────────────


def _ik(suffix: str = "") -> str:
    """Unique idempotency key per call."""
    return f"vfy-{suffix or uuid.uuid4().hex}"


# ── HTTP client ──────────────────────────────────────────────────────────

from httpx import ASGITransport, AsyncClient

_transport = ASGITransport(app=app)


async def _ac() -> AsyncClient:
    return AsyncClient(transport=_transport, base_url="http://test")


# ══════════════════════════════════════════════════════════════════════════
#  用例 1: Topic → Strategy → Content → Draft
# ══════════════════════════════════════════════════════════════════════════

async def run_use_case_1(backend: MockLifecycleBackend) -> None:
    section("用例 1: Topic → Strategy → Content → Draft 落地")

    backend.clear()
    clear_idempotency_caches_for_tests()

    # 1.1 Create a topic
    ik = _ik("uc1-topic")
    async with await _ac() as ac:
        resp = await ac.post(
            "/api/v1/topics",
            json={"title": "选址技巧"},
            headers={**HEADERS, "Idempotency-Key": ik},
        )
    check("1.1 POST /topics → 201", resp.status_code == 201, f"got {resp.status_code}")
    topic = resp.json()
    topic_id = topic["topic_id"]
    check("1.1a topic_id 不为空", bool(topic_id))
    check("1.1b rev=1", topic.get("rev") == 1, f"got {topic.get('rev')}")

    # 1.2 Create a strategy linked to the topic
    ik = _ik("uc1-strat")
    async with await _ac() as ac:
        resp = await ac.post(
            "/api/v1/strategies",
            json={
                "topic_id": topic_id,
                "target_reader": "工厂业主",
                "angle": "反直觉型",
                "hook": "90%的人都搞错了选址重点",
            },
            headers={**HEADERS, "Idempotency-Key": ik},
        )
    check("1.2 POST /strategies → 201", resp.status_code == 201, f"got {resp.status_code}")
    strat = resp.json()
    strategy_id = strat["strategy_id"]
    check("1.2a strategy_id 不为空", bool(strategy_id))
    check("1.2b topic_id 关联正确", strat.get("topic_id") == topic_id,
          f"got {strat.get('topic_id')}")

    # 1.3 Generate content with topic_id + strategy_id lifecycle refs
    ik = _ik("uc1-gen")
    async with await _ac() as ac:
        resp = await ac.post(
            "/api/v1/content/generate",
            json={
                "goal_id": "goal_001",
                "topic": "选址技巧",
                "count": 2,
                "persist": True,
                "topic_id": topic_id,
                "strategy_id": strategy_id,
            },
            headers={**HEADERS, "Idempotency-Key": ik},
        )
    check("1.3 POST /content/generate → 200", resp.status_code == 200,
          f"got {resp.status_code}")
    gen = resp.json()
    check("1.3a items 非空", len(gen.get("items", [])) > 0,
          f"count={len(gen.get('items', []))}")
    content_id = gen["items"][0]["content_id"]
    check("1.3b content_id 不为空", bool(content_id))

    # 1.4 Fetch the draft and verify lifecycle refs
    async with await _ac() as ac:
        resp = await ac.get(f"/api/v1/drafts/{content_id}", headers=HEADERS)
    check("1.4 GET /drafts/{id} → 200", resp.status_code == 200,
          f"got {resp.status_code}")
    draft = resp.json()
    check("1.4a topic_id matches", draft.get("topic_id") == topic_id,
          f"got {draft.get('topic_id')}")
    check("1.4b strategy_id matches", draft.get("strategy_id") == strategy_id,
          f"got {draft.get('strategy_id')}")
    check("1.4c status='draft'", draft.get("status") == "draft",
          f"got {draft.get('status')}")


# ══════════════════════════════════════════════════════════════════════════
#  用例 2: Calendar → Content → calendar_item_id 回写
# ══════════════════════════════════════════════════════════════════════════

async def run_use_case_2(backend: MockLifecycleBackend) -> None:
    section("用例 2: Calendar → Content → calendar_item_id 回写")

    backend.clear()
    clear_idempotency_caches_for_tests()

    # 2.1 Create a calendar item
    ik = _ik("uc2-cal")
    async with await _ac() as ac:
        resp = await ac.post(
            "/api/v1/calendar",
            json={"scheduled_date": "2026-06-15", "funnel_stage": "traffic"},
            headers={**HEADERS, "Idempotency-Key": ik},
        )
    check("2.1 POST /calendar → 201", resp.status_code == 201, f"got {resp.status_code}")
    cal = resp.json()
    cal_id = cal["calendar_item_id"]
    check("2.1a calendar_item_id 不为空", bool(cal_id))
    check("2.1b rev=1", cal.get("rev") == 1, f"got {cal.get('rev')}")

    # 2.2 Generate content with calendar_item_id
    ik = _ik("uc2-gen")
    async with await _ac() as ac:
        resp = await ac.post(
            "/api/v1/content/generate",
            json={
                "goal_id": "goal_001",
                "topic": "深圳选址",
                "count": 1,
                "persist": True,
                "calendar_item_id": cal_id,
            },
            headers={**HEADERS, "Idempotency-Key": ik},
        )
    check("2.2 POST /content/generate → 200", resp.status_code == 200,
          f"got {resp.status_code}")
    gen = resp.json()
    check("2.2a items 非空", len(gen.get("items", [])) > 0)
    content_id = gen["items"][0]["content_id"]

    # 2.3 Verify draft has calendar_item_id set
    async with await _ac() as ac:
        resp = await ac.get(f"/api/v1/drafts/{content_id}", headers=HEADERS)
    check("2.3 GET /drafts/{id} → 200", resp.status_code == 200,
          f"got {resp.status_code}")
    draft = resp.json()
    check("2.3a calendar_item_id matches", draft.get("calendar_item_id") == cal_id,
          f"got {draft.get('calendar_item_id')}")


# ══════════════════════════════════════════════════════════════════════════
#  用例 3: 草稿 OCC 编辑冲突
# ══════════════════════════════════════════════════════════════════════════

async def run_use_case_3(backend: MockLifecycleBackend) -> None:
    section("用例 3: 草稿 OCC 编辑冲突（409 + current_rev）")

    backend.clear()
    clear_idempotency_caches_for_tests()

    # 3.0 Seed a draft via content generation
    ik = _ik("uc3-seed")
    async with await _ac() as ac:
        resp = await ac.post(
            "/api/v1/content/generate",
            json={"goal_id": "goal_001", "topic": "OCC测试", "count": 1, "persist": True},
            headers={**HEADERS, "Idempotency-Key": ik},
        )
    assert resp.status_code == 200
    content_id = resp.json()["items"][0]["content_id"]

    # 3.1 First PUT with correct rev → 200
    ik = _ik("uc3-put1")
    async with await _ac() as ac:
        resp = await ac.put(
            f"/api/v1/drafts/{content_id}",
            json={"title": "修改标题", "rev": 1},
            headers={**HEADERS, "Idempotency-Key": ik},
        )
    check("3.1 PUT /drafts/{id} rev=1 → 200", resp.status_code == 200,
          f"got {resp.status_code}")
    updated = resp.json()
    check("3.1a title 已更新", updated.get("title") == "修改标题",
          f"got {updated.get('title')}")
    check("3.1b rev 自增到 2", updated.get("rev") == 2, f"got {updated.get('rev')}")

    # 3.2 Second PUT with stale rev → 409 + current_rev
    ik = _ik("uc3-put2")
    async with await _ac() as ac:
        resp = await ac.put(
            f"/api/v1/drafts/{content_id}",
            json={"title": "冲突标题", "rev": 1},  # stale — current is 2
            headers={**HEADERS, "Idempotency-Key": ik},
        )
    check("3.2 PUT /drafts/{id} stale rev=1 → 409", resp.status_code == 409,
          f"got {resp.status_code}")
    err = resp.json().get("error", {})
    check("3.2a error.code == rev_mismatch", err.get("code") == "rev_mismatch",
          f"got {err.get('code')}")
    check("3.2b current_rev == 2", err.get("current_rev") == 2,
          f"got {err.get('current_rev')}")


# ══════════════════════════════════════════════════════════════════════════
#  用例 4: 草稿复制
# ══════════════════════════════════════════════════════════════════════════

async def run_use_case_4(backend: MockLifecycleBackend) -> None:
    section("用例 4: 草稿复制（新 id + status=draft）")

    backend.clear()
    clear_idempotency_caches_for_tests()

    # 4.0 Seed a draft
    ik = _ik("uc4-seed")
    async with await _ac() as ac:
        resp = await ac.post(
            "/api/v1/content/generate",
            json={"goal_id": "goal_001", "topic": "复制测试", "count": 1, "persist": True},
            headers={**HEADERS, "Idempotency-Key": ik},
        )
    assert resp.status_code == 200
    orig_id = resp.json()["items"][0]["content_id"]

    # 4.1 Duplicate the draft
    ik = _ik("uc4-dup")
    async with await _ac() as ac:
        resp = await ac.post(
            f"/api/v1/drafts/{orig_id}/duplicate",
            json={"title_suffix": "（副本）"},
            headers={**HEADERS, "Idempotency-Key": ik},
        )
    check("4.1 POST /drafts/{id}/duplicate → 201", resp.status_code == 201,
          f"got {resp.status_code}")
    dup = resp.json()
    dup_id = dup.get("content_id")
    check("4.1a 新 content_id 不为空", bool(dup_id))
    check("4.1b 新 id ≠ 原 id", dup_id != orig_id,
          f"dup_id={dup_id} orig_id={orig_id}")
    check("4.1c status=draft", dup.get("status") == "draft",
          f"got {dup.get('status')}")

    # 4.2 Original draft is still accessible
    async with await _ac() as ac:
        resp = await ac.get(f"/api/v1/drafts/{orig_id}", headers=HEADERS)
    check("4.2 原草稿仍可访问", resp.status_code == 200, f"got {resp.status_code}")


# ══════════════════════════════════════════════════════════════════════════
#  用例 5: Calendar 软删除
# ══════════════════════════════════════════════════════════════════════════

async def run_use_case_5(backend: MockLifecycleBackend) -> None:
    section("用例 5: Calendar 软删除（list 过滤）")

    backend.clear()
    clear_idempotency_caches_for_tests()

    # 5.1 Create a calendar item
    ik = _ik("uc5-cal")
    async with await _ac() as ac:
        resp = await ac.post(
            "/api/v1/calendar",
            json={"scheduled_date": "2026-07-01", "funnel_stage": "conversion"},
            headers={**HEADERS, "Idempotency-Key": ik},
        )
    check("5.1 POST /calendar → 201", resp.status_code == 201, f"got {resp.status_code}")
    cal = resp.json()
    cal_id = cal["calendar_item_id"]

    # 5.2 Soft delete
    ik = _ik("uc5-del")
    async with await _ac() as ac:
        resp = await ac.delete(
            f"/api/v1/calendar/{cal_id}?rev=1&mode=soft",
            headers={**HEADERS, "Idempotency-Key": ik},
        )
    check("5.2 DELETE /calendar/{id} soft → 200", resp.status_code == 200,
          f"got {resp.status_code}")
    del_resp = resp.json()
    check("5.2a status=cancelled", del_resp.get("status") == "cancelled",
          f"got {del_resp.get('status')}")

    # 5.3 Default list — should NOT include the deleted item
    async with await _ac() as ac:
        resp = await ac.get("/api/v1/calendar", headers=HEADERS)
    body_5_3 = resp.json()
    check("5.3 GET /calendar default → total=0 (deleted hidden)",
          body_5_3.get("total") == 0, f"total={body_5_3.get('total')}")

    # 5.4 Detail — should still be accessible with status=cancelled
    async with await _ac() as ac:
        resp = await ac.get(f"/api/v1/calendar/{cal_id}", headers=HEADERS)
    check("5.4 GET /calendar/{id} detail → 200", resp.status_code == 200,
          f"got {resp.status_code}")
    detail = resp.json()
    check("5.4a status=cancelled in detail", detail.get("status") == "cancelled",
          f"got {detail.get('status')}")

    # 5.5 List with include_deleted=true — item reappears
    async with await _ac() as ac:
        resp = await ac.get("/api/v1/calendar?include_deleted=true", headers=HEADERS)
    body_5_5 = resp.json()
    check("5.5 GET /calendar?include_deleted=true → total=1",
          body_5_5.get("total") == 1, f"total={body_5_5.get('total')}")


# ══════════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════════

async def main() -> bool:
    print("=" * 60)
    print("  verify_content_lifecycle.py — 内容生命周期闭环验收")
    print("  5 个用例, PRD §12 用例 1-5")
    print("=" * 60)

    # Wire mock backend into storage factory
    import storage.factory as sf

    backend = MockLifecycleBackend()
    sf.get_backend = lambda: backend  # type: ignore[method-assign]

    # Patch call_kimi so content generation uses mock response
    import agent_tools.kimi as kimi_mod

    with patch.object(kimi_mod, "call_kimi", side_effect=_mock_kimi):
        await run_use_case_1(backend)
        await run_use_case_2(backend)
        await run_use_case_3(backend)
        await run_use_case_4(backend)
        await run_use_case_5(backend)

    return summary()


if __name__ == "__main__":
    import asyncio

    success = asyncio.run(main())
    sys.exit(0 if success else 1)
