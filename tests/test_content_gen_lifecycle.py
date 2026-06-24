"""
Test 3.3 — content_gen lifecycle refs pass-through (3.2) + F13 fix.

Relies on monkeypatch to mock call_kimi so no real API call is made.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from agent_tools.registry import ToolContext


def _mock_kimi_json(*, title: str = "测试标题", body: str = "测试正文") -> str:
    """Return a Kimi-like response with Chinese keys (F13 source format)."""
    import json
    return json.dumps({
        "主标题": title,
        "备选标题1": "备选1",
        "备选标题2": "备选2",
        "正文": body,
        "标签": ["tag1", "tag2"],
        "最佳发布时间": "12:00",
        "发布时间理由": "工作日午间流量高峰",
        "本次角度": "反直觉型",
        "参考关键词": "自助机器 点位",
        "生成时间": "2026-05-27 09:00:00",
    }, ensure_ascii=False)


@pytest.fixture
def mock_kimi(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace call_kimi with a stub that returns valid Chinese-key JSON."""
    responses: dict[str, str] = {}

    def _side_effect(prompt: str, system: str = "", **kwargs: Any) -> tuple[str, str | None]:
        # Use prompt hash as key to return different responses per call
        # For simplicity, return the same response for all calls
        key = str(hash(prompt[:50]))
        if key not in responses:
            responses[key] = _mock_kimi_json(
                title=f"测试标题 {len(responses) + 1}",
                body=f"测试正文 {len(responses) + 1}",
            )
        return responses[key], None

    import agent_tools.kimi as kimi_mod
    monkeypatch.setattr(kimi_mod, "call_kimi", _side_effect)


def _make_handler_args(**overrides: Any) -> dict[str, Any]:
    """Build args dict for _generate_batch_handler."""
    base = {
        "batch_size": 2,
        "system_prompt": "你是小红书运营专家。",
        "top_notes": [
            {"关键词": "选址", "标题": "如何选对位置", "互动": 5000},
            {"关键词": "收益", "标题": "月入3万的真实记录", "互动": 3200},
        ],
        "used_angles": [],
        "goal_id": "goal_001",
        "topic_id": "t_001",
        "strategy_id": "s_001",
        "calendar_item_id": None,
        "knowledge_refs": [{"type": "note", "id": "ref_001", "label": "知识库参考"}],
        "memory_refs": [],
    }
    base.update(overrides)
    return base


class TestContentGenLifecycle:
    """3.3 — content_gen lifecycle refs + F13 field mapping."""

    def test_lifecycle_fields_passed_to_records(self, mock_kimi: None) -> None:
        """Verify lifecycle refs appear in output records."""
        from agent_tools.content_gen import _generate_batch_handler

        args = _make_handler_args()
        ctx = ToolContext(tenant_id="test_tenant", storage=None)
        result = _generate_batch_handler(args, ctx)

        assert result["ok"] is True
        records = result["data"]["records"]
        assert len(records) == 2

        for rec in records:
            assert rec["topic_id"] == "t_001", f"Missing topic_id in {rec}"
            assert rec["strategy_id"] == "s_001", f"Missing strategy_id in {rec}"
            assert rec["calendar_item_id"] is None
            assert len(rec["knowledge_refs"]) == 1
            assert rec["knowledge_refs"][0]["id"] == "ref_001"

    def test_f13_english_keys_in_output(self, mock_kimi: None) -> None:
        """F13 fix: records use English keys, not Chinese."""
        from agent_tools.content_gen import _generate_batch_handler

        args = _make_handler_args()
        ctx = ToolContext(tenant_id="test_tenant", storage=None)
        result = _generate_batch_handler(args, ctx)
        records = result["data"]["records"]

        for rec in records:
            # Must have English keys (F13 fix)
            assert "title" in rec, f"Missing 'title' key; got {list(rec.keys())}"
            assert "body" in rec, f"Missing 'body' key"
            assert "hashtags" in rec, f"Missing 'hashtags' key"
            assert "content_id" in rec, f"Missing 'content_id' key"
            assert "status" in rec, f"Missing 'status' key"
            assert "publish_at" in rec, f"Missing 'publish_at' key"
            assert "meta" in rec, f"Missing 'meta' key"

            # Must NOT have Chinese keys (F13 regression guard)
            assert "主标题" not in rec, "F13 regression: Chinese key '主标题' found"
            assert "正文" not in rec, "F13 regression: Chinese key '正文' found"

            # Values must be populated
            assert rec["title"], f"Title is empty: {rec}"
            assert rec["body"], f"Body is empty: {rec}"
            # hashtags may carry # prefix (from generate_one tag_str format)
            assert len(rec["hashtags"]) == 2

    def test_none_lifecycle_fields(self, mock_kimi: None) -> None:
        """When lifecycle args are omitted, fields should be None/empty."""
        from agent_tools.content_gen import _generate_batch_handler

        args = _make_handler_args(
            topic_id=None, strategy_id=None, calendar_item_id=None,
            knowledge_refs=[], memory_refs=[],
        )
        ctx = ToolContext(tenant_id="test_tenant", storage=None)
        result = _generate_batch_handler(args, ctx)
        records = result["data"]["records"]

        for rec in records:
            assert rec["topic_id"] is None
            assert rec["strategy_id"] is None
            assert rec["calendar_item_id"] is None
            assert rec["knowledge_refs"] == []
            assert rec["memory_refs"] == []

    def test_stats_reporting(self, mock_kimi: None) -> None:
        """Verify stats in result."""
        from agent_tools.content_gen import _generate_batch_handler

        args = _make_handler_args()
        ctx = ToolContext(tenant_id="test_tenant", storage=None)
        result = _generate_batch_handler(args, ctx)

        stats = result["data"]["stats"]
        assert stats["batch_size"] == 2
        assert stats["successful"] == 2
        assert stats["failed"] == 0


class _CaptureStorage:
    """记录传入 save_generated_posts 的 df，供溯源断言。"""

    def __init__(self) -> None:
        self.df = None
        self.tenant_id = None

    def save_generated_posts(self, tenant_id: str, df: Any, meta: Any = None) -> str:
        self.df = df
        self.tenant_id = tenant_id
        return "/tmp/fake_generated.xlsx"


class TestSourceSessionIdHook:
    """P3.1.6 — 聊天里生成的笔记落库写 source_session_id 溯源字段。"""

    def test_source_session_id_stamped_on_persisted_df(self, mock_kimi: None) -> None:
        """ctx.extra['source_session_id'] → 持久化 df 的列（每行都带）。"""
        from agent_tools.content_gen import _generate_batch_handler

        storage = _CaptureStorage()
        ctx = ToolContext(tenant_id="test_tenant", storage=storage,
                          extra={"source_session_id": "os-abc123"})
        result = _generate_batch_handler(_make_handler_args(), ctx)

        assert result["ok"] is True
        assert storage.df is not None, "save_generated_posts 未被调用"
        assert "source_session_id" in storage.df.columns
        assert (storage.df["source_session_id"] == "os-abc123").all()

    def test_no_source_session_id_column_when_absent(self, mock_kimi: None) -> None:
        """无 source_session_id 时不加该列，不污染既有调用路径（dashboard 等）。"""
        from agent_tools.content_gen import _generate_batch_handler

        storage = _CaptureStorage()
        ctx = ToolContext(tenant_id="test_tenant", storage=storage)
        result = _generate_batch_handler(_make_handler_args(), ctx)

        assert result["ok"] is True
        assert storage.df is not None
        assert "source_session_id" not in storage.df.columns
