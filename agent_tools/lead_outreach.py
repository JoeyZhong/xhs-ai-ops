"""
首触草稿 + 校验器（lead-intent-radar V1）。

- outreach.draft：针对一条原帖，按人设口吻生成「短首触回复」，并跑两道校验：
    · 引流词校验（check_lure_words）：微信/电话/加我/私信等命中即不通过
    · 雷同度校验（check_similarity）：与近期已发首触过于相似即不通过（防风控判刷屏）
  返回 {draft_text, check_lure_pass, check_dup_pass, lure_hits, dup_ratio}。

两道校验是纯函数（无 LLM、无 IO），可独立单测。
首触是「短回复」，不是整篇笔记——与 content_gen 区分。
"""

from __future__ import annotations

import json
import re
import difflib
from pathlib import Path
from typing import Optional

from agent_tools import registry
from agent_tools.registry import ToolContext
from agent_tools.kimi import call_kimi


CONFIG_DIR = Path(__file__).parent.parent / "config"

# ── 触发场景标签（与 intent_classifier 对齐）────────────────────────────────
TRIGGER_LABELS: dict[str, str] = {
    "loan":    "银行贷款/融资",
    "bid":     "招投标",
    "hitech":  "高新认定/研发加计",
    "foreign": "外资企业年审",
    "cancel":  "注销/清算",
}


# ── 引流词校验（纯函数）─────────────────────────────────────────────────────
# 命中任意一条 = 不通过。覆盖：社交账号引流 + 联系方式 + 明示导流话术。
_LURE_PATTERNS: list[tuple[str, str]] = [
    (r"微信",                         "微信"),
    (r"\b(wechat|weixin)\b",          "wechat"),
    (r"(?<![a-z])v\s*[x信]",          "vx/v信"),
    (r"\b(qq|扣扣|企鹅)\b",            "QQ"),
    (r"电话|手机号|手机|来电|拨打",      "电话"),
    (r"私信我|私我|滴我|扣我|联系我|找我", "导流话术"),
    (r"加我|加个|加一下|加微|加好友",     "加好友"),
    (r"联系方式|留个联系|留言区|评论区扣", "留联系方式"),
    (r"1[3-9]\d{9}",                  "手机号(11位)"),
    (r"\d{3,4}[\-\s]?\d{7,8}",        "座机号"),
]
_LURE_COMPILED = [(re.compile(p, re.IGNORECASE), label) for p, label in _LURE_PATTERNS]


def check_lure_words(text: str) -> tuple[bool, list[str]]:
    """返回 (是否通过, 命中词列表)。通过=没命中任何引流词。"""
    if not text:
        return True, []
    hits: list[str] = []
    for rx, label in _LURE_COMPILED:
        if rx.search(text):
            hits.append(label)
    # 去重保序
    seen: set[str] = set()
    uniq = [h for h in hits if not (h in seen or seen.add(h))]
    return (len(uniq) == 0), uniq


def check_similarity(text: str, priors: list[str],
                     threshold: float = 0.82) -> tuple[bool, float]:
    """与历史首触比相似度。返回 (是否通过, 最大相似度)。
    通过 = 最大相似度 < threshold（不与任何历史首触过于雷同）。
    """
    if not text or not priors:
        return True, 0.0
    max_ratio = 0.0
    for p in priors:
        if not p:
            continue
        ratio = difflib.SequenceMatcher(None, text, p).ratio()
        if ratio > max_ratio:
            max_ratio = ratio
    return (max_ratio < threshold), round(max_ratio, 3)


# ── 人设加载 ────────────────────────────────────────────────────────────────

def _load_persona_prompt(persona_id: str) -> str:
    """从 config/personas.json 按 id 取 system_prompt。"""
    if not persona_id:
        return ""
    path = CONFIG_DIR / "personas.json"
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        for p in data.get("personas", []):
            if p.get("id") == persona_id:
                return p.get("system_prompt", "") or ""
    except Exception:
        pass
    return ""


# ── 草稿生成 ────────────────────────────────────────────────────────────────

