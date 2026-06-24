"""P2 存储层测试 · orchestrator_sessions

LocalJsonBackend 的会话 CRUD：创建/取回/续接、OCC rev 冲突、跨租户隔离、列表。
（PG 实现 SQL 同形，本机 PG 不可达 → 见 db/migrations/010 reviewed-only）
"""
from __future__ import annotations

import pytest

from storage.base import RevMismatch
from storage.local_json import LocalJsonBackend


def _backend(tmp_path) -> LocalJsonBackend:
    return LocalJsonBackend(base_dir=str(tmp_path))


def test_create_and_get_roundtrip(tmp_path):
    b = _backend(tmp_path)
    created = b.create_session(
        "default", session_id="os-aaa", goal_id="goal_001",
        status="gathering", messages=[{"role": "user", "text": "hi"}],
        proposed_plan=[], decision_cards=[], dag_id=None)
    assert created["rev"] == 1
    assert created["session_id"] == "os-aaa"

    got = b.get_session("default", "os-aaa")
    assert got is not None
    assert got["goal_id"] == "goal_001"
    assert got["messages"] == [{"role": "user", "text": "hi"}]
    assert got["status"] == "gathering"


def test_get_missing_returns_none(tmp_path):
    b = _backend(tmp_path)
    assert b.get_session("default", "os-nope") is None


def test_update_bumps_rev_and_persists(tmp_path):
    b = _backend(tmp_path)
    b.create_session("default", session_id="os-bbb")
    updated = b.update_session("default", "os-bbb", expected_rev=1,
                               status="planned",
                               proposed_plan=[{"id": "t1", "type": "intel",
                                               "prompt": "采集", "blocked_by": []}])
    assert updated["rev"] == 2
    assert updated["status"] == "planned"
    # 续接：重新读出仍是 planned + rev=2
    again = b.get_session("default", "os-bbb")
    assert again["rev"] == 2
    assert again["status"] == "planned"
    assert len(again["proposed_plan"]) == 1


def test_update_stale_rev_raises(tmp_path):
    b = _backend(tmp_path)
    b.create_session("default", session_id="os-ccc")
    b.update_session("default", "os-ccc", expected_rev=1, status="planned")  # rev→2
    with pytest.raises(RevMismatch):
        b.update_session("default", "os-ccc", expected_rev=1, status="dispatched")


def test_update_missing_raises_keyerror(tmp_path):
    b = _backend(tmp_path)
    with pytest.raises(KeyError):
        b.update_session("default", "os-nope", expected_rev=1, status="planned")


def test_cross_tenant_isolation(tmp_path):
    b = _backend(tmp_path)
    b.create_session("tenant-a", session_id="os-shared", goal_id="g")
    # 另一租户既看不到，也改不动
    assert b.get_session("tenant-b", "os-shared") is None
    with pytest.raises(KeyError):
        b.update_session("tenant-b", "os-shared", expected_rev=1, status="planned")


def test_list_sessions_sorted_recent_first(tmp_path):
    b = _backend(tmp_path)
    b.create_session("default", session_id="os-1")
    b.create_session("default", session_id="os-2")
    b.update_session("default", "os-1", expected_rev=1, status="planned")  # bump os-1 updated_at
    sessions = b.list_sessions("default", limit=10)
    assert {s["session_id"] for s in sessions} == {"os-1", "os-2"}
    assert sessions[0]["session_id"] == "os-1"  # 最近更新在前


