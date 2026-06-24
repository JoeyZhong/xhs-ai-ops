"""聚合 strategy/generate prompt 所需的全部上下文。

所有 /api/v1/content/{strategy,generate} 的 prompt 拼装统一从这里取，
避免把 funnel 路由、overall_strategy 取值、packaging 注入散到多个端点里。
"""
from __future__ import annotations

from typing import Any

from agent_tools.packaging_rules import load_packaging_rules
from agents.playbook_learning import extract_auto_block

_FUNNEL_FIELD = {
    "traffic": "top_30pct",
    "trust": "mid_40pct",
    "conversion": "bottom_30pct",
}


def build_strategy_prompt_context(
    *,
    backend: Any,
    tenant_id: str,
    goal: dict,
    topic_id: str | None,
) -> dict[str, Any]:
    """返回 dict: funnel_stage / funnel_strategy_text / core_message / packaging_rules.

    - topic_id 传了但取不到 → 静默回退为无 funnel 模式（不抛错，避免选题被删时 strategy 端点连锁挂掉）
    - overall_strategy 缺失 → core_message='', funnel_strategy_text=''
    - 始终返回 packaging_rules，即便其他都缺
    """
    funnel_stage: str | None = None
    funnel_strategy_text = ""

    if topic_id:
        try:
            topic = backend.get_topic(tenant_id, topic_id)
            funnel_stage = topic.get("funnel_stage") or None
        except (KeyError, Exception):
            funnel_stage = None

    ovs = goal.get("overall_strategy") or {}
    funnel = ovs.get("content_funnel") or {}
    if funnel_stage and funnel_stage in _FUNNEL_FIELD:
        val = funnel.get(_FUNNEL_FIELD[funnel_stage])
        if isinstance(val, str):
            funnel_strategy_text = val

    evidence_refs: list[dict] = []
    if funnel_stage:
        try:
            evidence_refs = backend.list_evidence(
                tenant_id,
                funnel_stage=funnel_stage,
                limit=3,
            )
        except Exception:
            evidence_refs = []

    # P3.4: 读 playbook 自动区（AnalystEvaluator 写的已验证爆款规律），缺失则空
    playbook_summary = ""
    try:
        pb = backend.load_memory(tenant_id, "content", "playbook.md")
        playbook_summary = extract_auto_block(pb)
    except Exception:
        playbook_summary = ""

    return {
        "funnel_stage": funnel_stage,
        "funnel_strategy_text": funnel_strategy_text,
        "core_message": ovs.get("core_message", "") if isinstance(ovs, dict) else "",
        "packaging_rules": load_packaging_rules(),
        "evidence_refs": evidence_refs,
        "playbook_summary": playbook_summary,
    }
