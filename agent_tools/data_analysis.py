"""
数据分析 Tool（新增）。

为 Analyst Agent 提供：
- compute_ces：CES 综合互动评分
- run_10_3_1_model：10-3-1 选题优化分析
- diagnose_traffic：流量诊断检查清单
"""

from __future__ import annotations

from typing import Any

from agent_tools import registry
from agent_tools.registry import ToolContext


# ── CES 计算（小红书算法权重） ─────────────────────────────────────────

CES_WEIGHTS = {
    "点赞": 1, "收藏": 1, "评论": 4, "分享": 4, "关注": 8,
}


def compute_ces_for_post(post: dict) -> int:
    """单篇 CES = Σ(行为数 × 权重)"""
    total = 0
    for k, w in CES_WEIGHTS.items():
        v = post.get(k, 0) or 0
        try:
            total += int(v) * w
        except Exception:
            pass
    return total


def _compute_ces_handler(args: dict, ctx: ToolContext) -> dict:
    posts = args["posts"]
    enriched = []
    for p in posts:
        ces = compute_ces_for_post(p)
        enriched.append({**p, "CES": ces})
    enriched.sort(key=lambda x: x["CES"], reverse=True)
    return {
        "ok": True,
        "data": {
            "posts_with_ces": enriched,
            "stats": {
                "total_posts": len(enriched),
                "max_ces":     enriched[0]["CES"] if enriched else 0,
                "avg_ces":     sum(p["CES"] for p in enriched) / len(enriched) if enriched else 0,
                "median_ces":  sorted([p["CES"] for p in enriched])[len(enriched)//2] if enriched else 0,
            },
        },
    }


# ── 10-3-1 模型 ──────────────────────────────────────────────────────────

def _run_10_3_1_handler(args: dict, ctx: ToolContext) -> dict:
    """
    分析帖子表现：
    - 找 Top 3 高 CES 帖子
    - 提取共性（角度、标题钩子、时段）
    - 给出爆款候选建议
    """
    posts = args["posts"]
    if not posts:
        return {"ok": False, "error": "no posts provided"}

    # 计算 CES（如果没有的话）
    for p in posts:
        if "CES" not in p:
            p["CES"] = compute_ces_for_post(p)

    sorted_posts = sorted(posts, key=lambda x: x["CES"], reverse=True)
    total = len(sorted_posts)
    top3 = sorted_posts[:3]

    # 角度共性
    angles = [p.get("角度", "") for p in top3 if p.get("角度")]
    angle_counts: dict[str, int] = {}
    for a in angles:
        angle_counts[a] = angle_counts.get(a, 0) + 1
    top_angles = sorted(angle_counts.items(), key=lambda x: x[1], reverse=True)

    # 标题钩子模式（提取首 8 字）
    title_hooks = [p.get("标题", "")[:8] for p in top3 if p.get("标题")]

    # 发布时段模式
    time_buckets: dict[str, int] = {"早晨": 0, "中午": 0, "下午": 0, "晚上": 0}
    for p in top3:
        date_str = p.get("日期", "") or p.get("发布时间", "")
        if isinstance(date_str, str) and len(date_str) >= 13:
            try:
                hour = int(date_str[11:13])
                if 6 <= hour < 11:
                    time_buckets["早晨"] += 1
                elif 11 <= hour < 14:
                    time_buckets["中午"] += 1
                elif 14 <= hour < 18:
                    time_buckets["下午"] += 1
                else:
                    time_buckets["晚上"] += 1
            except Exception:
                pass

    # 阶段判断
    stage = "Phase 1: 广撒网"
    progress_msg = ""
    if total >= 10:
        if len([p for p in sorted_posts if p["CES"] > 0]) >= 3:
            stage = "Phase 2: 已识别有效角度，可进入 Phase 3"
        progress_msg = f"已发 {total}/10 篇，Top 3 CES：{', '.join(str(p['CES']) for p in top3)}"
    else:
        progress_msg = f"已发 {total}/10 篇，再发 {10-total} 篇可进入下一阶段"

    findings = []
    if top_angles:
        findings.append(f"Top 角度：{top_angles[0][0]}（在 Top3 中出现 {top_angles[0][1]} 次）")
    if title_hooks:
        findings.append(f"高互动标题前缀样本：{ ' / '.join(title_hooks)}")
    best_time = max(time_buckets.items(), key=lambda x: x[1]) if any(time_buckets.values()) else None
    if best_time:
        findings.append(f"高互动时段：{best_time[0]}（Top3 中 {best_time[1]} 篇）")

    return {
        "ok": True,
        "data": {
            "stage": stage,
            "progress": progress_msg,
            "top3":  [{"标题": p.get("标题", ""), "角度": p.get("角度", ""),
                        "CES": p["CES"], "日期": p.get("日期", "")}
                       for p in top3],
            "findings": findings,
            "angle_distribution": dict(top_angles),
            "time_distribution":  time_buckets,
            "totals": {
                "posts": total, "avg_ces": sum(p["CES"] for p in sorted_posts) / max(1, total),
            },
        },
    }


# ── 流量诊断检查清单 ─────────────────────────────────────────────────────

DIAGNOSIS_CHECKLIST = {
    "内容质量": [
        "标题前 5 字是否包含核心关键词",
        "正文前 3 行是否有钩子（数字/痛点/悬念）",
        "是否有引导评论的互动句",
        "标签 3-8 个，是否大词+小词组合",
    ],
    "发布时机": [
        "是否在 12:00 或 20:30 流量高峰发布",
        "是否在热点事件/节假日前 2 天发布相关内容",
    ],
    "账号健康": [
        "近 7 天是否有登录互动行为",
        "搜索能否找到自己（账号权重正常）",
        "是否有违规内容被降权",
    ],
    "竞品对标": [
        "同赛道 Top 账号互动率是否远高于我",
        "对标账号的发布频率/时间/标题是否有规律",
    ],
}


def _diagnose_handler(args: dict, ctx: ToolContext) -> dict:
    """
    返回诊断清单 + 基于已提供的 self_check 字典自动评分。
    self_check 格式：{"内容质量.标题前5字..": True/False, ...}
    """
    self_check: dict[str, bool] = args.get("self_check", {})
    detailed = []
    pass_count, fail_count = 0, 0
    for category, items in DIAGNOSIS_CHECKLIST.items():
        cat_block = {"category": category, "items": []}
        for item in items:
            key = f"{category}.{item}"
            checked = self_check.get(key)
            status = "✅" if checked else ("❌" if checked is False else "❓")
            cat_block["items"].append({"item": item, "status": status, "checked": checked})
            if checked is True:
                pass_count += 1
            elif checked is False:
                fail_count += 1
        detailed.append(cat_block)

    total_items = sum(len(v) for v in DIAGNOSIS_CHECKLIST.values())
    score = round(pass_count / total_items * 100, 1) if total_items else 0
    return {
        "ok": True,
        "data": {
            "checklist": detailed,
            "summary": {
                "pass": pass_count, "fail": fail_count,
                "unknown": total_items - pass_count - fail_count,
                "score": score,
            },
        },
    }


# ── 注册 ─────────────────────────────────────────────────────────────────

registry.register(
    name="data_analysis.compute_ces",
    schema={
        "description": "Compute CES (Comprehensive Engagement Score) for a list of posts.",
        "parameters": {
            "type": "object",
            "required": ["posts"],
            "properties": {
                "posts": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Each item has 点赞 / 收藏 / 评论 / 分享 / 关注 fields",
                },
            },
        },
    },
    handler=_compute_ces_handler,
    description="CES scoring per XHS algorithm weights",
)

registry.register(
    name="data_analysis.run_10_3_1_model",
    schema={
        "description": "Run 10-3-1 topic optimization analysis: find Top 3 posts and extract patterns.",
        "parameters": {
            "type": "object",
            "required": ["posts"],
            "properties": {
                "posts": {"type": "array", "items": {"type": "object"}},
            },
        },
    },
    handler=_run_10_3_1_handler,
    description="Top-3 pattern extraction for content optimization",
)

registry.register(
    name="data_analysis.diagnose_traffic",
    schema={
        "description": "Generate traffic diagnosis checklist with optional self-check scoring.",
        "parameters": {
            "type": "object",
            "properties": {
                "self_check": {
                    "type": "object",
                    "description": "Map of 'category.item' → bool",
                },
            },
        },
    },
    handler=_diagnose_handler,
    description="Traffic diagnosis with checklist scoring",
)
