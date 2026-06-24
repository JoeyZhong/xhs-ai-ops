"""
Memory 操作工具（Phase 3 新增）。

为 AnalystAgent 提供把分析结论沉淀到 content/playbook.md 的能力。
基于 MemoryLayer.add_entry / replace_entry / remove_entry 实现，
权限由 _WRITE_PERMISSIONS 矩阵控制（analyst → content 是允许的）。
"""

from __future__ import annotations

from typing import Optional

from agent_tools import registry
from agent_tools.registry import ToolContext
from agents.memory import WriteConflictError


# ── 共享 Memory 实例获取（lazy） ─────────────────────────────────────────

def _get_memory_layer(ctx: ToolContext):
    """
    优先从 ctx.extra 读取 memory（Master 主流程注入），
    否则按 ctx.storage 现场构造一个（独立调用兼容）。
    """
    mem = ctx.extra.get("memory") if ctx.extra else None
    if mem is not None:
        return mem
    if ctx.storage is None:
        raise RuntimeError("memory tool requires ctx.storage or ctx.extra['memory']")
    from agents.memory import MemoryLayer
    return MemoryLayer(storage=ctx.storage)


# ── write_playbook_entry handler ────────────────────────────────────────

def _replace_with_retry(mem, tenant_id, scope, file,
                         entry_id, content, agent_role,
                         entry_meta: Optional[dict] = None,
                         max_retries: int = 3) -> str:
    """
    OCC 语义：先 read_entry 拿 rev，再 replace_entry(expected_rev=...)。
    冲突时重读重写，最多 max_retries 次。
    """
    for attempt in range(max_retries):
        read_result = mem.read_entry(tenant_id, scope, file, entry_id)
        if read_result is None:
            expected = None
        else:
            _, expected = read_result
        try:
            return mem.replace_entry(
                tenant_id, scope, file, entry_id, content, agent_role,
                expected_rev=expected, entry_meta=entry_meta,
            )
        except WriteConflictError:
            if attempt == max_retries - 1:
                raise
            continue
    return "replaced"  # 不会到达


def _write_playbook_entry_handler(args: dict, ctx: ToolContext) -> dict:
    op = args["op"]
    entry_id = args["entry_id"]
    content = args.get("content", "")
    status = args.get("status", "active")
    source = args.get("source", "manual")
    confidence = args.get("confidence", "high")
    agent_role = (ctx.extra or {}).get("agent_role", "analyst")
    file = "playbook.md"
    scope = "content"

    valid_statuses = ("active", "draft", "rejected")
    valid_sources = ("manual", "scheduler")
    valid_confidence = ("high", "low")
    if status not in valid_statuses:
        return {"ok": False, "error": f"invalid status '{status}', must be one of {valid_statuses}"}
    if source not in valid_sources:
        return {"ok": False, "error": f"invalid source '{source}', must be one of {valid_sources}"}
    if confidence not in valid_confidence:
        return {"ok": False, "error": f"invalid confidence '{confidence}', must be one of {valid_confidence}"}

    mem = _get_memory_layer(ctx)
    meta = {"status": status, "source": source, "confidence": confidence}

    try:
        if op == "add":
            if not content.strip():
                return {"ok": False, "error": "content is required for op=add"}
            result_op = mem.add_entry(ctx.tenant_id, scope, file,
                                        entry_id, content, agent_role,
                                        entry_meta=meta)
        elif op == "replace":
            if not content.strip():
                return {"ok": False, "error": "content is required for op=replace"}
            result_op = _replace_with_retry(
                mem, ctx.tenant_id, scope, file,
                entry_id, content, agent_role,
                entry_meta=meta,
                max_retries=3,
            )
        elif op == "remove":
            result_op = mem.remove_entry(ctx.tenant_id, scope, file,
                                           entry_id, agent_role)
        else:
            return {"ok": False, "error": f"unknown op: {op}"}
    except ValueError as e:
        return {"ok": False, "error": f"ValueError: {e}"}
    except WriteConflictError as e:
        return {"ok": False, "error": f"WriteConflict: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # 读回当前 entries 摘要（方便 LLM 确认）
    entries_after = mem.list_entries(ctx.tenant_id, scope, file)
    return {
        "ok": True,
        "data": {
            "op": result_op,
            "entry_id": entry_id,
            "total_entries": len(entries_after),
            "all_entry_ids": list(entries_after.keys()),
        },
    }


# ── 注册 ─────────────────────────────────────────────────────────────────

registry.register(
    name="memory.write_playbook_entry",
    schema={
        "description": (
            "Write/update/delete an entry in content/playbook.md. "
            "Use this AFTER completing performance analysis to persist actionable "
            "insights so the next Content Agent session reads them. "
            "entry_id should be stable and meaningful (e.g. 'ces-pattern-202604', "
            "'time-slot-202604'). Body should be ≤80 chars, concrete, no fluff."
        ),
        "parameters": {
            "type": "object",
            "required": ["op", "entry_id"],
            "properties": {
                "op": {
                    "type": "string",
                    "enum": ["add", "replace", "remove"],
                    "description": ("add: fail if entry exists; "
                                     "replace: overwrite or create; "
                                     "remove: delete entry, no-op if missing"),
                },
                "entry_id": {
                    "type": "string",
                    "description": "Stable unique id, lowercase-with-dashes",
                },
                "content": {
                    "type": "string",
                    "description": "Entry body. Required for add/replace, ignored for remove.",
                },
                "status": {
                    "type": "string",
                    "enum": ["active", "draft", "rejected"],
                    "description": "Review status (default: active). scheduler uses draft.",
                },
                "source": {
                    "type": "string",
                    "enum": ["manual", "scheduler"],
                    "description": "Origin of the entry (default: manual).",
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "low"],
                    "description": "Data confidence (default: high). low when perf data missing.",
                },
            },
        },
    },
    handler=_write_playbook_entry_handler,
    description="Persist analyst insight into content playbook (feedback loop)",
)
