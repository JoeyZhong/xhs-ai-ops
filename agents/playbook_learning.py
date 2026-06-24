"""P3.3 · CES → playbook 学习闭环纯逻辑（无 IO，可单测）。

设计基线：openspec/changes/content-lifecycle-v2/design.md §5（三态判定）+ §6（防污染注释块）

职责：
- classify_angles：按 angle group 最近窗口的 post，算平均 CES → 三态 verdict
- render_auto_block：把 verdict 渲染成 markdown
- merge_playbook：把 auto block 合进 playbook.md，只替换 <!-- analyst-auto --> 区，
  保留运营人手写区（防污染核心）

三态判定（design §5）：
- validated_hit：样本数 ≥ min_samples 且 平均 CES > validated_hit_ces
- sunk：样本数 ≥ min_samples 且 平均 CES < sunk_ces
- unknown：样本数 ≥ min_samples 且 CES 居中
- 样本数 < min_samples：不判定（不出现在 verdict）
"""
from __future__ import annotations

from typing import Any

# 阈值集中定义（design §5 / §7：不 hardcode 散落）
TRISTATE_THRESHOLDS = {
    "validated_hit_ces": 200,
    "sunk_ces": 80,
    "min_samples": 3,
    "window_days": 30,
}

# playbook 自动区注释标记（design §6）
AUTO_BEGIN = "<!-- analyst-auto: v2 -->"
AUTO_END = "<!-- /analyst-auto -->"


def _to_num(v: Any) -> float:
    if v is None:
        return 0.0
    try:
        f = float(v)
        return 0.0 if f != f else f  # NaN → 0
    except (TypeError, ValueError):
        return 0.0


def classify_angles(posts: list[dict], thresholds: dict | None = None) -> dict[str, dict]:
    """按 angle 聚合 CES，给出三态判定。

    posts: [{"angle": str, "ces_score": number}, ...]
    返回: {angle: {"status", "avg_ces", "count"}}（只含样本数达标的 angle）
    """
    th = {**TRISTATE_THRESHOLDS, **(thresholds or {})}
    min_samples = th["min_samples"]
    hit = th["validated_hit_ces"]
    sunk = th["sunk_ces"]

    groups: dict[str, list[float]] = {}
    for p in posts:
        angle = str(p.get("angle") or "").strip()
        if not angle:
            continue
        groups.setdefault(angle, []).append(_to_num(p.get("ces_score")))

    verdicts: dict[str, dict] = {}
    for angle, ces_list in groups.items():
        if len(ces_list) < min_samples:
            continue
        avg = sum(ces_list) / len(ces_list)
        avg = round(avg, 2)
        if avg == int(avg):
            avg = int(avg)
        if avg > hit:
            status = "validated_hit"
        elif avg < sunk:
            status = "sunk"
        else:
            status = "unknown"
        verdicts[angle] = {"status": status, "avg_ces": avg, "count": len(ces_list)}
    return verdicts


_STATUS_LABEL = {
    "validated_hit": "✅ 已验证爆款",
    "sunk": "❌ 已沉底",
    "unknown": "◽ 待观察",
}


def render_auto_block(verdicts: dict[str, dict]) -> str:
    """把三态 verdict 渲染成 markdown（不含注释标记，由 merge_playbook 包裹）。"""
    if not verdicts:
        return "（暂无足够样本，本周不更新角度判定）"
    # 按 status 排序：validated_hit 在前，sunk 在后
    order = {"validated_hit": 0, "unknown": 1, "sunk": 2}
    items = sorted(verdicts.items(), key=lambda kv: (order.get(kv[1]["status"], 9), -kv[1]["avg_ces"]))
    lines = ["## 角度表现（AnalystEvaluator 自动判定）", ""]
    for angle, v in items:
        label = _STATUS_LABEL.get(v["status"], v["status"])
        lines.append(f"- **{angle}** — {label}（近 {v['count']} 篇平均 CES {v['avg_ces']}）")
    return "\n".join(lines)


def extract_auto_block(playbook_text: str | None, *, max_chars: int = 500) -> str:
    """从 playbook.md 取出 <!-- analyst-auto: v2 --> 块的正文（截断 max_chars）。

    给 prompt_context 注入用：只读自动区，避免运营人手写区污染 prompt 上下文。
    无自动区 / 空 → 返回 ""。
    """
    if not playbook_text:
        return ""
    begin = playbook_text.find(AUTO_BEGIN)
    end = playbook_text.find(AUTO_END)
    if begin == -1 or end == -1 or end <= begin:
        return ""
    body = playbook_text[begin + len(AUTO_BEGIN):end].strip()
    return body[:max_chars]


def merge_playbook(existing: str, auto_block_body: str) -> str:
    """把 auto_block_body 合进 playbook，只替换 AUTO_BEGIN..AUTO_END 区。

    - 已有自动区 → 原地替换内容（保留前后手写区）
    - 无自动区 → 追加到文末
    幂等地保证只有一个自动区。
    """
    wrapped = f"{AUTO_BEGIN}\n{auto_block_body}\n{AUTO_END}"
    existing = existing or ""

    begin = existing.find(AUTO_BEGIN)
    end = existing.find(AUTO_END)
    if begin != -1 and end != -1 and end > begin:
        before = existing[:begin].rstrip()
        after = existing[end + len(AUTO_END):].lstrip()
        parts = [p for p in (before, wrapped, after) if p]
        return "\n\n".join(parts) + "\n"

    # 无自动区 → 追加
    base = existing.rstrip()
    if base:
        return f"{base}\n\n{wrapped}\n"
    return f"{wrapped}\n"
