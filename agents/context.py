"""
派生上下文（兼容层）。

把旧数据（config/persona.json / config/goals.json / 采集数据）
派生为 markdown 文本，注入到 Agent 的 memory snapshot 中。

约定：派生文本以 `_derived` 前缀的"虚拟文件名"出现在 snapshot 中，
Agent 的 build_system_prompt 可以自由选择是否读取。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
from storage.factory import get_backend
from agents.used_angles import angle_names

CONFIG_DIR = Path(__file__).parent.parent / "config"


# ── 工具函数 ─────────────────────────────────────────────────────────────

def _load_json(path: Path, default=None):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default if default is not None else {}


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + "…"


# ── persona 加载（多账号 + 回退链） ──────────────────────────────────────

def _load_active_persona(tenant_id: str,
                          goal_id: str = "") -> Optional[dict]:
    """
    多账号人设加载，回退链：
    1. 读 goals.json 找到 active goal 的 persona_id
    2. 在 personas.json 中按 id 查找
    3. 找不到 → 用 personas.json.active_id
    4. personas.json 不存在 → backend.load_persona
    5. 也没有 → 回退到 legacy persona.json
    """
    backend = get_backend()

    # 1. 读取 active goal 的 persona_id
    target_persona_id = ""
    goals_data = backend.load_goals(tenant_id)
    goals = goals_data.get("goals", [])
    if goals:
        active_goal_id = goal_id or goals_data.get("active_goal_id", "")
        goal = next((g for g in goals if g.get("id") == active_goal_id), goals[0])
        target_persona_id = goal.get("persona_id", "")

    # 2. 在 personas.json 中查找
    personas_data = _load_json(CONFIG_DIR / "personas.json")
    if personas_data and personas_data.get("personas"):
        personas = personas_data["personas"]
        if target_persona_id:
            match = next((p for p in personas if p.get("id") == target_persona_id), None)
            if match:
                return match
        active_id = personas_data.get("active_id", "")
        if active_id:
            match = next((p for p in personas if p.get("id") == active_id), None)
            if match:
                return match
        if personas:
            return personas[0]

    # 3. backend.load_persona (single persona)
    persona = backend.load_persona(tenant_id)
    if persona:
        return persona

    # 4. 回退到 legacy persona.json
    legacy = _load_json(CONFIG_DIR / "persona.json")
    if legacy:
        return legacy

    return None


# ── persona 派生 ─────────────────────────────────────────────────────────

def derive_persona_md(tenant_id: str,
                        goal_id: str = "") -> Optional[str]:
    """
    派生账号人设描述（账号品牌人设，与 Agent 角色身份分离）。

    不注入 persona.json 里的 system_prompt 字段。
    """
    persona = _load_active_persona(tenant_id, goal_id)
    if not persona:
        return None
    parts = ["## 当前服务账号"]
    if persona.get("nickname"):
        parts.append(f"**账号昵称：** {persona['nickname']}")
    if persona.get("background"):
        parts.append(f"**背景：** {persona['background']}")
    if persona.get("style_notes"):
        parts.append(f"**写作风格：** {persona['style_notes']}")
    if persona.get("tone"):
        parts.append(f"**常用表达 / 口头禅：** {persona['tone']}")
    return "\n".join(parts) if len(parts) > 1 else None


# ── 当前目标派生 ─────────────────────────────────────────────────────────

def derive_active_goal_md(tenant_id: str,
                            goal_id: str = "") -> Optional[str]:
    goals_data = get_backend().load_goals(tenant_id)
    goals = goals_data.get("goals", [])
    if not goals:
        return None

    target_id = goal_id or goals_data.get("active_goal_id", "")
    goal = next((g for g in goals if g.get("id") == target_id), goals[0])

    parts = [f"## 当前运营目标\n**{goal.get('name','—')}**（{goal.get('objective','—')}）",
             f"\n**描述：** {goal.get('description','—')}"]

    ta = goal.get("target_audience", {})
    if ta:
        parts.append(
            "\n## 目标受众\n"
            f"- **谁：** {ta.get('who','—')}\n"
            f"- **痛点：** {ta.get('pain_points','—')}\n"
            f"- **兴趣：** {ta.get('interests','—')}"
        )

    if goal.get("brand_position"):
        parts.append(f"\n## 品牌定位\n{goal['brand_position']}")

    ovs = goal.get("overall_strategy", {})
    if ovs:
        cm = ovs.get("core_message", "")
        cf = ovs.get("content_funnel", {})
        block = "\n## 总体策略"
        if cm:
            block += f"\n- **核心信息：** {cm}"
        if cf:
            block += (f"\n- **内容漏斗：**"
                       f"\n  - 顶层30%：{cf.get('top_30pct','—')}"
                       f"\n  - 中层40%：{cf.get('mid_40pct','—')}"
                       f"\n  - 底层30%：{cf.get('bottom_30pct','—')}")
        if ovs.get("differentiation"):
            block += f"\n- **差异化：** {ovs['differentiation']}"
        parts.append(block)

    if goal.get("keywords"):
        parts.append("\n## 当前采集关键词\n" + " · ".join(goal["keywords"]))

    used = angle_names(goal.get("used_angles", []))
    if used:
        parts.append("\n## 已用过的角度（务必回避，避免重复）\n"
                     + "\n".join(f"- {a}" for a in used))

    return "\n".join(parts)


# ── 爆款笔记基线（来自最近采集） ────────────────────────────────────────

def derive_benchmarks_md(tenant_id: str, n: int = 10,
                           since_days: int = 7) -> Optional[str]:
    backend = get_backend()
    since = datetime.now() - timedelta(days=since_days)
    df = backend.list_collected_data(tenant_id, since=since)
    if df.empty:
        return None

    # 采集产出为中文列名（标题/点赞数/…），规范化到本函数期望的英文列；
    # 仅在英文列缺席时改名，避免覆盖已是英文 schema 的后端。
    _CN2EN = {"标题": "title", "点赞数": "likes", "收藏数": "collects",
              "评论数": "comments_count", "笔记ID": "note_id", "搜索关键词": "keyword"}
    df = df.rename(columns={cn: en for cn, en in _CN2EN.items()
                            if cn in df.columns and en not in df.columns})

    # 无标题列（异常 schema）→ 无法做爆款基线，优雅跳过而非崩溃整个子 agent
    if "title" not in df.columns:
        return None

    # drop duplicates by note_id
    if "note_id" in df.columns:
        df = df.drop_duplicates(subset=["note_id"], keep="last")

    # ensure numeric interaction columns
    for col in ["likes", "collects", "comments_count"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    df["interaction"] = (
        df.get("likes", 0) + df.get("collects", 0) + df.get("comments_count", 0)
    )
    titled = df[df["title"].notna() & (df["title"].astype(str).str.strip() != "")]
    if len(titled) == 0:
        return None
    top = titled.nlargest(n, "interaction")

    parts = [f"## 近{since_days}天高互动笔记（爆款标题参考）"]
    for _, r in top.iterrows():
        kw = r.get("keyword", "")
        title = str(r["title"]).strip()
        parts.append(f"- [互动 {int(r['interaction'])}] [{kw}] {title}")

    return "\n".join(parts)


# ── 性能数据派生（给 Analyst） ───────────────────────────────────────────

def derive_performance_md(tenant_id: str,
                            goal_id: str = "") -> Optional[str]:
    goals_data = get_backend().load_goals(tenant_id)
    goals = goals_data.get("goals", [])
    if not goals:
        return None
    target_id = goal_id or goals_data.get("active_goal_id", "")
    goal = next((g for g in goals if g.get("id") == target_id), goals[0])

    posts = goal.get("performance", {}).get("posts", [])
    if not posts:
        return None

    parts = [f"## 已发布笔记表现数据（{len(posts)} 篇）"]
    for p in posts:
        ces = p.get("CES", "")
        parts.append(
            f"- 「{p.get('标题','—')}」角度={p.get('角度','—')} "
            f"日期={p.get('日期','—')} CES={ces} "
            f"点赞{p.get('点赞',0)} 收藏{p.get('收藏',0)} "
            f"评论{p.get('评论',0)} 关注{p.get('关注',0)}"
        )
    return "\n".join(parts)


# ── 整合：给 AgentBase._collect_memory_snapshot 用 ──────────────────────

def derived_snapshot(tenant_id: str, goal_id: str = "") -> dict:
    """
    返回 {scope: {"_derived__xxx.md": content}} 结构，
    使其能与真实 memory snapshot 合并而不冲突。
    """
    snap: dict = {"shared": {}, "intel": {}, "content": {}, "analyst": {}}

    persona_md = derive_persona_md(tenant_id, goal_id)
    if persona_md:
        snap["shared"]["_derived__persona.md"] = persona_md

    goal_md = derive_active_goal_md(tenant_id, goal_id)
    if goal_md:
        snap["shared"]["_derived__goal.md"] = goal_md

    bm_md = derive_benchmarks_md(tenant_id)
    if bm_md:
        snap["shared"]["_derived__benchmarks.md"] = bm_md

    perf_md = derive_performance_md(tenant_id, goal_id)
    if perf_md:
        snap["analyst"]["_derived__performance.md"] = perf_md
        if goal_md:
            snap["analyst"]["_derived__goal.md"] = goal_md

    return snap
