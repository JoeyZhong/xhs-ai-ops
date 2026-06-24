"""
Methodology integration fixes — TDD
- shared/methodology.md → shared/orchestration.md (no naming collision with analyst)
- orchestration.md not in shared snapshot defaults (Planner-only, save tokens)
- dashboard loads via MemoryLayer (no Path bypass, no hardcoded tenant)
"""
from __future__ import annotations

from pathlib import Path

import pytest


# ── 1. 命名冲突修复：shared 那份改名为 orchestration.md ─────────────────

def test_shared_orchestration_file_exists():
    p = Path("memory/default/shared/orchestration.md")
    assert p.exists(), "shared/orchestration.md (renamed) must exist"


def test_old_shared_methodology_file_removed():
    p = Path("memory/default/shared/methodology.md")
    assert not p.exists(), "old shared/methodology.md must be removed (collision)"


def test_analyst_methodology_still_exists():
    """analyst/methodology.md 不动，scope 内不冲突。"""
    p = Path("memory/default/analyst/methodology.md")
    assert p.exists()


# ── 2. snapshot 默认列表：shared 不含 orchestration.md（Planner 专用）──

def test_shared_default_snapshot_excludes_orchestration():
    from agents.memory import MemoryLayer

    class _StubStorage:
        def load_memory(self, *a, **kw): return None
        def save_memory(self, *a, **kw): pass

    m = MemoryLayer(storage=_StubStorage())
    # snapshot() with files=None → uses defaults; capture which files it asks for
    asked: list[str] = []

    class _CapturingStorage:
        def load_memory(self, tenant, scope, file):
            asked.append(file)
            return None
        def save_memory(self, *a, **kw): pass

    m2 = MemoryLayer(storage=_CapturingStorage())
    m2.snapshot("default", "shared")
    assert "methodology.md" not in asked, \
        "shared default must not load methodology.md (renamed + Planner-only)"
    assert "orchestration.md" not in asked, \
        "orchestration.md is Planner-only, must not be in shared defaults"


def test_analyst_default_snapshot_includes_methodology():
    from agents.memory import MemoryLayer

    asked: list[str] = []

    class _CapturingStorage:
        def load_memory(self, tenant, scope, file):
            asked.append(file)
            return None
        def save_memory(self, *a, **kw): pass

    m = MemoryLayer(storage=_CapturingStorage())
    m.snapshot("default", "analyst")
    assert "methodology.md" in asked, "analyst default must still include methodology.md"


# ── 3. 统一加载路径：master 暴露 .memory，dashboard 不再 Path 旁路 ─────

def test_master_exposes_memory_property():
    from agents.master import HermesMaster

    master = HermesMaster()
    assert hasattr(master, "memory"), "master must expose .memory accessor"
    # 必须是 MemoryLayer 实例（read/write/snapshot 接口齐备）
    assert hasattr(master.memory, "read")
    assert hasattr(master.memory, "snapshot")


def test_dashboard_uses_memorylayer_not_path():
    """dashboard.py DAG 模式不再用 Path('memory/default/shared/methodology.md').read_text。"""
    src = Path("dashboard.py").read_text(encoding="utf-8")
    # 旧的 Path 旁路必须删除
    assert 'Path("memory/default/shared/methodology.md")' not in src
    assert 'Path("memory/default/shared/orchestration.md")' not in src
    # 必须走 MemoryLayer
    assert "master.memory.read" in src or "master.memory.snapshot" in src, \
        "dashboard must load orchestration via master.memory.*, not raw Path"
