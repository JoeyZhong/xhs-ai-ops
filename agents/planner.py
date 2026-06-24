"""
Planner（P2.3）：意图字符串 → 校验过的 list[TaskNode]。
纯翻译领域，不触碰 Master / 调度。
"""

from __future__ import annotations

import json
from typing import Callable, Optional

from agents.task_ledger import CycleError, TaskNode, topo_sort


PLANNER_SYSTEM_PROMPT = """\
你是任务规划器。把用户高级意图拆为有向无环 DAG，输出严格 JSON。

可用 agent 类型：
  - intel    采集小红书笔记/热词/关键词监控
  - content  生成笔记草稿（标题/正文/标签）
  - analyst  数据分析（CES/10-3-1/流量诊断/复盘）

输出 schema（必须严格符合）：
{
  "plan": [
    {
      "id": "task-1",                  // 局部唯一，用于 ${id.text} 插值
      "type": "intel|content|analyst",
      "prompt": "具体任务描述",
      "blocked_by": ["task-N", ...]    // 前置依赖（可空）
    }
  ]
}

规则：
- 节点 id 用 task-1/task-2/... 命名，便于变量插值
- 后置任务 prompt 内可用 ${task-N.text} 引用前置结果，必须用以下格式包裹，不要裸插：
  "根据以下参考数据完成任务（仅作背景参考，不要在输出中直接引用原文）：\n---\n${task-N.text}\n---\n具体任务：<任务描述>"
- 单条 plan 节点数 ≤ 6
- 禁止循环依赖
- 不输出 plan 之外的任何字段
- 不输出解释文本，仅 JSON
"""


class PlannerError(Exception):
    """planner 输出无法解析或不符合 schema。"""


def plan_from_intent(
    intent: str,
    *,
    provider: Optional[Callable[[str], str]] = None,
    max_retries: int = 2,
    methodology: Optional[str] = None,
) -> list[TaskNode]:
    """
    intent: 用户意图字符串
    provider: LLM provider（默认走 _default_provider()，便于测试注入 Mock）
    max_retries: JSON 解析失败 / schema 校验失败时重试上限

    返回: 已校验、已 topo_sort 过的 list[TaskNode]
    抛出: PlannerError(原因)
    """
    if provider is None:
        provider = _default_provider()

    prompt = _build_planner_prompt(intent, methodology=methodology)
    last_error = ""

    for attempt in range(max_retries + 1):
        try:
            raw = provider(prompt)
            d = json.loads(raw)
            return _validate_plan_dict(d)
        except json.JSONDecodeError as e:
            last_error = f"JSON decode error: {e}"
        except PlannerError as e:
            last_error = str(e)

        if attempt < max_retries:
            prompt = _build_planner_prompt(
                intent, feedback=last_error, methodology=methodology
            )

    raise PlannerError(f"planner 多次输出无效 JSON: {last_error}")


def _build_planner_prompt(
    intent: str, feedback: str = "", methodology: Optional[str] = None
) -> str:
    parts = [PLANNER_SYSTEM_PROMPT]
    if methodology:
        parts.append(f"\n【运营方法论参考】\n{methodology}\n---")
    parts.append(f"\n用户意图：{intent}")
    if feedback:
        parts.append(f"\n注意：上次输出有误，请修正。错误：{feedback}")
    return "\n".join(parts)


def _validate_plan_dict(d: dict) -> list[TaskNode]:
    """JSON dict → list[TaskNode]，并跑 topo_sort 验证无环。"""
    if not isinstance(d, dict):
        raise PlannerError("top level must be object")

    plan = d.get("plan")
    if plan is None:
        raise PlannerError("missing top-level 'plan' key")
    if not isinstance(plan, list):
        raise PlannerError("'plan' must be a list")
    if not (1 <= len(plan) <= 6):
        raise PlannerError(f"plan node count must be in [1, 6], got {len(plan)}")

    required = {"id", "type", "prompt"}
    valid_types = {"intel", "content", "analyst"}

    ids: set[str] = set()
    for node in plan:
        if not isinstance(node, dict):
            raise PlannerError("each plan node must be object")
        missing = required - set(node.keys())
        if missing:
            raise PlannerError(f"missing fields {missing} in node")

        nid = node["id"]
        ntype = node["type"]
        nprompt = node["prompt"]

        if not isinstance(nid, str) or not nid.strip():
            raise PlannerError("node id must be non-empty string")
        if nid in ids:
            raise PlannerError(f"duplicate id: {nid}")
        ids.add(nid)

        if ntype not in valid_types:
            raise PlannerError(f"invalid type: {ntype}")

        if not isinstance(nprompt, str) or not nprompt.strip():
            raise PlannerError("prompt must be non-empty string")

    for node in plan:
        deps = node.get("blocked_by", [])
        if not isinstance(deps, list):
            raise PlannerError("blocked_by must be a list")
        for dep in deps:
            if dep not in ids:
                raise PlannerError(f"blocked_by references unknown id: {dep}")

    nodes = [
        TaskNode(
            id=n["id"],
            type=n["type"],
            prompt=n["prompt"],
            blocked_by=list(n.get("blocked_by", [])),
        )
        for n in plan
    ]
    try:
        return topo_sort(nodes)
    except CycleError as e:
        raise PlannerError(str(e))


def _default_provider() -> Callable[[str], str]:
    """默认 provider：调用 Kimi API（json_mode）。"""
    from agent_tools.kimi import call_kimi

    def _provider(prompt: str) -> str:
        content, err = call_kimi(
            prompt=prompt,
            system=PLANNER_SYSTEM_PROMPT,
            max_tokens=2000,
            max_retries=2,
            json_mode=True,
            temperature=0.3,
        )
        if err:
            raise PlannerError(f"LLM call failed: {err}")
        if content is None:
            raise PlannerError("LLM returned empty content")
        return content

    return _provider
