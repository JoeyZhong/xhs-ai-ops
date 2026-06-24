from __future__ import annotations

import json
from typing import Any

from agent_tools.registry import ToolContext, invoke


class _MemoryEvidenceStorage:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def upsert_evidence(self, tenant_id: str, evidence: dict) -> dict:
        row = {**evidence, "tenant_id": tenant_id}
        self.rows[row["source_note_id"]] = row
        return dict(row)

    def list_evidence(self, tenant_id: str, *, angle=None, funnel_stage=None, limit=3):
        items = list(self.rows.values())
        if angle is not None:
            items = [i for i in items if i.get("angle") == angle]
        if funnel_stage is not None:
            items = [i for i in items if i.get("funnel_stage") == funnel_stage]
        return items[:limit]


def _note(note_id: str, *, ces: float = 360.0, funnel: str = "trust") -> dict[str, Any]:
    return {
        "note_id": note_id,
        "title": f"{note_id} 标题",
        "content": f"{note_id} 正文，讲一个真实点位故事",
        "ces_score": ces,
        "funnel_stage": funnel,
        "keyword": "自助机点位",
    }


def _json_array(rows: list[dict[str, Any]]) -> str:
    return json.dumps(rows, ensure_ascii=False)


def test_extract_evidence_tool_batch_upserts_valid_rows(monkeypatch):
    import agent_tools.intel_evidence  # noqa: F401 - registers tool

    storage = _MemoryEvidenceStorage()

    def fake_call_kimi(prompt: str, **kwargs: Any):
        return _json_array([
            {
                "source_note_id": "n1",
                "angle": "反直觉型",
                "funnel_stage": "trust",
                "hook": "人流大的地方，未必适合放售货机",
                "key_insight": "工厂夜班和写字楼午休是两个完全不同的补货节奏。",
            },
            {
                "source_note_id": "n2",
                "angle": "工具型",
                "funnel_stage": "trust",
                "hook": "先看这张点位评分表",
                "key_insight": "用消费时段、补货半径、物业配合度三项筛掉低效点位。",
            },
        ]), None

    monkeypatch.setattr("agent_tools.kimi.call_kimi", fake_call_kimi)

    result = invoke(
        "intel.extract_evidence",
        {"notes": [_note("n1"), _note("n2")], "batch_size": 10},
        ToolContext(tenant_id="tenant-a", storage=storage),
    )

    assert result["ok"] is True
    data = result["data"]
    assert data["extracted_count"] == 2
    assert data["errors"] == []
    assert set(storage.rows) == {"n1", "n2"}
    assert storage.rows["n1"]["evidence_id"] == "tenant-a:n1"
    assert storage.rows["n1"]["ces_score"] == 360.0
    assert storage.rows["n1"]["raw"]["title"] == "n1 标题"


def test_extract_evidence_tool_falls_back_per_note_when_batch_json_invalid(monkeypatch):
    import agent_tools.intel_evidence  # noqa: F401 - registers tool

    storage = _MemoryEvidenceStorage()
    calls: list[str] = []

    def fake_call_kimi(prompt: str, **kwargs: Any):
        calls.append(prompt)
        if len(calls) == 1:
            return "不是 JSON", None
        note_id = "n1" if '"n1"' in prompt else "n2"
        return _json_array([
            {
                "source_note_id": note_id,
                "angle": "数字清单型",
                "funnel_stage": "traffic",
                "hook": "3 个信号判断点位能不能做",
                "key_insight": "高 CES 笔记会把判断标准拆成可检查清单。",
            }
        ]), None

    monkeypatch.setattr("agent_tools.kimi.call_kimi", fake_call_kimi)

    result = invoke(
        "intel.extract_evidence",
        {
            "notes": [_note("n1", funnel="traffic"), _note("n2", funnel="traffic")],
            "batch_size": 10,
        },
        ToolContext(tenant_id="tenant-a", storage=storage),
    )

    assert result["ok"] is True
    assert result["data"]["extracted_count"] == 2
    assert result["data"]["fallback_batches"] == 1
    assert len(calls) == 3
    assert set(storage.rows) == {"n1", "n2"}


def test_extract_evidence_tool_rejects_invalid_angle(monkeypatch):
    import agent_tools.intel_evidence  # noqa: F401 - registers tool

    storage = _MemoryEvidenceStorage()

    def fake_call_kimi(prompt: str, **kwargs: Any):
        return _json_array([
            {
                "source_note_id": "n1",
                "angle": "广告招商型",
                "funnel_stage": "conversion",
                "hook": "这个 hook 不该入库",
                "key_insight": "非法 angle 必须被后端拒绝。",
            }
        ]), None

    monkeypatch.setattr("agent_tools.kimi.call_kimi", fake_call_kimi)

    result = invoke(
        "intel.extract_evidence",
        {"notes": [_note("n1", funnel="conversion")], "batch_size": 10},
        ToolContext(tenant_id="tenant-a", storage=storage),
    )

    assert result["ok"] is True
    assert result["data"]["extracted_count"] == 0
    assert result["data"]["errors"]
    assert storage.rows == {}
