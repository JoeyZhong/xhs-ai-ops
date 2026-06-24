#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 5 P2 验收测试：TaskLedger + Master.submit_dag
运行方式：python verify_phase5_p2.py
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

_results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = ""):
    status = "PASS" if condition else "FAIL"
    mark = "[+]" if condition else "[X]"
    line = f"  {mark} {status}  {name}"
    if detail:
        line += f"  <- {detail}"
    print(line)
    _results.append((name, condition, detail))
    return condition


def section(title: str):
    print(f"\n{'='*60}\n  {title}\n{'-'*60}")


def summary():
    total = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = total - passed
    print(f"\n{'='*60}\n  结果：{passed}/{total} 通过")
    if failed:
        print(f"  失败 {failed} 项")
        for name, ok, detail in _results:
            if not ok:
                print(f"    [X] {name}" + (f": {detail}" if detail else ""))
    else:
        print("  全部通过")
    print('='*60)
    return failed == 0


# ── S1 · 单节点 dag 跑通（tracer bullet）─────────────────────────────────


def test_single_node_dag():
    section("S1 · 单节点 dag 跑通")
    from agents.master import HermesMaster, TaskResult
    from agents.task_ledger import TaskNode

    master = HermesMaster()

    def fake_submit(task):
        return TaskResult(
            task_id="x", ok=True, agent=task.type,
            content=f"[mock] {task.prompt}",
        )

    plan = [TaskNode(id="task-1", type="intel", prompt="hello world")]
    with patch.object(master, "submit", side_effect=fake_submit):
        results = master.submit_dag(plan)

    check("S1.1 返回 list 且长度 1", isinstance(results, list) and len(results) == 1)
    if results:
        r = results[0]
        check("S1.2 result.ok == True", r.ok is True)
        check("S1.3 content 含 prompt 内容", "hello world" in r.content)


# ── S2 · 拓扑排序（blocked_by 决定顺序）────────────────────────────────


def test_topo_ordering():
    section("S2 · 拓扑排序")
    from agents.master import HermesMaster, TaskResult
    from agents.task_ledger import TaskNode

    master = HermesMaster()
    call_order: list[str] = []

    def fake_submit(task):
        call_order.append(task.prompt)
        return TaskResult(
            task_id="x", ok=True, agent=task.type, content=f"[ok] {task.prompt}",
        )

    # plan 故意把 B 放前面，A 放后面（提交顺序 != 执行顺序）
    plan = [
        TaskNode(id="task-B", type="content", prompt="B-prompt", blocked_by=["task-A"]),
        TaskNode(id="task-A", type="intel",   prompt="A-prompt"),
    ]
    with patch.object(master, "submit", side_effect=fake_submit):
        results = master.submit_dag(plan)

    check("S2.1 results 长度 2", len(results) == 2)
    check("S2.2 A 先于 B 执行",
          call_order == ["A-prompt", "B-prompt"],
          detail=f"call_order={call_order}")


def test_cycle_detected():
    section("S2.cycle · 死锁检测")
    from agents.master import HermesMaster
    from agents.task_ledger import TaskNode, CycleError

    master = HermesMaster()
    plan = [
        TaskNode(id="A", type="intel", prompt="a", blocked_by=["B"]),
        TaskNode(id="B", type="intel", prompt="b", blocked_by=["A"]),
    ]
    raised = False
    try:
        master.submit_dag(plan)
    except CycleError:
        raised = True
    check("S2.cycle.1 A<->B 循环依赖触发 CycleError", raised)


# ── S3 · 变量插值 ${id.text} ────────────────────────────────────────────


def test_variable_interpolation():
    section("S3 · 变量插值 ${id.text}")
    from agents.master import HermesMaster, TaskResult
    from agents.task_ledger import TaskNode

    master = HermesMaster()
    captured: list[str] = []

    def fake_submit(task):
        captured.append(task.prompt)
        # A 的产出会被 B 引用
        return TaskResult(
            task_id="x", ok=True, agent=task.type,
            content="result-of-A" if task.prompt == "step A" else "result-of-B",
        )

    plan = [
        TaskNode(id="task-A", type="intel", prompt="step A"),
        TaskNode(id="task-B", type="content",
                 prompt="use ${task-A.text}", blocked_by=["task-A"]),
    ]
    with patch.object(master, "submit", side_effect=fake_submit):
        master.submit_dag(plan)

    check("S3.1 A prompt 原样传入", captured[0] == "step A")
    check("S3.2 B prompt 已替换 ${task-A.text} -> result-of-A",
          len(captured) >= 2 and captured[1] == "use result-of-A",
          detail=f"got={captured[1] if len(captured) >= 2 else 'N/A'}")