# ── 文体随信源（persona 不变，prompt 模板变）────────────────────────────────
# 共享红线（三源通用）：无引流词、不写死时效、纯文本输出。
_DRAFT_BODY = {
    "xhs": (
        "请以你的人设口吻，写**一条**简短的首次回复（首触），目标是让对方觉得你专业靠谱、愿意继续聊。硬性要求：\n"
        "1. 只写正文，80-130 字，像专业同行在评论区的随手真实回复，不要标题、不要分点、不要表情符号。\n"
        "2. 先点出该场景下的一两个关键点（让对方感到你懂行），再用一个简短问题把话头递回去。\n"
    ),
    "zhihu": (
        "请以你的人设口吻，写**一条**知乎风格的专业回答（首触），先输出干货建立信任，再自然留口子。硬性要求：\n"
        "1. 250-400 字，条理清楚（可用短句分层，但不要堆砌符号/表情），像懂行的人认真答题。\n"
        "2. 针对该场景把关键流程/要点讲透一两段，让对方读完觉得你确实专业，结尾用一个简短问题把话头递回去。\n"
    ),
    "zhubajie": (
        "请以你的人设口吻，写**一条**猪八戒接单语境下的报价话术（首触），直给、贴合发单人想快速比价选人的心理。硬性要求：\n"
        "1. 120-200 字，直接说明：能做这类报告、需要哪些基础资料、大概交付节奏、价位档（价格实在，可点到区间但不要把死价钉死）。\n"
        "2. 像正规事务所接单的专业回应，不要标题、不要分点符号、不要表情符号，结尾用一个简短问题确认需求把话头递回去。\n"
    ),
}
_DRAFT_COMMON_RULES = (
    "3. 绝对不能出现：微信、电话、手机号、QQ、'加我'、'私信我'、'联系方式'等任何引流词。\n"
    "4. 不要把出报告时间写死（如'两天'），可含蓄表达可加急。\n"
    "5. 只输出这条回复的纯文本，不要任何解释或引号。"
)
_VALID_SOURCES = set(_DRAFT_BODY.keys())


def _build_draft_prompt(post_text: str, trigger_type: Optional[str],
                        source: str = "xhs") -> str:
    src = source if source in _VALID_SOURCES else "xhs"
    trig = TRIGGER_LABELS.get(trigger_type or "", "")
    trig_line = f"\n（该客户的场景判定为：{trig}）" if trig else ""
    src_label = {"xhs": "小红书帖子", "zhihu": "知乎提问", "zhubajie": "猪八戒需求单"}[src]
    return (
        f"下面是一位潜在客户在{src_label}上公开发布的求助内容：\n"
        f"——————\n{post_text}\n——————{trig_line}\n\n"
        f"{_DRAFT_BODY[src]}"
        f"{_DRAFT_COMMON_RULES}"
    )


def _draft_handler(args: dict, ctx: ToolContext) -> dict:
    post_text = (args.get("post_text") or "").strip()
    if not post_text:
        return {"ok": False, "error": "post_text is required (原帖全文)"}

    system_prompt = args.get("system_prompt") or _load_persona_prompt(args.get("persona_id", ""))
    if not system_prompt:
        return {"ok": False, "error": "需要 system_prompt 或可解析的 persona_id"}

    trigger_type = args.get("trigger_type") or None
    source = (args.get("source") or "xhs").strip().lower()
    dup_threshold = float(args.get("dup_threshold", 0.82))

    raw, err = call_kimi(
        prompt=_build_draft_prompt(post_text, trigger_type, source),
        system=system_prompt,
        max_tokens=600,
        temperature=0.7,
    )
    if err:
        return {"ok": False, "error": err}

    draft_text = (raw or "").strip().strip('"').strip()

    # 校验
    lure_pass, lure_hits = check_lure_words(draft_text)

    priors = args.get("prior_drafts")
    if priors is None and getattr(ctx, "storage", None):
        try:
            recent = ctx.storage.list_leads(ctx.tenant_id, limit=50)
            priors = [r.get("draft_text") for r in recent if r.get("draft_text")]
        except Exception:
            priors = []
    dup_pass, dup_ratio = check_similarity(draft_text, priors or [], threshold=dup_threshold)

    return {
        "ok": True,
        "data": {
            "draft_text": draft_text,
            "check_lure_pass": lure_pass,
            "lure_hits": lure_hits,
            "check_dup_pass": dup_pass,
            "dup_ratio": dup_ratio,
            "sendable": lure_pass and dup_pass,
        },
    }


# ── 注册 ─────────────────────────────────────────────────────────────────

registry.register(
    name="outreach.draft",
    schema={
        "description": (
            "针对一条原帖按人设生成首触短回复，并跑引流词/雷同度校验。"
            "返回 {draft_text, check_lure_pass, lure_hits, check_dup_pass, dup_ratio, sendable}。"
        ),
        "parameters": {
            "type": "object",
            "required": ["post_text"],
            "properties": {
                "post_text":      {"type": "string", "description": "原帖全文"},
                "persona_id":     {"type": "string", "description": "人设 id（从 personas.json 取口吻）"},
                "system_prompt":  {"type": "string", "description": "可选：直接给人设 prompt，覆盖 persona_id"},
                "trigger_type":   {"type": "string", "description": "触发场景 loan|bid|hitech|foreign|cancel"},
                "source":         {"type": "string", "description": "信源 xhs|zhihu|zhubajie，决定草稿文体（默认 xhs 短回复）"},
                "prior_drafts":   {"type": "array", "items": {"type": "string"},
                                   "description": "可选：历史首触文本，用于查重；不传则从 leads 取近 50 条"},
                "dup_threshold":  {"type": "number", "minimum": 0.5, "maximum": 1.0,
                                   "description": "雷同阈值，默认 0.82"},
            },
        },
    },
    handler=_draft_handler,
    cost_estimate=400.0,
    description="首触草稿生成 + 引流词/雷同度校验",
)
