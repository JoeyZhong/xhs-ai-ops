"""P3.1 · used_angles 三态 schema 规整工具（纯函数，无 IO）。

设计基线：openspec/changes/content-lifecycle-v2/design.md §5

老 schema（v1）：
    "used_angles": ["反直觉型", "工具型"]

新 schema（三态）：
    "used_angles": [
        {"angle": "反直觉型", "status": "validated_hit", "evidence_count": 5, "last_ces": 320},
        {"angle": "工具型",   "status": "sunk",          "evidence_count": 2, "last_ces": 45},
    ]

三态由 AnalystEvaluator 自动写（见 agents/evaluators.py）：
- validated_hit / sunk / unknown

本模块只负责形态规整与读取容错，不做 IO、不做状态判定。
"""
from __future__ import annotations

from typing import Any

VALID_STATUSES = {"unknown", "validated_hit", "sunk"}

_DEFAULTS = {"status": "unknown", "evidence_count": 0, "last_ces": None}


def normalize_used_angles(raw: Any) -> list[dict]:
    """把任意形态的 used_angles 规整成三态对象数组（幂等）。

    - 老字符串数组 → 每项 wrap 成 unknown 态对象
    - 新对象数组 → 补全缺省字段，非法 status 归 unknown
    - 空字符串 / 空 angle / None → 跳过
    - 同名 angle 去重，保留第一次出现（含其状态）
    """
    if not raw:
        return []

    out: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            angle = item.strip()
            entry = {"angle": angle, **_DEFAULTS}
        elif isinstance(item, dict):
            angle = str(item.get("angle", "")).strip()
            status = item.get("status", "unknown")
            if status not in VALID_STATUSES:
                status = "unknown"
            entry = {
                "angle": angle,
                "status": status,
                "evidence_count": item.get("evidence_count", 0) or 0,
                "last_ces": item.get("last_ces", None),
            }
        else:
            continue

        if not angle or angle in seen:
            continue
        seen.add(angle)
        out.append(entry)
    return out


def angle_names(raw: Any) -> list[str]:
    """从任意形态的 used_angles 取出角度名列表（给只认字符串的消费点容错）。"""
    return [e["angle"] for e in normalize_used_angles(raw)]
