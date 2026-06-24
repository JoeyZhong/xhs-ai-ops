"""P3.1 · used_angles 三态迁移脚本测试（local backend 真跑）。

scripts/migrate_used_angles_to_tristate.py 的核心 transform：
load_goals → 每个 goal 的 used_angles 经 normalize → save_goals。幂等。
"""
from __future__ import annotations

import json

import pytest

from storage.local_json import LocalJsonBackend
from scripts.migrate_used_angles_to_tristate import migrate_tenant


def _write_goals(backend: LocalJsonBackend, tenant_id: str, goals: list[dict]) -> None:
    backend.save_goals(tenant_id, {"active_goal_id": goals[0]["id"] if goals else "", "goals": goals})


@pytest.fixture()
def backend(tmp_path):
    return LocalJsonBackend(base_dir=str(tmp_path))


def test_legacy_array_migrated(backend):
    _write_goals(backend, "default", [
        {"id": "g1", "name": "t", "used_angles": ["反直觉型", "工具型"]},
    ])
    changed = migrate_tenant(backend, "default")
    assert changed == 1
    out = backend.load_goals("default")["goals"][0]["used_angles"]
    assert out == [
        {"angle": "反直觉型", "status": "unknown", "evidence_count": 0, "last_ces": None},
        {"angle": "工具型", "status": "unknown", "evidence_count": 0, "last_ces": None},
    ]


def test_idempotent_second_run_no_change(backend):
    _write_goals(backend, "default", [
        {"id": "g1", "name": "t", "used_angles": ["反直觉型"]},
    ])
    assert migrate_tenant(backend, "default") == 1
    # 第二次跑：已是三态，无变更
    assert migrate_tenant(backend, "default") == 0


def test_already_tristate_preserved(backend):
    _write_goals(backend, "default", [
        {"id": "g1", "name": "t", "used_angles": [
            {"angle": "反直觉型", "status": "validated_hit", "evidence_count": 5, "last_ces": 320},
        ]},
    ])
    assert migrate_tenant(backend, "default") == 0
    out = backend.load_goals("default")["goals"][0]["used_angles"]
    assert out[0]["status"] == "validated_hit"
    assert out[0]["last_ces"] == 320


def test_empty_used_angles_untouched(backend):
    _write_goals(backend, "default", [{"id": "g1", "name": "t", "used_angles": []}])
    assert migrate_tenant(backend, "default") == 0


def test_missing_used_angles_field_untouched(backend):
    _write_goals(backend, "default", [{"id": "g1", "name": "t"}])
    assert migrate_tenant(backend, "default") == 0
    assert "used_angles" not in backend.load_goals("default")["goals"][0]


def test_multiple_goals_mixed(backend):
    _write_goals(backend, "default", [
        {"id": "g1", "name": "t1", "used_angles": ["反直觉型"]},
        # g2 缺 evidence_count/last_ces → normalize 补全，算一次变更
        {"id": "g2", "name": "t2", "used_angles": [{"angle": "工具型", "status": "sunk"}]},
        # g3 已是完整三态 → 不变
        {"id": "g3", "name": "t3", "used_angles": [
            {"angle": "数字清单型", "status": "unknown", "evidence_count": 0, "last_ces": None},
        ]},
        {"id": "g4", "name": "t4", "used_angles": []},
    ])
    changed = migrate_tenant(backend, "default")
    assert changed == 2  # g1 (legacy) + g2 (field fill); g3 already complete, g4 empty
    out = backend.load_goals("default")["goals"]
    assert out[1]["used_angles"][0] == {"angle": "工具型", "status": "sunk", "evidence_count": 0, "last_ces": None}
