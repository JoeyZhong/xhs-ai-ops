"""
线索雷达扫描流水线（lead-intent-radar V1 · Phase 1）。

scan_goal(): 一次完整扫描 = 采集 → 意图判定 → (合格) 首触草稿+校验 → 入库。
这是 scheduler（Phase 4）周期触发的入口，也是「采集→判定→草稿→入库」的编排中枢。

设计：
- 只编排，不实现细节——三步分别调注册好的 tool（collect.xhs_intent / intent.classify
  / outreach.draft），lead 落库走 storage.create_lead（signal_key 幂等去重）。
- 不合格信号丢弃（噪声过滤），不入库。
- 任一信号处理异常不影响其余（逐条 try）。
"""

from __future__ import annotations

from typing import Any, Optional

from agent_tools import registry
from agent_tools.registry import ToolContext


# 信源 → 采集工具映射（V2 扩源）。新增信源在此加一行即可，scan 流程不变。
_SOURCE_TOOLS: dict[str, str] = {
    "xhs": "collect.xhs_intent",
    "zhihu": "collect.zhihu_question",
    "zhubajie": "collect.zhubajie_demand",
}


def _goal(storage, tenant_id: str, goal_id: str) -> Optional[dict]:
    try:
        goals = storage.load_goals(tenant_id) or {}
        return next((g for g in goals.get("goals", []) if g.get("id") == goal_id), None)
    except Exception:
        return None


def scan_goal(tenant_id: str, goal_id: str, *, storage,
              limit_per_keyword: int = 20,
              min_match: int = 50,
              audit: Any = None) -> dict:
    """对一个 goal 跑一次完整雷达扫描，返回统计摘要。

    幂等：已存在（signal_key 命中）的 lead 由 create_lead 去重，不重复建。
    """
    goal = _goal(storage, tenant_id, goal_id)
    if not goal:
        return {"ok": False, "error": f"goal '{goal_id}' not found", "stats": {}}

    keywords = goal.get("keywords") or []
    persona_id = goal.get("persona_id") or ""
    if not keywords:
        return {"ok": False, "error": "goal 无监控关键词（keywords 为空）", "stats": {}}

    # 信源（V2）：goal.lead_sources 决定从哪些池子捞，缺省 ["xhs"] 向后兼容 V1。
    sources = goal.get("lead_sources") or ["xhs"]
    sources = [s for s in sources if s in _SOURCE_TOOLS] or ["xhs"]

    ctx = ToolContext(tenant_id=tenant_id, storage=storage, audit=audit)

    stats = {"scanned": 0, "qualified": 0, "created": 0,
             "duplicate": 0, "noise": 0, "errors": 0,
             "by_source": {}}

    # 1. 多源采集 → 合并（signal_key 跨源去重，前缀含源天然唯一）
    signals: list[dict] = []
    seen_keys: set[str] = set()
    for src in sources:
        tool_name = _SOURCE_TOOLS[src]
        col = registry.invoke(tool_name,
                              {"keywords": keywords, "limit": limit_per_keyword}, ctx)
        src_signals = col.get("data", {}).get("signals", []) if col.get("ok") else []
        kept = 0
        for sig in src_signals:
            key = sig.get("signal_key")
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            signals.append(sig)
            kept += 1
        stats["by_source"][src] = {"scanned": kept}
    stats["scanned"] = len(signals)
    if not signals:
        return {"ok": True, "stats": stats, "created_lead_ids": []}

    created_leads: list[dict] = []
    for sig in signals:
        try:
            # 2. 意图判定
            j = registry.invoke("intent.classify",
                                {"text": sig["post_text"], "goal_id": goal_id,
                                 "min_match": min_match}, ctx)
            if not j.get("ok"):
                stats["errors"] += 1
                continue
            jd = j["data"]
            if not jd["qualified"]:
                stats["noise"] += 1
                continue
            stats["qualified"] += 1

            src = sig.get("source", "xhs")
            stats["by_source"].setdefault(src, {"scanned": 0})
            stats["by_source"][src]["qualified"] = \
                stats["by_source"][src].get("qualified", 0) + 1

            # 3. 首触草稿 + 校验（文体随信源）
            draft_text = ""
            lure_pass = dup_pass = False
            if persona_id:
                d = registry.invoke("outreach.draft",
                                    {"post_text": sig["post_text"],
                                     "persona_id": persona_id,
                                     "source": src,
                                     "trigger_type": jd.get("trigger_type") or ""}, ctx)
                if d.get("ok"):
                    dd = d["data"]
                    draft_text = dd["draft_text"]
                    lure_pass = dd["check_lure_pass"]
                    dup_pass = dd["check_dup_pass"]

            # 4. 入库（signal_key 幂等；猪八戒结构化 meta 透传）
            before = storage.list_leads(tenant_id, goal_id=goal_id, limit=1000)
            before_keys = {r.get("signal_key") for r in before}
            lead = storage.create_lead(
                tenant_id,
                signal_key=sig["signal_key"], goal_id=goal_id, persona_id=persona_id or None,
                source=src, source_url=sig.get("source_url"),
                author=sig.get("author"), posted_at=sig.get("posted_at"),
                post_text=sig.get("post_text"), excerpt=(sig.get("post_text") or "")[:32],
                match_score=jd.get("match_score"), trigger_type=jd.get("trigger_type"),
                judge_reason=jd.get("judge_reason"), draft_text=draft_text,
                check_lure_pass=lure_pass, check_dup_pass=dup_pass,
                lead_status="drafted" if draft_text else "qualified",
                meta=sig.get("meta"),
            )
            if lead["signal_key"] in before_keys:
                stats["duplicate"] += 1
            else:
                stats["created"] += 1
                stats["by_source"][src]["created"] = \
                    stats["by_source"][src].get("created", 0) + 1
                created_leads.append(lead)
        except Exception:
            stats["errors"] += 1
            continue

    return {"ok": True, "stats": stats,
            "created_lead_ids": [l["lead_id"] for l in created_leads]}
