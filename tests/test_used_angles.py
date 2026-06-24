"""P3.1 · used_angles 三态 schema —— normalize / angle_names 纯函数测试。

设计基线：openspec/changes/content-lifecycle-v2/design.md §5
- 老 schema: ["反直觉型", "工具型"]
- 新 schema: [{"angle":"反直觉型","status":"validated_hit","evidence_count":5,"last_ces":320}, ...]
- normalize 幂等：老数组 wrap 成 unknown 态对象；新对象数组原样保留（补全缺省字段）
- angle_names：两种形态都返回 list[str]（给只认字符串的消费点容错）
"""
from __future__ import annotations

from agents.used_angles import normalize_used_angles, angle_names, VALID_STATUSES


class TestNormalize:
    def test_empty(self):
        assert normalize_used_angles([]) == []
        assert normalize_used_angles(None) == []

    def test_legacy_string_array_wrapped_unknown(self):
        out = normalize_used_angles(["反直觉型", "工具型"])
        assert out == [
            {"angle": "反直觉型", "status": "unknown", "evidence_count": 0, "last_ces": None},
            {"angle": "工具型", "status": "unknown", "evidence_count": 0, "last_ces": None},
        ]

    def test_new_object_array_preserved(self):
        src = [{"angle": "反直觉型", "status": "validated_hit", "evidence_count": 5, "last_ces": 320}]
        assert normalize_used_angles(src) == src

    def test_object_missing_fields_filled(self):
        out = normalize_used_angles([{"angle": "工具型"}])
        assert out == [{"angle": "工具型", "status": "unknown", "evidence_count": 0, "last_ces": None}]

    def test_idempotent(self):
        once = normalize_used_angles(["反直觉型"])
        twice = normalize_used_angles(once)
        assert once == twice

    def test_invalid_status_coerced_to_unknown(self):
        out = normalize_used_angles([{"angle": "X", "status": "bogus"}])
        assert out[0]["status"] == "unknown"

    def test_mixed_array(self):
        out = normalize_used_angles(["反直觉型", {"angle": "工具型", "status": "sunk", "last_ces": 45}])
        assert out[0] == {"angle": "反直觉型", "status": "unknown", "evidence_count": 0, "last_ces": None}
        assert out[1]["angle"] == "工具型" and out[1]["status"] == "sunk" and out[1]["last_ces"] == 45

    def test_blank_angle_skipped(self):
        assert normalize_used_angles(["", {"angle": ""}, {"angle": "  "}]) == []

    def test_dedup_keeps_first(self):
        out = normalize_used_angles(["反直觉型", {"angle": "反直觉型", "status": "sunk"}])
        assert len(out) == 1
        assert out[0]["status"] == "unknown"  # first wins


class TestAngleNames:
    def test_from_legacy(self):
        assert angle_names(["反直觉型", "工具型"]) == ["反直觉型", "工具型"]

    def test_from_new(self):
        src = [{"angle": "反直觉型", "status": "validated_hit"}, {"angle": "工具型", "status": "sunk"}]
        assert angle_names(src) == ["反直觉型", "工具型"]

    def test_empty(self):
        assert angle_names([]) == []
        assert angle_names(None) == []


def test_valid_statuses_contract():
    assert VALID_STATUSES == {"unknown", "validated_hit", "sunk"}