def test_variable_missing_reference():
    section("S3.miss · 引用未存在的 task")
    from agents.master import HermesMaster, TaskResult
    from agents.task_ledger import TaskNode

    master = HermesMaster()
    captured: list[str] = []

    def fake_submit(task):
        captured.append(task.prompt)
        return TaskResult(task_id="x", ok=True, agent=task.type, content="x")

    # 引用不存在的 task-Z（无 blocked_by），插值应保留原 placeholder 不崩
    plan = [
        TaskNode(id="task-A", type="intel", prompt="hi ${task-Z.text}"),
    ]
    with patch.object(master, "submit", side_effect=fake_submit):
        try:
            master.submit_dag(plan)
            ok = True
        except Exception as e:
            ok = False
            captured.append(f"[err] {e}")

    check("S3.miss.1 未知引用不报错（保留原文）",
          ok and captured and "${task-Z.text}" in captured[0],
          detail=f"prompt={captured[0] if captured else 'N/A'}")


# ── S4 · 失败传播：A failed -> B cancelled ────────────────────────────


def test_failure_propagation():
    section("S4 · 失败传播")
    from agents.master import HermesMaster, TaskResult
    from agents.task_ledger import TaskNode

    master = HermesMaster()
    submit_calls: list[str] = []

    def fake_submit(task):
        submit_calls.append(task.prompt)
        # A 失败
        if task.prompt == "step A":
            return TaskResult(
                task_id="x", ok=False, agent=task.type,
                error="boom", error_type="LLMError",
            )
        return TaskResult(task_id="x", ok=True, agent=task.type, content="ok")

    plan = [
        TaskNode(id="task-A", type="intel",   prompt="step A"),
        TaskNode(id="task-B", type="content", prompt="step B", blocked_by=["task-A"]),
        TaskNode(id="task-C", type="analyst", prompt="step C", blocked_by=["task-B"]),
    ]
    with patch.object(master, "submit", side_effect=fake_submit):
        results = master.submit_dag(plan)

    check("S4.1 A 实际被调用", "step A" in submit_calls)
    check("S4.2 B/C 不再被调用", "step B" not in submit_calls and "step C" not in submit_calls)
    check("S4.3 results 长度仍为 3（cancelled 占位）", len(results) == 3)
    if len(results) == 3:
        rA, rB, rC = results
        check("S4.4 A.ok == False 且 error_type 保留",
              rA.ok is False and rA.error_type == "LLMError")
        check("S4.5 B.ok == False, error_type=Cancelled",
              rB.ok is False and rB.error_type == "Cancelled")
        check("S4.6 C 也 Cancelled（传递性）",
              rC.ok is False and rC.error_type == "Cancelled")


def test_independent_branch_still_runs():
    section("S4.indep · 独立分支不受失败影响")
    from agents.master import HermesMaster, TaskResult
    from agents.task_ledger import TaskNode

    master = HermesMaster()
    submit_calls: list[str] = []

    def fake_submit(task):
        submit_calls.append(task.prompt)
        if task.prompt == "fail-A":
            return TaskResult(task_id="x", ok=False, agent=task.type,
                              error="boom", error_type="LLMError")
        return TaskResult(task_id="x", ok=True, agent=task.type, content="ok")

    # A 失败、B 依赖 A → cancelled；X 独立 → 仍跑
    plan = [
        TaskNode(id="A", type="intel",   prompt="fail-A"),
        TaskNode(id="B", type="content", prompt="b",      blocked_by=["A"]),
        TaskNode(id="X", type="analyst", prompt="indep-X"),
    ]
    with patch.object(master, "submit", side_effect=fake_submit):
        results = master.submit_dag(plan)

    check("S4.indep.1 X 独立分支仍执行", "indep-X" in submit_calls)
    by_id = {n.id: r for n, r in zip(plan, [results[0], results[1], results[2]])}
    # 上一行假设按 plan 顺序返回；如果是按拓扑顺序返回需要调整
    # 拓扑序里 A 和 X 都是入度 0，可能任一在前。我们直接按 ok/error_type 找：
    cancelled_count = sum(1 for r in results if r.error_type == "Cancelled")
    ok_count = sum(1 for r in results if r.ok)
    failed_count = sum(1 for r in results if r.ok is False and r.error_type == "LLMError")
    check("S4.indep.2 1 个 LLMError + 1 个 Cancelled + 1 个 ok",
          failed_count == 1 and cancelled_count == 1 and ok_count == 1,
          detail=f"failed={failed_count} cancelled={cancelled_count} ok={ok_count}")


