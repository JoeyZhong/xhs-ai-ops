"""
HermesMaster — Master Agent（OpenClaw 风格）。

职责：
1. 任务分发：所有 Sub Agent 调用必须经过此处
2. 安全网关：注入合适的 ToolPolicy
3. 审计：每个阶段写 JSONL 日志
4. 失败兜底：超时 / policy violation / exception 各自策略
"""

from __future__ import annotations

import secrets
import time
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

from agents import base as agent_base
from agents.audit import make_logger
from agents.base import AgentTask, AgentResult, DirectInvocationError
from agents.memory import MemoryLayer
from agents.policy import (
    policy_for_intel, policy_for_content, policy_for_analyst,
)
from agents.intel import IntelAgent
from agents.content import ContentAgent
from agents.analyst import AnalystAgent
from storage import get_backend


# ── 任务结果（对外） ────────────────────────────────────────────────────

@dataclass
class TaskResult:
    task_id: str
    ok: bool
    agent: str
    content: str = ""
    error: Optional[str] = None
    error_type: Optional[str] = None
    iterations: int = 0
    tool_calls: list[dict] = field(default_factory=list)
    duration_ms: int = 0
    submitted_at: str = ""
    completed_at: str = ""

    @classmethod
    def denied(cls, task_id: str, agent: str, reason: str):
        return cls(task_id=task_id, ok=False, agent=agent,
                   error=reason, error_type="PolicyViolation",
                   submitted_at=datetime.now().isoformat(timespec="seconds"))

    @classmethod
    def from_agent_result(cls, task_id: str, agent: str,
                            ar: AgentResult, started_ts: float,
                            submitted_at: str):
        return cls(
            task_id=task_id, ok=ar.ok, agent=agent,
            content=ar.content, error=ar.error, error_type=ar.error_type,
            iterations=ar.iterations, tool_calls=ar.tool_calls,
            duration_ms=int((time.perf_counter() - started_ts) * 1000),
            submitted_at=submitted_at,
            completed_at=datetime.now().isoformat(timespec="seconds"),
        )


# ── HermesMaster ────────────────────────────────────────────────────────

