"""
TaskLedger（P2.1）：DAG 任务节点数据模型 + 持久化 + 拓扑/状态机。

设计：
- TaskNode 是 plan 内的局部节点（id 由用户提供，如 task-1/task-2，用于变量插值）
- dag_id 标识同一次 submit_dag 调用，区分同 tenant 多 dag
- ledger jsonl: xhs_data/tasks/ledger_<tenant>.jsonl，每行一个 TaskNode 状态快照
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Literal, Optional

TaskStatus = Literal["pending", "in_progress", "completed", "failed", "cancelled"]


@dataclass
class TaskNode:
    """DAG 节点。id 是 plan 局部变量名（用户提供），用于 ${id.text} 插值。"""

    id: str
    type: str  # "intel" | "content" | "analyst"
    prompt: str
    blocked_by: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    status: TaskStatus = "pending"
    result: Optional[dict[str, Any]] = None  # 序列化的 TaskResult
    dag_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    rev: int = 0


class CycleError(Exception):
    """DAG 含循环依赖。"""


def topo_sort(plan: list[TaskNode]) -> list[TaskNode]:
    """
    Kahn 算法。仅按 blocked_by 排序（blocks 字段为派生信息，不参与）。
    plan 中所有 blocked_by 引用必须在 plan 内，否则视为外部依赖忽略。

    返回拓扑序列；检测到循环抛 CycleError。
    """
    by_id = {n.id: n for n in plan}
    indegree = {
        n.id: sum(1 for dep in n.blocked_by if dep in by_id)
        for n in plan
    }
    # Kahn：取 indegree=0 的节点入队
    queue = [n for n in plan if indegree[n.id] == 0]
    sorted_out: list[TaskNode] = []

    while queue:
        node = queue.pop(0)
        sorted_out.append(node)
        # 把它从其他人的 blocked_by 里"摘掉"
        for other in plan:
            if node.id in other.blocked_by:
                indegree[other.id] -= 1
                if indegree[other.id] == 0:
                    queue.append(other)

    if len(sorted_out) != len(plan):
        remaining = [n.id for n in plan if n not in sorted_out]
        raise CycleError(f"cycle detected among nodes: {remaining}")
    return sorted_out


# ── TaskLedger ──────────────────────────────────────────────────────────


class TaskLedger:
    """
    Append-only jsonl ledger，按 (dag_id, id) 分组取最新 rev。

    设计：
    - 每次状态变更追加新行（不修改旧行）
    - load_dag(dag_id) 读所有行，filter dag_id，按 id groupby 取 rev 最大者
    - 文件级 lock 防多线程并发写
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def append(self, node: TaskNode) -> None:
        """追加一行 jsonl。自动填 updated_at。"""
        if not node.updated_at:
            node.updated_at = datetime.now().isoformat(timespec="seconds")
        if not node.created_at:
            node.created_at = node.updated_at
        line = json.dumps(asdict(node), ensure_ascii=False) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line)

    def _read_all(self) -> list[TaskNode]:
        if not self.path.exists():
            return []
        out: list[TaskNode] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    out.append(TaskNode(**d))
                except Exception:
                    continue
        return out

    def load_dag(self, dag_id: str) -> list[TaskNode]:
        """取 dag 内所有 node 的最新状态（按 (id, rev) 取 rev 最大者）。"""
        rows = [n for n in self._read_all() if n.dag_id == dag_id]
        latest: dict[str, TaskNode] = {}
        for n in rows:
            cur = latest.get(n.id)
            if cur is None or n.rev >= cur.rev:
                latest[n.id] = n
        return list(latest.values())

    def latest_rev(self, dag_id: str, task_id: str) -> int:
        """返回 (dag_id, task_id) 最大 rev；不存在返 0。"""
        rows = self._read_all()
        max_rev = 0
        for n in rows:
            if n.dag_id == dag_id and n.id == task_id:
                if n.rev > max_rev:
                    max_rev = n.rev
        return max_rev

    def get_completed_results(self, dag_id: str) -> dict[str, str]:
        """
        返回 {task_id: result.content}，仅 status=completed 节点。
        retry_task 用它恢复插值上下文。
        """
        rows = self._read_all()
        latest: dict[str, TaskNode] = {}
        for n in rows:
            if n.dag_id != dag_id:
                continue
            cur = latest.get(n.id)
            if cur is None or n.rev >= cur.rev:
                latest[n.id] = n
        out: dict[str, str] = {}
        for n in latest.values():
            if n.status == "completed" and n.result:
                content = n.result.get("content", "")
                out[n.id] = content
        return out

    def cancel_stale_in_progress(self) -> int:
        """
        启动时调用：把所有 status=in_progress 的 node 强制 cancelled（rev+1）。
        返回清除数。防 Streamlit 重启残留。
        """
        rows = self._read_all()
        latest: dict[tuple[str, str], TaskNode] = {}
        for n in rows:
            key = (n.dag_id, n.id)
            cur = latest.get(key)
            if cur is None or n.rev >= cur.rev:
                latest[key] = n

        n_cancelled = 0
        for n in latest.values():
            if n.status == "in_progress":
                self.append(TaskNode(
                    id=n.id, type=n.type, prompt=n.prompt,
                    blocked_by=list(n.blocked_by), blocks=list(n.blocks),
                    status="cancelled",
                    result={"reason": "stale_in_progress_at_startup"},
                    dag_id=n.dag_id, rev=n.rev + 1,
                ))
                n_cancelled += 1
        return n_cancelled
