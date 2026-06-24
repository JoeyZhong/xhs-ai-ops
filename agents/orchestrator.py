"""Orchestrator 主助手 service（V1.3 MVP）。

对话 → 计划 → 决策卡片 → 确认 → 复用 HermesMaster.submit_dag 执行。

设计原则（见 openspec/changes/orchestrator-mvp/design.md）：
  - **包装而非重写**：复用 planner.plan_from_intent + HermesMaster.submit_dag，零改调度内核。
  - 本模块不直接调 Tool、不直接跑 Agent；执行一律经 submit_dag（保留 ToolPolicy/AuditLogger）。
  - 不是 AgentBase 子类（PRD §313）。

P2：会话经 storage backend 落库（orchestrator_sessions 表 / sidecar），
刷新可恢复；OCC rev 防并发丢更新。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from typing import Any, Callable, Optional

from agents.planner import PlannerError, plan_from_intent
from agents.task_ledger import TaskNode

_HIGH_RISK_KEYWORDS = ("发布", "上线", "对外", "群发", "删除", "publish", "post")


def _new_session_id() -> str:
    return f"os-{uuid.uuid4().hex[:8]}"


def get_session(backend: Any, tenant_id: str,
                session_id: Optional[str]) -> Optional[dict]:
    """租户隔离的会话取回（backend 已按 tenant 隔离，None = 不存在/跨租户）。"""
    if not session_id:
        return None
    return backend.get_session(tenant_id, session_id)


# ── goal 上下文 → planner methodology ──────────────────────────────────────

def _find_goal(backend: Any, tenant_id: str, goal_id: Optional[str]) -> Optional[dict]:
    if not goal_id:
        return None
    data = backend.load_goals(tenant_id)
    return next((g for g in data.get("goals", []) if g.get("id") == goal_id), None)


def _audience_text(ta: Any) -> str:
    """target_audience 可能是 str 或 dict，压成一行人话。"""
    if not ta:
        return ""
    if isinstance(ta, str):
        return ta.strip()
    if isinstance(ta, dict):
        bits: list[str] = []
        for k in ("description", "who", "segments", "demographics", "pain_points"):
            v = ta.get(k)
            if v:
                bits.append(v if isinstance(v, str)
                            else json.dumps(v, ensure_ascii=False))
        return "；".join(bits) or json.dumps(ta, ensure_ascii=False)[:300]
    return str(ta)


def _goal_methodology(goal: dict) -> str:
    """把 goal 压成主 Agent / planner 的运营上下文摘要。

    先放基础身份信息（名称/目标/受众/定位/关键词）——即使目标只填了名字，
    也要让主 Agent 知道在为哪个业务服务，不再因上下文为空而反问行业；
    再叠加高级策略（核心信息/漏斗/已验证或沉底角度，老目标才有）。
    """
    parts: list[str] = []

    name = (goal.get("name") or "").strip()
    if name:
        parts.append(f"运营目标：{name}")
    obj = (goal.get("objective") or "").strip()
    if obj:
        parts.append(f"目标说明：{obj}")
    desc = (goal.get("description") or "").strip()
    if desc and desc != obj:
        parts.append(f"补充描述：{desc}")
    audience = _audience_text(goal.get("target_audience"))
    if audience:
        parts.append(f"目标受众：{audience}")
    bp = (goal.get("brand_position") or "").strip()
    if bp:
        parts.append(f"品牌定位：{bp}")
    kws = [str(k).strip() for k in (goal.get("keywords") or []) if str(k).strip()]
    if kws:
        parts.append(f"当前关键词：{' / '.join(kws)}")

    ovs = goal.get("overall_strategy") or {}
    if ovs.get("core_message"):
        parts.append(f"品牌核心信息：{ovs['core_message']}")
    funnel = ovs.get("content_funnel") or {}
    if funnel:
        parts.append(f"内容漏斗策略：{json.dumps(funnel, ensure_ascii=False)}")
    angles = goal.get("used_angles") or []
    hits = [a.get("angle") for a in angles
            if isinstance(a, dict) and a.get("status") == "validated_hit"]
    sunk = [a.get("angle") for a in angles
            if isinstance(a, dict) and a.get("status") == "sunk"]
    if hits:
        parts.append(f"已验证爆款角度（优先复用）：{', '.join(filter(None, hits))}")
    if sunk:
        parts.append(f"已沉底角度（避免）：{', '.join(filter(None, sunk))}")
    return "\n".join(parts)


# ── 纯函数：理解 / 计划 / 卡片 / 解释 ───────────────────────────────────────

def understand_intent(message: str, goal: Optional[dict]) -> list[str]:
    """一轮必填检查，返回缺失项列表（空 = 信息充分）。"""
    missing: list[str] = []
    if goal is None:
        missing.append("goal_id")
    if not (message or "").strip():
        missing.append("message")
    return missing


def build_plan(message: str, goal: dict,
               provider: Optional[Callable[[str], str]] = None) -> list[TaskNode]:
    """复用 planner.plan_from_intent，注入 goal 上下文作 methodology。"""
    return plan_from_intent(message, provider=provider, methodology=_goal_methodology(goal))


def _is_high_risk(prompt: str) -> bool:
    return any(k in (prompt or "") for k in _HIGH_RISK_KEYWORDS)


def make_decision_cards(plan: list[TaskNode]) -> list[dict]:
    """MVP 两类卡片：整份 plan 采纳卡（必有）+ 高风险步骤卡（命中才有）。"""
    detail = " → ".join(f"{i + 1}.[{n.type}] {n.prompt[:24]}" for i, n in enumerate(plan))
    cards: list[dict] = [{
        "card_id": "dc-plan", "kind": "plan_approval",
        "title": "采纳这份计划？", "detail": detail,
        "options": ["approve", "reject"], "status": "pending",
    }]
    for n in plan:
        if _is_high_risk(n.prompt):
            cards.append({
                "card_id": f"dc-risk-{n.id}", "kind": "high_risk_step",
                "title": f"高风险步骤需确认：{n.id}", "detail": n.prompt[:80],
                "options": ["approve", "reject"], "status": "pending",
            })
    return cards


def explain_plan(plan: list[TaskNode], goal: dict,
                 llm: Optional[Callable[..., tuple]] = None) -> str:
    """把 plan 转人话。LLM 可达则用，否则规则化降级。"""
    rule_based = f"我把这个需求拆成 {len(plan)} 步：\n" + "\n".join(
        f"{i + 1}. [{n.type}] {n.prompt}" for i, n in enumerate(plan)
    )
    if llm is None:
        from agent_tools.kimi import call_kimi as llm  # call-time import，便于测试 monkeypatch
    try:
        steps = "\n".join(f"{i + 1}. [{n.type}] {n.prompt}" for i, n in enumerate(plan))
        prompt = ("你是运营总监助手。用 2-3 句话向运营人解释下面这份任务计划要做什么、"
                  "为什么这么安排；不要逐条复述、不要输出 JSON：\n" + steps)
        content, err = llm(prompt, max_tokens=300, temperature=0.4)
        if err or not content or not content.strip():
            return rule_based
        return content.strip()
    except Exception:
        return rule_based


# ── 会话状态机 ──────────────────────────────────────────────────────────────

def converse(*, backend: Any, tenant_id: str, message: str,
             goal_id: Optional[str] = None, session_id: Optional[str] = None,
             provider: Optional[Callable[[str], str]] = None,
             llm: Optional[Callable[..., tuple]] = None) -> dict:
    """对话主入口：理解 → （追问 | 出计划+卡片）。会话状态落库。"""
    sess = get_session(backend, tenant_id, session_id)
    if sess is None:
        sess = backend.create_session(
            tenant_id, session_id=_new_session_id(), goal_id=goal_id,
            status="gathering", messages=[], proposed_plan=[],
            decision_cards=[], dag_id=None)

    goal_id_eff = goal_id or sess.get("goal_id")
    messages = list(sess.get("messages") or [])
    messages.append({"role": "user", "text": message})

    goal = _find_goal(backend, tenant_id, goal_id_eff)
    missing = understand_intent(message, goal)
    if missing:
        label = {"goal_id": "要针对哪个运营目标", "message": "你想做什么"}
        reply = "请补充：" + "、".join(label.get(m, m) for m in missing)
        messages.append({"role": "orchestrator", "text": reply})
        backend.update_session(tenant_id, sess["session_id"], expected_rev=sess["rev"],
                               goal_id=goal_id_eff, status="gathering", messages=messages)
        return {"session_id": sess["session_id"], "status": "gathering",
                "reply": reply, "missing": missing}

    try:
        plan = build_plan(message, goal, provider=provider)
    except PlannerError as e:
        reply = f"我没能把这个需求拆成可执行计划（{e}）。换个说法或补充细节再试？"
        messages.append({"role": "orchestrator", "text": reply})
        backend.update_session(tenant_id, sess["session_id"], expected_rev=sess["rev"],
                               goal_id=goal_id_eff, status="gathering", messages=messages)
        return {"session_id": sess["session_id"], "status": "gathering",
                "reply": reply, "missing": []}

    cards = make_decision_cards(plan)
    reply = explain_plan(plan, goal, llm=llm)
    plan_dicts = [asdict(n) for n in plan]
    messages.append({"role": "orchestrator", "text": reply})
    backend.update_session(tenant_id, sess["session_id"], expected_rev=sess["rev"],
                           goal_id=goal_id_eff, status="planned", messages=messages,
                           proposed_plan=plan_dicts, decision_cards=cards)
    return {"session_id": sess["session_id"], "status": "planned", "reply": reply,
            "proposed_plan": plan_dicts, "decision_cards": cards}


def plan_nodes(session: dict) -> list[TaskNode]:
    """session.proposed_plan(dict) → list[TaskNode]，供 submit_dag。"""
    return [TaskNode(id=n["id"], type=n["type"], prompt=n["prompt"],
                     blocked_by=list(n.get("blocked_by", [])))
            for n in session.get("proposed_plan", [])]


def _approve_plan_cards(cards: list[dict], approved: bool) -> list[dict]:
    """把 plan_approval 卡标为 approved/rejected，返回新列表。"""
    out = [dict(c) for c in cards]
    for c in out:
        if c.get("kind") == "plan_approval":
            c["status"] = "approved" if approved else "rejected"
    return out


def mark_dispatched(backend: Any, tenant_id: str, session: dict, dag_id: str) -> dict:
    cards = _approve_plan_cards(session.get("decision_cards") or [], True)
    return backend.update_session(
        tenant_id, session["session_id"], expected_rev=session["rev"],
        status="dispatched", dag_id=dag_id, decision_cards=cards)


def mark_cancelled(backend: Any, tenant_id: str, session: dict) -> dict:
    cards = _approve_plan_cards(session.get("decision_cards") or [], False)
    return backend.update_session(
        tenant_id, session["session_id"], expected_rev=session["rev"],
        status="cancelled", decision_cards=cards)


def set_card_decision(backend: Any, tenant_id: str, session: dict,
                      card_id: str, decision: str) -> Optional[dict]:
    """更新某张决策卡片状态并落库。命中返回更新后的 session，未命中返回 None。"""
    cards = [dict(c) for c in (session.get("decision_cards") or [])]
    hit = False
    for c in cards:
        if c.get("card_id") == card_id:
            c["status"] = "approved" if decision == "approve" else "rejected"
            hit = True
            break
    if not hit:
        return None
    return backend.update_session(
        tenant_id, session["session_id"], expected_rev=session["rev"],
        decision_cards=cards)