class HermesMaster:
    """
    使用方式：
        master = HermesMaster()
        result = master.submit(AgentTask(type="intel", prompt="采集深圳点位招商相关笔记"))
    """

    AGENT_CLASSES = {
        "intel":   IntelAgent,
        "content": ContentAgent,
        "analyst": AnalystAgent,
    }

    POLICY_FACTORIES = {
        "intel":   policy_for_intel,
        "content": policy_for_content,
        "analyst": policy_for_analyst,
    }

    def __init__(
        self,
        settings: Optional[dict] = None,
        *,
        tenant_id: str = "default",
        ledger_dir: Optional[Path] = None,
    ):
        # 关键：生成 master_token，Sub Agent 实例化时验证
        self._master_token = agent_base._generate_master_token()
        self._storage = get_backend(settings or {})
        self._memory = MemoryLayer(storage=self._storage)
        self.memory = self._memory
        self._task_counter = 0
        # P2.3: tenant_id 参数化 ledger 路径
        from pathlib import Path
        self._tenant_id = tenant_id
        self._ledger_path: Path = (
            (ledger_dir or Path("xhs_data") / "tasks")
            / f"ledger_{tenant_id}.jsonl"
        )
        # P2.2.5: 启动时清残留 in_progress（防 Streamlit 重启残留）
        try:
            from agents.task_ledger import TaskLedger
            n = TaskLedger(self._ledger_path).cancel_stale_in_progress()
            if n:
                print(f"[HermesMaster] cleared {n} stale in_progress task(s)")
        except Exception:
            pass

    # ── 对外入口 ─────────────────────────────────────────────────────

    def submit(self, task: AgentTask, progress_cb=None) -> TaskResult:
        # 1. 注册任务
        task_id = self._next_task_id()
        submitted_at = datetime.now().isoformat(timespec="seconds")
        started = time.perf_counter()

        audit = make_logger(self._storage, tenant_id=task.tenant_id, task_id=task_id)
        audit.write({
            "kind": "master_submit",
            "task_type": task.type, "tenant_id": task.tenant_id,
            "goal_id": task.goal_id, "prompt_preview": task.prompt[:200],
            "budget_tokens": task.budget_tokens,
        })

        # 1.5 输入校验
        if not task.prompt or not task.prompt.strip():
            audit.write({"kind": "master_validation_failed", "reason": "empty_prompt"})
            return TaskResult.denied(task_id, task.type or "?",
                                       "task prompt cannot be empty")

        # 2. 路由
        agent_cls = self.AGENT_CLASSES.get(task.type)
        policy_factory = self.POLICY_FACTORIES.get(task.type)
        if agent_cls is None or policy_factory is None:
            audit.write({"kind": "master_route_failed",
                          "task_type": task.type, "reason": "unknown_type"})
            return TaskResult.denied(task_id, task.type or "?",
                                       f"unknown task type: {task.type}")

        # 3. 实例化 Agent（注入 master_token）
        try:
            agent = agent_cls(
                master_token=self._master_token,
                memory=self._memory,
                audit=audit,
                policy=policy_factory(),
                tenant_id=task.tenant_id,
                task_id=task_id,
                goal_id=task.goal_id,
            )
        except DirectInvocationError as e:
            audit.write({"kind": "master_spawn_failed", "error": str(e)})
            return TaskResult.denied(task_id, task.type, str(e))

        # 4. 执行
        try:
            ar = agent.run(task, progress_cb=progress_cb)
        except Exception as e:
            audit.write({
                "kind": "master_unhandled_exception",
                "agent": task.type, "error": str(e),
                "trace": traceback.format_exc(),
            })
            ar = AgentResult.failed(str(e), type(e).__name__)

        # 5. 包装结果
        result = TaskResult.from_agent_result(
            task_id=task_id, agent=task.type,
            ar=ar, started_ts=started, submitted_at=submitted_at,
        )

        audit.write({
            "kind": "master_complete",
            "agent": task.type, "ok": result.ok,
            "iterations": result.iterations,
            "tool_calls": len(result.tool_calls),
            "duration_ms": result.duration_ms,
            "error": result.error,
        })

        # 6. 持久化结果
        try:
            self._storage.save_task_result(task.tenant_id, task_id, asdict(result))
        except Exception:
            pass

        return result

    # ── 内部 ─────────────────────────────────────────────────────────

    def _next_task_id(self) -> str:
        self._task_counter += 1
        return f"task-{int(time.time())}-{self._task_counter:04d}-{secrets.token_hex(3)}"

    # ── 检视接口（Console 用） ────────────────────────────────────────

    def list_tools(self) -> list[str]:
        from agent_tools import registry
        return registry.list_tools()

    def get_policy(self, agent_type: str):
        factory = self.POLICY_FACTORIES.get(agent_type)
        return factory() if factory else None

    # ── DAG 检视（P2.2.4 整体状态推算）──────────────────────────────────

    def get_dag_status(self, dag_id: str) -> str:
        """
        从 ledger 读取 dag 所有节点最新状态，推算整体 status。

        - completed: 全部 completed
        - failed: 全部 failed/cancelled（无 completed）
        - partial_failure: 既有 completed 又有 failed/cancelled
        - in_progress: 还有 pending / in_progress
        - unknown: dag 不存在
        """
        from agents.task_ledger import TaskLedger
        nodes = TaskLedger(self._ledger_path).load_dag(dag_id)
        if not nodes:
            return "unknown"
        statuses = {n.status for n in nodes}
        if statuses == {"completed"}:
            return "completed"
        if statuses <= {"failed", "cancelled"}:
            return "failed"
        if "completed" in statuses and (statuses & {"failed", "cancelled"}):
            return "partial_failure"
        return "in_progress"

    # ── DAG 提交（P2.2）─────────────────────────────────────────────────

    def submit_dag(self, plan: list, dag_id: Optional[str] = None, progress_cb=None,
                   tenant_id: Optional[str] = None, extra: Optional[dict] = None,
                   task_budget: Optional[int] = None) -> list[TaskResult]:
        """
        提交 DAG plan，串行执行（v1）。

        - plan: list[TaskNode]
        - dag_id: 可选，自动生成 uuid
        - 返回: list[TaskResult]，按拓扑顺序
        - 变量插值：node.prompt 中 ${id.text} 替换为已完成 task 的 result.content；
          未知引用保留原 placeholder。
        - 失败传播：blocked_by 中任一 task 失败/cancelled，则当前 task 标 Cancelled。
        - 状态机持久化：pending -> in_progress -> completed | failed | cancelled
        """
        import re
        import uuid as _uuid
        from dataclasses import asdict
        from agents.task_ledger import TaskLedger, topo_sort

        ordered = topo_sort(plan)
        results_by_id: dict[str, TaskResult] = {}
        results: list[TaskResult] = []

        if not dag_id:
            dag_id = f"dag-{_uuid.uuid4().hex[:8]}"

        ledger = TaskLedger(self._ledger_path)

        # 写入所有 node 初始 pending 状态
        for node in ordered:
            node.dag_id = dag_id
            node.rev = 1
            node.status = "pending"
            ledger.append(node)

        placeholder_re = re.compile(r"\$\{([\w\-]+)\.text\}")

        def interpolate(prompt: str) -> str:
            def repl(m):
                ref_id = m.group(1)
                ref = results_by_id.get(ref_id)
                if ref is None or not getattr(ref, "ok", False):
                    return m.group(0)
                return ref.content
            return placeholder_re.sub(repl, prompt)

        def make_cancelled(task_id_local: str, agent: str) -> TaskResult:
            return TaskResult(
                task_id=task_id_local, ok=False, agent=agent,
                error="upstream task failed or cancelled",
                error_type="Cancelled",
            )

        def write_status(node, status, r=None):
            from agents.task_ledger import TaskNode as _TN
            ledger.append(_TN(
                id=node.id, type=node.type, prompt=node.prompt,
                blocked_by=list(node.blocked_by), blocks=list(node.blocks),
                status=status,
                result=asdict(r) if r is not None else None,
                dag_id=dag_id, rev=node.rev + 1,
            ))
            node.rev += 1
            node.status = status
            if progress_cb:
                progress_cb(node.id, status, r)

        for node in ordered:
            upstream_failed = any(
                (results_by_id.get(dep) is None) or (not results_by_id[dep].ok)
                for dep in node.blocked_by
            ) if node.blocked_by else False

            if upstream_failed:
                r = make_cancelled(node.id, node.type)
                write_status(node, "cancelled", r)
            else:
                # in_progress
                write_status(node, "in_progress")
                final_prompt = interpolate(node.prompt)
                task = AgentTask(
                    type=node.type, prompt=final_prompt,
                    tenant_id=tenant_id or "default",
                    extra=dict(extra or {}),
                    budget_tokens=task_budget or AgentTask.budget_tokens,
                )
                r = self.submit(task)
                # 把内部 task_id 替换为 DAG node.id，使 retry_task 能在 ledger 中找到
                from dataclasses import replace as _dc_replace
                r = _dc_replace(r, task_id=node.id)
                write_status(node, "completed" if r.ok else "failed", r)

            results_by_id[node.id] = r
            results.append(r)
        return results

    # ── DAG 重试（P2.3）───────────────────────────────────────────────────

    def retry_task(self, dag_id: str, task_id: str,
                   tenant_id: Optional[str] = None, extra: Optional[dict] = None) -> list[TaskResult]:
        """
        单 task 重试（工业级工作流引擎风格）。

        步骤：
        1. 从 ledger 加载该 dag 全量 nodes（取每 id 最大 rev）
        2. 校验 task_id 存在 + 状态 ∈ {failed, cancelled, completed}
           （pending / in_progress 直接拒绝）
        3. 收集 task_id 的所有传递下游（blocks 链 BFS）
        4. 对 task_id + 下游全部追加新 rev：状态 reset 为 pending
        5. 复用执行循环，但仅遍历这批被 reset 的节点
           （上游 completed 结果从 ledger.result 字段反序列化恢复）
        6. 返回这批节点的 TaskResult 列表
        """
        import re
        from dataclasses import asdict
        from agents.task_ledger import TaskLedger, TaskNode, topo_sort

        ledger = TaskLedger(self._ledger_path)
        all_nodes = ledger.load_dag(dag_id)
        if not all_nodes:
            raise ValueError(f"dag {dag_id} not found")

        by_id = {n.id: n for n in all_nodes}
        if task_id not in by_id:
            raise ValueError(f"task {task_id} not found in dag {dag_id}")

        target = by_id[task_id]
        if target.status in ("pending", "in_progress"):
            raise ValueError(
                f"task {task_id} status is {target.status}, cannot retry"
            )

        # 重建 blocks 关系（ledger 只存 blocked_by）
        for n in all_nodes:
            n.blocks = []
        for n in all_nodes:
            for dep in n.blocked_by:
                if dep in by_id:
                    by_id[dep].blocks.append(n.id)

        # BFS 收集传递下游
        downstream = set()
        queue = [task_id]
        while queue:
            current = queue.pop(0)
            if current in downstream:
                continue
            downstream.add(current)
            node = by_id.get(current)
            if node:
                for child in node.blocks:
                    queue.append(child)

        # 重置下游节点为 pending
        for node in all_nodes:
            if node.id in downstream:
                node.rev = ledger.latest_rev(dag_id, node.id) + 1
                node.status = "pending"
                ledger.append(TaskNode(
                    id=node.id, type=node.type, prompt=node.prompt,
                    blocked_by=list(node.blocked_by), blocks=list(node.blocks),
                    status="pending", dag_id=dag_id, rev=node.rev,
                ))

        # 恢复上游 completed 结果（用于插值）
        completed_results = ledger.get_completed_results(dag_id)
        results_by_id: dict[str, TaskResult] = {}
        for tid, content in completed_results.items():
            if tid not in downstream:
                results_by_id[tid] = TaskResult(
                    task_id=tid, ok=True, agent=by_id[tid].type,
                    content=content,
                )

        # 执行循环（仅遍历被 reset 的节点）
        all_sorted = topo_sort(all_nodes)
        reset_set = set(downstream)

        placeholder_re = re.compile(r"\$\{([\w\-]+)\.text\}")

        def interpolate(prompt: str) -> str:
            def repl(m):
                ref_id = m.group(1)
                ref = results_by_id.get(ref_id)
                if ref is None or not getattr(ref, "ok", False):
                    return m.group(0)
                return ref.content
            return placeholder_re.sub(repl, prompt)

        def make_cancelled(task_id_local: str, agent: str) -> TaskResult:
            return TaskResult(
                task_id=task_id_local, ok=False, agent=agent,
                error="upstream task failed or cancelled",
                error_type="Cancelled",
            )

        def write_status(node, status, r=None):
            ledger.append(TaskNode(
                id=node.id, type=node.type, prompt=node.prompt,
                blocked_by=list(node.blocked_by), blocks=list(node.blocks),
                status=status,
                result=asdict(r) if r is not None else None,
                dag_id=dag_id, rev=node.rev + 1,
            ))
            node.rev += 1
            node.status = status

        results: list[TaskResult] = []
        for node in all_sorted:
            if node.id not in reset_set:
                continue

            upstream_failed = any(
                (results_by_id.get(dep) is None) or (not results_by_id[dep].ok)
                for dep in node.blocked_by
            ) if node.blocked_by else False

            if upstream_failed:
                r = make_cancelled(node.id, node.type)
                write_status(node, "cancelled", r)
            else:
                write_status(node, "in_progress")
                final_prompt = interpolate(node.prompt)
                task = AgentTask(
                    type=node.type, prompt=final_prompt,
                    tenant_id=tenant_id or "default",
                    extra=dict(extra or {}),
                )
                r = self.submit(task)
                from dataclasses import replace as _dc_replace
                r = _dc_replace(r, task_id=node.id)
                write_status(node, "completed" if r.ok else "failed", r)

            results_by_id[node.id] = r
            results.append(r)

        return results