# ── S5 · TaskLedger 持久化（append-only jsonl）─────────────────────────


def test_ledger_append_load(tmp_dir):
    section("S5 · TaskLedger 持久化")
    from agents.task_ledger import TaskLedger, TaskNode

    path = tmp_dir / "ledger_default.jsonl"
    ledger = TaskLedger(path)

    n1 = TaskNode(id="task-1", type="intel", prompt="p1",
                  dag_id="dag-X", status="pending", rev=1)
    ledger.append(n1)
    n1b = TaskNode(id="task-1", type="intel", prompt="p1",
                   dag_id="dag-X", status="completed", rev=2)
    ledger.append(n1b)
    n2 = TaskNode(id="task-2", type="content", prompt="p2",
                  dag_id="dag-X", status="pending", rev=1)
    ledger.append(n2)

    nodes = ledger.load_dag("dag-X")
    by_id = {n.id: n for n in nodes}

    check("S5.1 load_dag 返回 2 节点", len(nodes) == 2)
    check("S5.2 task-1 取到最新状态 completed",
          by_id.get("task-1") and by_id["task-1"].status == "completed",
          detail=f"got={by_id.get('task-1').status if by_id.get('task-1') else 'N/A'}")
    check("S5.3 task-2 仍为 pending",
          by_id.get("task-2") and by_id["task-2"].status == "pending")


def test_ledger_isolation_across_dags(tmp_dir):
    section("S5.iso · 多 dag 隔离")
    from agents.task_ledger import TaskLedger, TaskNode

    path = tmp_dir / "ledger_default.jsonl"
    ledger = TaskLedger(path)

    ledger.append(TaskNode(id="task-1", type="intel", prompt="p",
                           dag_id="dag-A", status="pending"))
    ledger.append(TaskNode(id="task-1", type="intel", prompt="p",
                           dag_id="dag-B", status="pending"))

    a = ledger.load_dag("dag-A")
    b = ledger.load_dag("dag-B")
    check("S5.iso.1 dag-A / dag-B 各自只见到自己的 task-1",
          len(a) == 1 and len(b) == 1)


# ── S6 · 状态机：pending -> in_progress -> completed ─────────────────


def test_ledger_records_state_transitions(tmp_dir):
    section("S6 · submit_dag 状态机写盘")
    from agents.master import HermesMaster, TaskResult
    from agents.task_ledger import TaskNode, TaskLedger

    path = tmp_dir / "ledger_default.jsonl"
    master = HermesMaster()
    master._ledger_path = path  # 注入测试路径

    def fake_submit(task):
        return TaskResult(task_id="x", ok=True, agent=task.type, content="done")

    plan = [TaskNode(id="task-1", type="intel", prompt="p1")]
    with patch.object(master, "submit", side_effect=fake_submit):
        master.submit_dag(plan, dag_id="dag-T")

    # 读 ledger，应至少记录到 completed 状态
    ledger = TaskLedger(path)
    nodes = ledger.load_dag("dag-T")
    check("S6.1 ledger 写入 1 个 task", len(nodes) == 1)
    if nodes:
        check("S6.2 终态为 completed",
              nodes[0].status == "completed",
              detail=f"got={nodes[0].status}")


