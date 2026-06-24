"""P3.3 · AnalystEvaluator._update_playbook 集成测试（local backend 真跑）。

验证：
- 读 generated posts（带 meta.ces_score）→ classify → 写 <!-- analyst-auto --> 块
- 防污染：手写区保留
- backup playbook.md.bak
- 更新 goals.used_angles 对应 angle 的 status
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from agents.playbook_learning import AUTO_BEGIN, AUTO_END


@pytest.fixture()
def evaluator(tmp_path, monkeypatch):
    # 用 local backend，避开 HermesMaster 重依赖：直接构造 evaluator 后替换 storage/memory
    cfg = tmp_path / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "settings.json").write_text(json.dumps({"llm_provider": "mock"}), encoding="utf-8")
    goals = {
        "active_goal_id": "g1",
        "goals": [{
            "id": "g1", "name": "t", "status": "active",
            "used_angles": [
                {"angle": "反直觉型", "status": "unknown", "evidence_count": 3, "last_ces": 250},
                {"angle": "工具型", "status": "unknown", "evidence_count": 3, "last_ces": 50},
            ],
        }],
    }
    (cfg / "goals.json").write_text(json.dumps(goals, ensure_ascii=False), encoding="utf-8")

    from storage.local_json import LocalJsonBackend
    backend = LocalJsonBackend(base_dir=str(tmp_path))

    # 预置 generated posts：反直觉型 3 篇高 CES、工具型 3 篇低 CES
    rows = []
    for ces in (300, 250, 200):
        rows.append({"content_id": f"hit{ces}", "goal_id": "g1", "angle": "反直觉型",
                     "title": "x", "body": "b", "status": "draft", "meta": {"ces_score": ces}})
    for ces in (60, 50, 40):
        rows.append({"content_id": f"sunk{ces}", "goal_id": "g1", "angle": "工具型",
                     "title": "x", "body": "b", "status": "draft", "meta": {"ces_score": ces}})
    backend.save_generated_posts("default", pd.DataFrame(rows), meta={"goal_id": "g1"})

    # 预置手写 playbook
    backend.save_memory("default", "content", "playbook.md",
                        "# 运营手写区\n这段必须保留。")

    from agents.evaluators import AnalystEvaluator
    ev = AnalystEvaluator.__new__(AnalystEvaluator)
    ev._tenant_id = "default"
    ev._data_dir = tmp_path / "xhs_data"
    ev._config_dir = cfg

    class _FakeMaster:
        _storage = backend
    ev._master = _FakeMaster()
    ev._memory = None
    return ev, backend


def test_update_playbook_writes_auto_block(evaluator):
    ev, backend = evaluator
    ev._update_playbook("default")
    pb = backend.load_memory("default", "content", "playbook.md")
    assert AUTO_BEGIN in pb and AUTO_END in pb
    assert "反直觉型" in pb
    assert "这段必须保留。" in pb  # 防污染


def test_update_playbook_classifies_tristate(evaluator):
    ev, backend = evaluator
    ev._update_playbook("default")
    goals = backend.load_goals("default")
    ua = {e["angle"]: e["status"] for e in goals["goals"][0]["used_angles"]}
    assert ua["反直觉型"] == "validated_hit"  # avg 250 > 200
    assert ua["工具型"] == "sunk"             # avg 50 < 80


def test_update_playbook_backup_created(evaluator):
    ev, backend = evaluator
    ev._update_playbook("default")
    bak = backend.load_memory("default", "content", "playbook.md.bak")
    assert bak is not None
    assert "这段必须保留。" in bak  # backup 是改写前的原文


def test_update_playbook_idempotent_single_block(evaluator):
    ev, backend = evaluator
    ev._update_playbook("default")
    ev._update_playbook("default")
    pb = backend.load_memory("default", "content", "playbook.md")
    assert pb.count(AUTO_BEGIN) == 1  # 跑两次仍只有一个自动区
