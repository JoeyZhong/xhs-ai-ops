"""
意图分类 Tool（lead-intent-radar V1）。

把一段原帖文本判定为：
  - is_intent     是否真实「求购/求服务」意图（vs 同行/广告/闲聊）
  - match_score   与目标客户画像的匹配度 0-100
  - trigger_type  触发事件类型（loan|bid|hitech|foreign|cancel|None）
  - judge_reason  一句话判定理由
  - qualified     是否合格线索（is_intent 且 match_score ≥ 阈值）→ 决定是否入收件箱

设计：
- 通用横向：画像上下文从 goal 派生（service/audience/brand），不硬编码「审计」。
- trigger 词表 V1 为审计 5 类（贷款/投标/高新/外资/注销），可后续参数化。
- 解析逻辑（_parse_classification）是纯函数，无 LLM，可独立单测；
  handler 调 LLM(json_mode, 低温) 后喂给它，做防御性 clamp/normalize。
"""

from __future__ import annotations

import json
from typing import Any, Optional

from agent_tools import registry
from agent_tools.registry import ToolContext
from agent_tools.kimi import call_kimi


# ── 触发事件词表（V1 审计场景，可后续参数化为横向）─────────────────────────
TRIGGER_TYPES: dict[str, str] = {
    "loan":    "银行贷款/融资（要审计报告给银行）",
    "bid":     "招投标（标书要求审计报告）",
    "hitech":  "高新认定/研发费加计扣除（专项审计）",
    "foreign": "外资企业年审（法定审计）",
    "cancel":  "注销/清算（清算审计报告）",
}
_VALID_TRIGGERS = set(TRIGGER_TYPES.keys())

DEFAULT_MIN_MATCH = 50   # 合格阈值：低于此判为噪声，不入收件箱


# ── 纯解析（无 LLM，可单测）──────────────────────────────────────────────

def _parse_classification(raw: str, *, min_match: int = DEFAULT_MIN_MATCH) -> dict:
    """把 LLM 的 JSON 字符串防御性解析成标准结构。

    任何字段缺失/越界/类型错 → 安全降级（保守判为不合格），绝不抛异常。
    """
    obj: dict[str, Any] = {}
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                obj = parsed
        except (json.JSONDecodeError, ValueError):
            obj = {}

    # is_intent → bool
    is_intent = bool(obj.get("is_intent", False))

    # match_score → int 0..100
    raw_score = obj.get("match_score", 0)
    try:
        match_score = int(round(float(raw_score)))
    except (TypeError, ValueError):
        match_score = 0
    match_score = max(0, min(100, match_score))

    # trigger_type → 词表内或 None
    trig = obj.get("trigger_type")
    if isinstance(trig, str):
        trig = trig.strip().lower()
    trigger_type = trig if trig in _VALID_TRIGGERS else None

    # judge_reason → str（截断防超长）
    reason = obj.get("judge_reason") or obj.get("reason") or ""
    if not isinstance(reason, str):
        reason = str(reason)
    reason = reason.strip()[:300]

    qualified = is_intent and match_score >= min_match

    return {
        "is_intent": is_intent,
        "match_score": match_score,
        "trigger_type": trigger_type,
        "judge_reason": reason,
        "qualified": qualified,
    }


# ── Prompt 组装 ────────────────────────────────────────────────────────────

def _build_profile_context(ctx: ToolContext, goal_id: str,
                           service_hint: str) -> str:
    """从 goal 派生「我们提供什么服务 + 目标客户画像」，喂进判定 prompt。"""
    parts: list[str] = []
    if service_hint:
        parts.append(f"我们提供的服务：{service_hint}")
    if goal_id and getattr(ctx, "storage", None):
        try:
            goals = ctx.storage.load_goals(ctx.tenant_id) or {}
            g = next((x for x in goals.get("goals", []) if x.get("id") == goal_id), None)
            if g:
                if g.get("description"):
                    parts.append(f"获客目标：{g['description']}")
                aud = g.get("target_audience") or {}
                if aud.get("who"):
                    parts.append(f"目标客户：{aud['who']}")
                if aud.get("pain_points"):
                    parts.append(f"客户痛点：{aud['pain_points']}")
                if g.get("brand_position"):
                    parts.append(f"我们的定位：{g['brand_position']}")
        except Exception:
            pass
    if not parts:
        parts.append("我们提供正规会计师事务所的审计报告服务，面向有审计报告需求的中小企业。")
    return "\n".join(parts)


def _build_prompt(text: str, profile_ctx: str) -> str:
    trig_lines = "\n".join(f"  - {code}：{label}" for code, label in TRIGGER_TYPES.items())
    return (
        f"{profile_ctx}\n\n"
        f"下面是一条社交平台帖子的原文，判断发帖人是否是我们的潜在客户：\n"
        f"——————\n{text}\n——————\n\n"
        f"请判断并**只输出 JSON**（不要解释、不要 markdown）：\n"
        f'{{\n'
        f'  "is_intent": true/false,   // 是否在主动求购/求此项服务（同行、广告、单纯科普、招聘都算 false）\n'
        f'  "match_score": 0-100,      // 与上述目标客户画像的匹配度\n'
        f'  "trigger_type": "代码",     // 触发场景，从下列选一个，无法判断填 null：\n'
        f'{trig_lines}\n'
        f'  "judge_reason": "一句话理由"\n'
        f'}}'
    )


# ── Tool handler ───────────────────────────────────────────────────────────

def _classify_handler(args: dict, ctx: ToolContext) -> dict:
    text = (args.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "text is required (待判定的帖子原文)"}

    min_match = int(args.get("min_match", DEFAULT_MIN_MATCH))
    profile_ctx = _build_profile_context(
        ctx, args.get("goal_id", ""), args.get("service_hint", ""))
    prompt = _build_prompt(text, profile_ctx)

    content, err = call_kimi(
        prompt=prompt,
        system="你是 B2B 销售线索判定专家，只输出 JSON，判断要严格、保守。",
        max_tokens=300,
        json_mode=True,
        temperature=0.2,
    )
    if err:
        return {"ok": False, "error": err}

    result = _parse_classification(content, min_match=min_match)
    return {"ok": True, "data": result}


# ── 注册 ─────────────────────────────────────────────────────────────────

registry.register(
    name="intent.classify",
    schema={
        "description": (
            "判定一段帖子原文是否为目标客户的真实求购意图，返回 "
            "{is_intent, match_score, trigger_type, judge_reason, qualified}。"
            "用于线索雷达的噪声过滤与排序。"
        ),
        "parameters": {
            "type": "object",
            "required": ["text"],
            "properties": {
                "text":         {"type": "string", "description": "待判定的帖子原文"},
                "goal_id":      {"type": "string", "description": "关联获客目标，用于派生客户画像"},
                "service_hint": {"type": "string", "description": "可选：直接给出服务描述，覆盖 goal 派生"},
                "min_match":    {"type": "integer", "minimum": 0, "maximum": 100,
                                 "description": f"合格阈值，默认 {DEFAULT_MIN_MATCH}"},
            },
        },
    },
    handler=_classify_handler,
    cost_estimate=300.0,
    description="意图资格判定（求购/匹配度/触发类型）",
)