def test_ledger_cancel_stale_in_progress(tmp_dir):
    section("S7 · 启动清残留 in_progress")
    from agents.task_ledger import TaskLedger, TaskNode

    path = tmp_dir / "ledger_default.jsonl"
    ledger = TaskLedger(path)
    # 模拟上次 Streamlit 崩溃留下的 in_progress
    ledger.append(TaskNode(id="task-1", type="intel", prompt="p",
                           dag_id="dag-old", status="in_progress", rev=2))
    ledger.append(TaskNode(id="task-2", type="content", prompt="p2",
                           dag_id="dag-old", status="completed", rev=2))

    n_canceled = ledger.cancel_stale_in_progress()
    check("S7.1 返回清除数 == 1", n_canceled == 1)

    nodes = ledger.load_dag("dag-old")
    by_id = {n.id: n for n in nodes}
    check("S7.2 task-1 已被强制 cancelled",
          by_id.get("task-1") and by_id["task-1"].status == "cancelled")
    check("S7.3 task-2 仍是 completed（不动正常态）",
          by_id.get("task-2") and by_id["task-2"].status == "completed")


# ── S8 · DAG 整体状态推算 ──────────────────────────────────────────────


def test_dag_status_completed(tmp_dir):
    section("S8 · DAG 整体状态")
    from agents.master import HermesMaster, TaskResult
    from agents.task_ledger import TaskNode

    master = HermesMaster()
    master._ledger_path = tmp_dir / "l.jsonl"

    def fake(t):
        return TaskResult(task_id="x", ok=True, agent=t.type, content="ok")

    plan = [TaskNode(id="A", type="intel", prompt="a")]
    with patch.object(master, "submit", side_effect=fake):
        master.submit_dag(plan, dag_id="dag-1")

    s = master.get_dag_status("dag-1")
    check("S8.1 全 ok 时 dag_status == completed",
          s == "completed", detail=f"got={s}")


def test_dag_status_partial_failure(tmp_dir):
    from agents.master import HermesMaster, TaskResult
    from agents.task_ledger import TaskNode

    master = HermesMaster()
    master._ledger_path = tmp_dir / "l.jsonl"

    def fake(t):
        if t.prompt == "fail":
            return TaskResult(task_id="x", ok=False, agent=t.type,
                              error="x", error_type="LLMError")
        return TaskResult(task_id="x", ok=True, agent=t.type, content="ok")

    plan = [
        TaskNode(id="A", type="intel",   prompt="ok"),
        TaskNode(id="B", type="content", prompt="fail"),
    ]
    with patch.object(master, "submit", side_effect=fake):
        master.submit_dag(plan, dag_id="dag-2")

    s = master.get_dag_status("dag-2")
    check("S8.2 有失败有成功 -> partial_failure",
          s == "partial_failure", detail=f"got={s}")


def test_dag_status_all_failed(tmp_dir):
    from agents.master import HermesMaster, TaskResult
    from agents.task_ledger import TaskNode

    master = HermesMaster()
    master._ledger_path = tmp_dir / "l.jsonl"

    def fake(t):
        return TaskResult(task_id="x", ok=False, agent=t.type,
                          error="x", error_type="LLMError")

    plan = [TaskNode(id="A", type="intel", prompt="a")]
    with patch.object(master, "submit", side_effect=fake):
        master.submit_dag(plan, dag_id="dag-3")

    s = master.get_dag_status("dag-3")
    check("S8.3 全失败 -> failed",
          s == "failed", detail=f"got={s}")


# ── S9 · 菱形依赖 + 大 plan ────────────────────────────────────────────


def test_diamond_dependency():
    section("S9 · 菱形依赖")
    from agents.master import HermesMaster, TaskResult
    from agents.task_ledger import TaskNode

    master = HermesMaster()
    order: list[str] = []

    def fake(t):
        order.append(t.prompt)
        return TaskResult(task_id="x", ok=True, agent=t.type, content="r")

    # 菱形：A -> B,C ; B,C -> D
    plan = [
        TaskNode(id="A", type="intel",   prompt="A"),
        TaskNode(id="B", type="content", prompt="B", blocked_by=["A"]),
        TaskNode(id="C", type="analyst", prompt="C", blocked_by=["A"]),
        TaskNode(id="D", type="content", prompt="D", blocked_by=["B", "C"]),
    ]
    with patch.object(master, "submit", side_effect=fake):
        master.submit_dag(plan)

    check("S9.1 A 先于 B,C", order.index("A") < order.index("B")
          and order.index("A") < order.index("C"))
    check("S9.2 B,C 先于 D", order.index("B") < order.index("D")
          and order.index("C") < order.index("D"))
    check("S9.3 共执行 4 次", len(order) == 4)