def test_trace_append_cycle(tmp_path):
    """Trace 通过读→追加→写模拟协调器追加语义。"""
    b = _backend(tmp_path)
    b.create_session("default", session_id="os-trace1")

    # 第 1 轮: thinking
    s = b.get_session("default", "os-trace1")
    trace = list(s.get("trace", []))
    trace.append({"type": "thinking", "seq": 1, "summary": "分析意图"})
    s = b.update_session("default", "os-trace1", expected_rev=s["rev"], trace=trace)
    assert s["rev"] == 2
    assert len(s["trace"]) == 1

    # 第 2 轮: subagent_start
    trace = list(s.get("trace", []))
    trace.append({"type": "subagent_start", "seq": 2, "archetype": "intel", "task": "采集"})
    s = b.update_session("default", "os-trace1", expected_rev=s["rev"], trace=trace)
    assert s["rev"] == 3
    assert len(s["trace"]) == 2

    # 持久化验证
    got = b.get_session("default", "os-trace1")
    assert len(got["trace"]) == 2
    assert got["trace"][0]["type"] == "thinking"
    assert got["trace"][1]["type"] == "subagent_start"


def test_pending_read_write(tmp_path):
    """Pending 字段: 设 question → 读回 → 清空 → 读回为 None。"""
    b = _backend(tmp_path)
    b.create_session("default", session_id="os-pend1")

    # 设 pending = question
    updated = b.update_session("default", "os-pend1", expected_rev=1,
                               pending={"kind": "question", "question": "确认执行？"})
    assert updated["pending"] == {"kind": "question", "question": "确认执行？"}

    # 清空 pending
    updated = b.update_session("default", "os-pend1", expected_rev=updated["rev"],
                               pending=None)
    assert updated["pending"] is None

    # 持久化验证
    got = b.get_session("default", "os-pend1")
    assert got["pending"] is None


def test_resume_with_trace(tmp_path):
    """续接恢复: trace 跨读写周期存活,status 同步变更。"""
    b = _backend(tmp_path)
    b.create_session("default", session_id="os-resume1")

    # 第 1 轮: thinking + subagent_start
    s = b.get_session("default", "os-resume1")
    trace = list(s.get("trace", []))
    trace.append({"type": "thinking", "seq": 1, "summary": "分析"})
    trace.append({"type": "subagent_start", "seq": 2, "archetype": "intel", "task": "采集"})
    s = b.update_session("default", "os-resume1", expected_rev=s["rev"],
                         trace=trace, status="thinking")

    # 第 2 轮(续接): subagent_result → final
    trace = list(s.get("trace", []))
    trace.append({"type": "subagent_result", "seq": 3, "archetype": "intel",
                  "ok": True, "summary": "采集完成"})
    trace.append({"type": "final", "seq": 4, "summary": "建议开启招商"})
    s = b.update_session("default", "os-resume1", expected_rev=s["rev"],
                         trace=trace, status="done")

    assert len(s["trace"]) == 4
    assert s["status"] == "done"

    # 持久化验证
    got = b.get_session("default", "os-resume1")
    assert len(got["trace"]) == 4
    assert got["trace"][-1]["type"] == "final"


def test_delete_session_removes_and_returns_true(tmp_path):
    b = _backend(tmp_path)
    b.create_session("default", session_id="os-del")
    assert b.delete_session("default", "os-del") is True
    assert b.get_session("default", "os-del") is None


def test_delete_missing_returns_false(tmp_path):
    b = _backend(tmp_path)
    assert b.delete_session("default", "os-nope") is False


def test_delete_cross_tenant_blocked(tmp_path):
    b = _backend(tmp_path)
    b.create_session("tenant-a", session_id="os-x")
    assert b.delete_session("tenant-b", "os-x") is False
    assert b.get_session("tenant-a", "os-x") is not None


def test_list_sessions_filters_by_goal(tmp_path):
    b = _backend(tmp_path)
    b.create_session("default", session_id="os-g1", goal_id="goal_001")
    b.create_session("default", session_id="os-g2", goal_id="goal_002")
    only1 = b.list_sessions("default", goal_id="goal_001", limit=10)
    assert {s["session_id"] for s in only1} == {"os-g1"}
    allg = b.list_sessions("default", limit=10)
    assert {s["session_id"] for s in allg} == {"os-g1", "os-g2"}
