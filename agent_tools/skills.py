"""
skills.read 工具：Agent 按需读取 skill 全文。

调用机制：
- Agent 主循环中系统 prompt 已注入 skills 摘要（name + when_to_use）
- LLM 遇到匹配场景时调本工具获取完整步骤
- scope 必须等于调用 agent 的 role，防止越界读其他 agent 的 skill
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Optional

from agent_tools import registry
from agent_tools.registry import ToolContext


# ── Per-task 读取预算熔断 ──────────────────────────────────────────────────
# 防止 LLM 在一个 task 内无节制调 skills.read（设计参见
# openspec/changes/add-skill-read-budget/design.md）。
#
# Key: task_id（AgentTask.task_id），由 AgentBase.run() 的 finally 清理。
_BUDGET_COUNTERS: dict[str, int] = defaultdict(int)


def clear_budget(task_id: str) -> None:
    """安全清空指定 task 的读取预算计数器。"""
    _BUDGET_COUNTERS.pop(task_id, None)


_SETTINGS_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.json"


def _load_budget() -> int:
    """从 settings.json 读取 skill_read_budget，缺省返回 2。"""
    try:
        if _SETTINGS_PATH.exists():
            data = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
            val = data.get("skill_read_budget", 2)
            return int(val) if val is not None else 2
    except Exception:
        pass
    return 2


# ── 熔断返回体（字面量冻结，参见 design.md §3）────────────────────────────
_BUDGET_EXHAUSTED_RESPONSE = {
    "ok": False,
    "error": (
        "🚨 [SKILL_BUDGET_EXHAUSTED] 本任务的技能读取额度已耗尽。"
        "严禁更换参数或重复尝试本工具调用。"
        "请直接综合已读取的方法论输出最终答案（AgentResult.success）。"
    ),
}


def _check_scope(agent_scope: str, requested_scope: str) -> bool:
    """检查请求的 scope 是否 = agent 自身 role。"""
    return agent_scope == requested_scope


def _load_skills_source() -> str:
    """从 settings.json 读取 skills_source，缺省返回 'files'。"""
    try:
        if _SETTINGS_PATH.exists():
            return json.loads(_SETTINGS_PATH.read_text(encoding="utf-8")).get("skills_source", "files")
    except Exception:
        pass
    return "files"


def _hub_read_skill(args: dict, ctx: ToolContext, agent_role: str) -> dict:
    """Hub 模式：从 backend equipment 加载 skill，支持 skill_id 或 name。"""
    skill_id = args.get("skill_id", "")
    name = args.get("name", "")
    if not skill_id and not name:
        return {"ok": False, "error": "at least one of skill_id or name is required"}

    backend = ctx.storage
    if backend is None:
        return {"ok": False, "error": "no storage backend in context for hub mode"}

    try:
        equipped = backend.list_equipment(ctx.tenant_id, agent_role)
    except Exception as e:
        return {"ok": False, "error": f"failed to list equipment: {e}"}

    # Resolve: skill_id first, then exact name
    matched = None
    if skill_id:
        for s in equipped:
            if s.get("id") == skill_id:
                matched = s
                break
    if matched is None and name:
        for s in equipped:
            if s.get("name") == name:
                matched = s
                break

    if matched is None:
        return {
            "ok": False,
            "error": (
                f"skill '{name or skill_id}' is not equipped for role '{agent_role}'. "
                f"Available skills for this role are shown in 'Available Skills'."
            ),
        }

    # Fetch full body
    resolved_id = matched["id"]
    try:
        skill = backend.get_skill(resolved_id, ctx.tenant_id)
    except KeyError:
        return {"ok": False, "error": f"skill with id '{resolved_id}' not found in database"}
    except Exception as e:
        return {"ok": False, "error": f"failed to get skill: {e}"}

    content = skill.get("body", "")
    if not content:
        return {"ok": False, "error": f"skill '{skill.get('name', resolved_id)}' has empty body"}

    # ── 预算检查 ────────────────────────────────────────────────────
    task_id = ctx.task_id or (ctx.extra or {}).get("task_id", "")
    if task_id:
        budget = _load_budget()
        if _BUDGET_COUNTERS[task_id] >= budget:
            return dict(_BUDGET_EXHAUSTED_RESPONSE)
        _BUDGET_COUNTERS[task_id] += 1

    return {"ok": True, "data": {"content": content}}


def _read_skill_handler(args: dict, ctx: ToolContext) -> dict:
    scope = args.get("scope", "")
    name = args.get("name", "")
    skill_id = args.get("skill_id", "")
    agent_role = (ctx.extra or {}).get("agent_role", "")

    # ── 前置校验（均不触发计数器）────────────────────────────────────
    if not scope:
        return {"ok": False, "error": "scope is required"}
    if not name and not skill_id:
        return {"ok": False, "error": "at least one of name or skill_id is required"}

    if not _check_scope(agent_role, scope):
        return {
            "ok": False,
            "error": f"scope mismatch: agent '{agent_role}' cannot read scope '{scope}'",
        }

    # ── Hub 模式 ─────────────────────────────────────────────────────
    if _load_skills_source() == "hub":
        return _hub_read_skill(args, ctx, agent_role)

    # ── File 模式（默认/旧） ─────────────────────────────────────────
    if not name:
        return {"ok": False, "error": "name is required in file mode"}

    mem = (ctx.extra or {}).get("memory")
    if mem is None:
        return {"ok": False, "error": "no memory layer in context"}

    content = mem.read_skill(ctx.tenant_id, scope, name)
    if content is None:
        return {"ok": False, "error": f"skill '{name}' not found in scope '{scope}'"}

    # ── 预算检查（仅在有效 content 返回后触发）──────────────────────
    task_id = ctx.task_id or (ctx.extra or {}).get("task_id", "")
    if task_id:
        budget = _load_budget()
        if _BUDGET_COUNTERS[task_id] >= budget:
            return dict(_BUDGET_EXHAUSTED_RESPONSE)
        _BUDGET_COUNTERS[task_id] += 1

    return {"ok": True, "data": {"content": content}}


VALID_SCOPES = ["intel", "content", "analyst"]

registry.register(
    name="skills.read",
    schema={
        "description": (
            "Inject a skill's methodology text into context, by name and scope. "
            "In hub mode, provide `skill_id` (preferred) or exact `name`."
        ),
        "parameters": {
            "type": "object",
            "required": ["scope"],
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": VALID_SCOPES,
                    "description": "Which agent's skills to read (must match your own role)",
                },
                "name": {
                    "type": "string",
                    "description": "Skill name (as listed in 'Available Skills') — required in file mode, optional in hub mode",
                },
                "skill_id": {
                    "type": "string",
                    "description": "Skill UUID (preferred in hub mode, from 'Available Skills' block)",
                },
            },
        },
    },
    handler=_read_skill_handler,
    description="Read a skill file by name and scope",
)