def test_large_chain():
    section("S9.large · 长链 10 节点")
    from agents.master import HermesMaster, TaskResult
    from agents.task_ledger import TaskNode

    master = HermesMaster()
    order: list[str] = []

    def fake(t):
        order.append(t.prompt)
        return TaskResult(task_id="x", ok=True, agent=t.type, content="r")

    # T1 -> T2 -> ... -> T10
    plan = [TaskNode(id=f"T{i}", type="intel", prompt=f"T{i}",
                     blocked_by=[f"T{i-1}"] if i > 1 else [])
            for i in range(1, 11)]
    with patch.object(master, "submit", side_effect=fake):
        master.submit_dag(plan)

    expected = [f"T{i}" for i in range(1, 11)]
    check("S9.large.1 10 节点按链顺序执行",
          order == expected, detail=f"got={order}")


def test_two_dags_dont_interfere(tmp_dir):
    section("S10 · 两次 submit_dag 不互相干扰")
    from agents.master import HermesMaster, TaskResult
    from agents.task_ledger import TaskNode

    master = HermesMaster()
    master._ledger_path = tmp_dir / "l.jsonl"

    def fake(t):
        return TaskResult(task_id="x", ok=True, agent=t.type, content=t.prompt)

    p1 = [TaskNode(id="A", type="intel", prompt="dag1-A")]
    p2 = [TaskNode(id="A", type="intel", prompt="dag2-A")]
    with patch.object(master, "submit", side_effect=fake):
        master.submit_dag(p1, dag_id="d1")
        master.submit_dag(p2, dag_id="d2")

    s1 = master.get_dag_status("d1")
    s2 = master.get_dag_status("d2")
    check("S10.1 d1 / d2 各自 completed",
          s1 == "completed" and s2 == "completed")


# ── S11-S13 · Planner ─────────────────────────────────────────────────────


