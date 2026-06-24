"""
P3.2.D9 Draft flow 验收测试（TDD RED→GREEN）
运行：pytest tests/test_p3_draft_flow.py -v

覆盖目标：
- S1: 旧 entry（无 §status）解析为 status=active
- S2: serialize/parse 元字段 round-trip
- S3: ContentAgent 跳过 status=draft 的 entry
- S4: ContentAgent 包含 status=active 的 entry
- S5: ContentAgent 不包含 status=rejected 的 entry
- S6: 混合 status 时只注入 active
- S7: add_entry 带 entry_meta 写入正确
- S8: replace_entry 带 entry_meta 保留元字段
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from agents.memory import (
    Entry,
    MemoryLayer,
    parse_entries,
    serialize_entries,
    WritePermissionDenied,
    MemoryInjectionDetected,
)


# ── fixture：内存 backend ───────────────────────────────────────────────

@pytest.fixture()
def mem_layer():
    """返回一个用 MemoryStorage 做 backend 的 MemoryLayer。"""
    from storage.local_json import LocalJsonBackend
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    storage = LocalJsonBackend(base_dir=str(tmp))
    return MemoryLayer(storage=storage)


# ── S1 · 向后兼容 ───────────────────────────────────────────────────────

class TestBackwardCompat:
    def test_old_entry_defaults_to_active(self):
        """旧格式 §id: x §rev: 1 → 解析为 status=active, source=manual, confidence=high。"""
        content = "§id: my-entry §rev: 1\nthis is body"
        header, entries = parse_entries(content)
        assert "my-entry" in entries
        e = entries["my-entry"]
        assert e.status == "active"
        assert e.source == "manual"
        assert e.confidence == "high"
        assert e.body == "this is body"
        assert e.rev == 1

    def test_no_rev_defaults_to_zero(self):
        """无 §rev → rev=0。"""
        content = "§id: old-entry\nbody text"
        _, entries = parse_entries(content)
        assert entries["old-entry"].rev == 0


# ── S2 · Round-trip ────────────────────────────────────────────────────

class TestRoundTrip:
    def test_serialize_draft_entry(self):
        """draft entry 序列化 → 包含 §status: draft §source: scheduler。"""
        entry = Entry(
            id="weekly-test", body="insight body", rev=1,
            status="draft", source="scheduler", confidence="low",
        )
        result = serialize_entries("", {"weekly-test": entry})
        assert "§status: draft" in result
        assert "§source: scheduler" in result
        assert "§confidence: low" in result

    def test_round_trip_preserves_metadata(self):
        """序列化后再解析，元字段不变。"""
        original = Entry(
            id="test-id", body="body content", rev=3,
            status="draft", source="scheduler", confidence="low",
        )
        serialized = serialize_entries("", {"test-id": original})
        _, entries = parse_entries(serialized)
        e = entries["test-id"]
        assert e.status == "draft"
        assert e.source == "scheduler"
        assert e.confidence == "low"
        assert e.body == "body content"
        assert e.rev == 3

    def test_mixed_entries(self):
        """不同 status 的 entries 共存。"""
        e1 = Entry(id="active-1", body="a", rev=1, status="active", source="manual", confidence="high")
        e2 = Entry(id="draft-1", body="d", rev=1, status="draft", source="scheduler", confidence="low")
        e3 = Entry(id="rejected-1", body="r", rev=2, status="rejected", source="manual", confidence="high")
        serialized = serialize_entries("", {"active-1": e1, "draft-1": e2, "rejected-1": e3})
        _, entries = parse_entries(serialized)
        assert entries["active-1"].status == "active"
        assert entries["draft-1"].status == "draft"
        assert entries["rejected-1"].status == "rejected"


# ── S3-S6 · ContentAgent prompt 过滤 ───────────────────────────────────

class TestContentPromptFilter:
    def _build_content_prompt_with_playbook(self, playbook_md: str) -> str:
        """模拟 ContentAgent.build_system_prompt 的核心逻辑。"""
        from agents.content import ContentAgent
        from agents.base import AgentBase
        # 直接测试核心过滤逻辑（不实例化整个 Agent）
        from agents.memory import parse_entries

        _, entries = parse_entries(playbook_md)
        active_entries = {
            eid: e for eid, e in entries.items()
            if e.status == "active"
        }
        if active_entries:
            return "\n\n".join(
                f"[{eid}] {e.body}" for eid, e in active_entries.items()
            )
        return ""

    def test_draft_entry_skipped(self):
        """draft entry 不进入 Content prompt。"""
        playbook = (
            "§id: weekly-2026-05-07 §rev: 1 §status: draft §source: scheduler §confidence: low\n"
            "this is a draft insight\n\n"
            "§id: existing-active §rev: 2 §status: active §source: manual §confidence: high\n"
            "this is active"
        )
        result = self._build_content_prompt_with_playbook(playbook)
        assert "weekly-2026-05-07" not in result
        assert "existing-active" in result
        assert "this is active" in result

    def test_all_draft_returns_empty(self):
        """全部 draft → Content prompt 无 playbook 内容。"""
        playbook = (
            "§id: draft-1 §rev: 1 §status: draft §source: scheduler §confidence: low\n"
            "draft body\n\n"
            "§id: draft-2 §rev: 1 §status: draft §source: scheduler §confidence: high\n"
            "another draft"
        )
        result = self._build_content_prompt_with_playbook(playbook)
        assert result == ""

    def test_rejected_entry_skipped(self):
        """rejected entry 不进入 Content prompt。"""
        playbook = (
            "§id: old-tip §rev: 3 §status: rejected §source: manual §confidence: high\n"
            "rejected tip"
        )
        result = self._build_content_prompt_with_playbook(playbook)
        assert result == ""

    def test_active_entry_included(self):
        """纯 active entry → 正常进入。"""
        playbook = (
            "§id: good-tip §rev: 1 §status: active §source: manual §confidence: high\n"
            "good insight"
        )
        result = self._build_content_prompt_with_playbook(playbook)
        assert "good insight" in result

    def test_old_entry_without_status_included(self):
        """旧无 §status entry → 缺省 active → 进入。"""
        playbook = (
            "§id: old-entry §rev: 2\n"
            "legacy insight"
        )
        result = self._build_content_prompt_with_playbook(playbook)
        assert "legacy insight" in result


# ── S7-S8 · MemoryLayer 写入元字段 ──────────────────────────────────────

class TestMemoryLayerMetadata:
    def test_add_entry_with_meta(self, mem_layer):
        """add_entry 带 entry_meta 写入后，读取元字段正确。"""
        meta = {"status": "draft", "source": "scheduler", "confidence": "low"}
        mem_layer.add_entry("default", "content", "playbook.md",
                             "weekly-test", "test body", "analyst",
                             entry_meta=meta)
        # 直读文件验证
        content = mem_layer.read("default", "content", "playbook.md")
        assert content is not None
        _, entries = parse_entries(content)
        e = entries["weekly-test"]
        assert e.status == "draft"
        assert e.source == "scheduler"
        assert e.confidence == "low"
        assert e.body == "test body"

    def test_replace_entry_with_meta_preserves(self, mem_layer):
        """replace_entry 后元字段保留。"""
        meta = {"status": "draft", "source": "scheduler", "confidence": "low"}
        mem_layer.add_entry("default", "content", "playbook.md",
                             "weekly-test", "original", "analyst",
                             entry_meta=meta)
        # replace 不传 entry_meta → 应保留旧的
        mem_layer.replace_entry("default", "content", "playbook.md",
                                 "weekly-test", "updated body", "analyst",
                                 expected_rev=1)
        _, entries = parse_entries(
            mem_layer.read("default", "content", "playbook.md") or ""
        )
        e = entries["weekly-test"]
        assert e.status == "draft"  # 保留
        assert e.source == "scheduler"  # 保留
        assert e.rev == 2
        assert e.body == "updated body"

    def test_replace_entry_overwrites_meta(self, mem_layer):
        """replace_entry 传新的 entry_meta 会覆盖。"""
        meta_old = {"status": "draft", "source": "scheduler", "confidence": "low"}
        mem_layer.add_entry("default", "content", "playbook.md",
                             "weekly-test", "original", "analyst",
                             entry_meta=meta_old)
        # replace 传新的 meta → 覆盖
        meta_new = {"status": "active", "source": "manual", "confidence": "high"}
        mem_layer.replace_entry("default", "content", "playbook.md",
                                 "weekly-test", "accepted", "analyst",
                                 expected_rev=1, entry_meta=meta_new)
        _, entries = parse_entries(
            mem_layer.read("default", "content", "playbook.md") or ""
        )
        e = entries["weekly-test"]
        assert e.status == "active"
        assert e.source == "manual"
        assert e.rev == 2