class MockProvider:
    """Mock LLM provider for planner tests."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self._idx = 0
        self.calls: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        if self._idx < len(self._responses):
            r = self._responses[self._idx]
            self._idx += 1
            return r
        return "{}"


def test_planner_valid_plan():
    section("S11 · planner 合法 plan 解析")
    from agents.planner import plan_from_intent, PlannerError
    from agents.task_ledger import TaskNode

    valid_json = json.dumps({
        "plan": [
            {"id": "task-1", "type": "intel", "prompt": "采集笔记"},
            {"id": "task-2", "type": "content", "prompt": "写草稿",
             "blocked_by": ["task-1"]},
        ]
    })
    provider = MockProvider([valid_json])
    nodes = plan_from_intent("test", provider=provider)

    check("S11.1 返回 list 长度 2", isinstance(nodes, list) and len(nodes) == 2)
    if len(nodes) == 2:
        check("S11.2 task-1 拓扑在前",
              nodes[0].id == "task-1" and nodes[1].id == "task-2")
        check("S11.3 类型正确",
              nodes[0].type == "intel" and nodes[1].type == "content")
        check("S11.4 blocked_by 保留",
              nodes[1].blocked_by == ["task-1"])


def test_planner_invalid_schema():
    section("S12 · planner schema 错误")
    from agents.planner import plan_from_intent, PlannerError

    cases = [
        ("缺 plan 顶层", json.dumps({"tasks": []})),
        ("缺 id", json.dumps({"plan": [{"type": "intel", "prompt": "p"}]})),
        ("缺 type", json.dumps({"plan": [{"id": "t1", "prompt": "p"}]})),
        ("缺 prompt", json.dumps({"plan": [{"id": "t1", "type": "intel"}]})),
        ("错 type", json.dumps({"plan": [{"id": "t1", "type": "bad",
                                            "prompt": "p"}]})),
        ("id 重复", json.dumps({"plan": [
            {"id": "t1", "type": "intel", "prompt": "p1"},
            {"id": "t1", "type": "content", "prompt": "p2"},
        ]})),
        ("外部依赖", json.dumps({"plan": [
            {"id": "t1", "type": "intel", "prompt": "p1",
             "blocked_by": ["nonexistent"]},
        ]})),
        ("空 plan", json.dumps({"plan": []})),
        ("超 6 节点", json.dumps({"plan": [
            {"id": f"t{i}", "type": "intel", "prompt": f"p{i}"}
            for i in range(1, 8)
        ]})),
        ("循环依赖", json.dumps({"plan": [
            {"id": "A", "type": "intel", "prompt": "a",
             "blocked_by": ["B"]},
            {"id": "B", "type": "intel", "prompt": "b",
             "blocked_by": ["A"]},
        ]})),
        ("prompt 空白", json.dumps({"plan": [
            {"id": "t1", "type": "intel", "prompt": "   "},
        ]})),
    ]

    for label, bad_json in cases:
        provider = MockProvider([bad_json])
        raised = False
        try:
            plan_from_intent("test", provider=provider, max_retries=0)
        except PlannerError:
            raised = True
        check(f"S12.{label} -> PlannerError", raised,
              detail=f"json={bad_json[:60]}...")


def test_planner_retry_success():
    section("S13 · planner 重试成功")
    from agents.planner import plan_from_intent

    bad = "not json"
    valid = json.dumps({
        "plan": [{"id": "t1", "type": "analyst", "prompt": "分析"}]
    })
    provider = MockProvider([bad, valid])
    nodes = plan_from_intent("test", provider=provider, max_retries=2)

    check("S13.1 重试后成功", len(nodes) == 1 and nodes[0].id == "t1")
    check("S13.2 provider 被调用 2 次", len(provider.calls) == 2)


# ── S14-S15 · retry_task ──────────────────────────────────────────────────


def test_retry_task_basic(tmp_dir):
    section("S14 · retry_task 基本路径")
    from agents.master import HermesMaster, TaskResult
    from agents.task_ledger import TaskNode, TaskLedger

    master = HermesMaster()
    master._ledger_path = tmp_dir / "l.jsonl"

    call_count = {"B": 0}

    def fake(t):
        if t.prompt == "step B":
            call_count["B"] += 1
            if call_count["B"] == 1:
                return TaskResult(
                    task_id="x", ok=False, agent=t.type,
                    error="boom", error_type="LLMError",
                )
            return TaskResult(task_id="x", ok=True, agent=t.type, content="B-ok")
        return TaskResult(task_id="x", ok=True, agent=t.type, content=t.prompt)

    plan = [
        TaskNode(id="A", type="intel",   prompt="step A"),
        TaskNode(id="B", type="content", prompt="step B", blocked_by=["A"]),
        TaskNode(id="C", type="analyst", prompt="step C", blocked_by=["B"]),
    ]
    with patch.object(master, "submit", side_effect=fake):
        master.submit_dag(plan, dag_id="dag-retry")
        s = master.get_dag_status("dag-retry")
        check("S14.1 首次执行 partial_failure", s == "partial_failure")

        retry_results = master.retry_task("dag-retry", "B")

    check("S14.2 retry 返回 2 个结果（B+C）", len(retry_results) == 2)
    if len(retry_results) == 2:
        rB, rC = retry_results
        check("S14.3 B 重试后 ok", rB.ok is True and rB.content == "B-ok")
        check("S14.4 C 也重新执行并 ok", rC.ok is True)

    ledger = TaskLedger(tmp_dir / "l.jsonl")
    nodes = ledger.load_dag("dag-retry")
    by_id = {n.id: n for n in nodes}
    check("S14.5 最终 A completed", by_id.get("A") and by_id["A"].status == "completed")
    check("S14.6 最终 B completed", by_id.get("B") and by_id["B"].status == "completed")
    check("S14.7 最终 C completed", by_id.get("C") and by_id["C"].status == "completed")


def test_retry_task_status_guard(tmp_dir):
    section("S15 · retry_task 状态保护")
    from agents.master import HermesMaster
    from agents.task_ledger import TaskNode, TaskLedger

    master = HermesMaster()
    master._ledger_path = tmp_dir / "l.jsonl"

    ledger = TaskLedger(tmp_dir / "l.jsonl")
    ledger.append(TaskNode(id="X", type="intel", prompt="p",
                           dag_id="dag-guard", status="in_progress", rev=1))

    raised = False
    detail = ""
    try:
        master.retry_task("dag-guard", "X")
    except ValueError as e:
        raised = True
        detail = str(e)
    check("S15.1 in_progress task 拒绝重试", raised, detail=detail)


# ── S16 · tenant 隔离 ─────────────────────────────────────────────────────


def test_tenant_isolation(tmp_dir):
    section("S16 · tenant_id 参数化")
    from agents.master import HermesMaster, TaskResult
    from agents.task_ledger import TaskNode, TaskLedger

    # 默认 tenant
    m1 = HermesMaster()
    check("S16.1 默认 tenant 路径不变",
          str(m1._ledger_path).endswith("ledger_default.jsonl"))

    # 自定义 tenant
    m2 = HermesMaster(tenant_id="tenant-a", ledger_dir=tmp_dir)
    expected = tmp_dir / "ledger_tenant-a.jsonl"
    check("S16.2 自定义 tenant 路径正确",
          m2._ledger_path == expected,
          detail=f"got={m2._ledger_path}")

    # 多 tenant 隔离
    def fake(t):
        return TaskResult(task_id="x", ok=True, agent=t.type, content=t.prompt)

    m_a = HermesMaster(tenant_id="a", ledger_dir=tmp_dir)
    m_b = HermesMaster(tenant_id="b", ledger_dir=tmp_dir)

    with patch.object(m_a, "submit", side_effect=fake):
        m_a.submit_dag([TaskNode(id="T", type="intel", prompt="pa")], dag_id="d")
    with patch.object(m_b, "submit", side_effect=fake):
        m_b.submit_dag([TaskNode(id="T", type="intel", prompt="pb")], dag_id="d")

    ledger_a = TaskLedger(tmp_dir / "ledger_a.jsonl")
    ledger_b = TaskLedger(tmp_dir / "ledger_b.jsonl")
    nodes_a = ledger_a.load_dag("d")
    nodes_b = ledger_b.load_dag("d")
    check("S16.3 tenant-a / tenant-b ledger 隔离",
          len(nodes_a) == 1 and len(nodes_b) == 1)
    if nodes_a and nodes_b:
        check("S16.4 tenant-a 内容正确",
              nodes_a[0].prompt == "pa")
        check("S16.5 tenant-b 内容正确",
              nodes_b[0].prompt == "pb")


def test_submit_dag_progress_cb():
    section("S17 · submit_dag progress_cb")
    from agents.master import HermesMaster, TaskResult
    from agents.task_ledger import TaskNode

    master = HermesMaster()
    cb_log = []

    def fake(t):
        return TaskResult(task_id="x", ok=True, agent=t.type, content="ok")

    def cb(tid, status, result):
        cb_log.append((tid, status, result.ok if result else None))

    plan = [
        TaskNode(id="A", type="intel", prompt="A"),
        TaskNode(id="B", type="content", prompt="B", blocked_by=["A"]),
    ]
    with patch.object(master, "submit", side_effect=fake):
        master.submit_dag(plan, progress_cb=cb)

    completed = [x for x in cb_log if x[1] == "completed"]
    in_progress = [x for x in cb_log if x[1] == "in_progress"]
    check("S17.1 in_progress 回调 ≥ 2", len(in_progress) >= 2,
          detail=f"in_progress={len(in_progress)}")
    check("S17.2 completed 回调 ≥ 2", len(completed) >= 2,
          detail=f"completed={len(completed)}")
    check("S17.3 A 先于 B completed",
          next((i for i, x in enumerate(cb_log) if x == ("A", "completed", True)), -1)
          < next((i for i, x in enumerate(cb_log) if x == ("B", "completed", True)), -1))


# ── 入口 ───────────────────────────────────────────────────────────────────


def main():
    test_single_node_dag()
    test_topo_ordering()
    test_cycle_detected()
    test_variable_interpolation()
    test_variable_missing_reference()
    test_failure_propagation()
    test_independent_branch_still_runs()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_ledger_append_load(tmp)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_ledger_isolation_across_dags(tmp)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_ledger_records_state_transitions(tmp)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_ledger_cancel_stale_in_progress(tmp)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_dag_status_completed(tmp)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_dag_status_partial_failure(tmp)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_dag_status_all_failed(tmp)
    test_diamond_dependency()
    test_large_chain()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_two_dags_dont_interfere(tmp)
    test_planner_valid_plan()
    test_planner_invalid_schema()
    test_planner_retry_success()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_retry_task_basic(tmp)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_retry_task_status_guard(tmp)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_tenant_isolation(tmp)
    test_submit_dag_progress_cb()
    return 0 if summary() else 1


if __name__ == "__main__":
    sys.exit(main())
