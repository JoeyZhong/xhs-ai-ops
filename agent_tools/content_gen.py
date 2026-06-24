"""
内容批量生成 Tool（包装 content_generator.py 的核心逻辑）。

直接调 Kimi 生成笔记，复用 content_generator 中的 prompt 构造和 JSON 解析。
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from agent_tools import registry
from agent_tools.registry import ToolContext


# ── 复用 content_generator.py 中的 prompt 构造和解析 ────────────────────

def _ensure_module():
    """把 Spider_XHS 根目录加到 sys.path 以便导入 content_generator。"""
    root = Path(__file__).parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


_ensure_module()


def _build_user_prompt(top_notes: list[dict], idx: int, total: int,
                        used_angles: list[str], campaign_strategy: str = "") -> str:
    """同 content_generator.build_user_prompt，独立一份避免循环依赖。"""
    ref_lines = [f"  {i:2d}. [{n.get('关键词','')}] 互动{n.get('互动',0)} | {n.get('标题','')}"
                 for i, n in enumerate(top_notes, 1)]

    used_block = ""
    if used_angles:
        used_block = ("\n【已使用过的角度，本篇必须回避】\n"
                       + "\n".join(f"  - {a}" for a in used_angles) + "\n")

    campaign_block = ""
    if campaign_strategy:
        campaign_block = f"\n【本次投放策略（请严格遵守）】\n{campaign_strategy}\n"

    return f"""以下是小红书高互动笔记参考（共 {len(top_notes)} 篇）：

{"".join(ref_lines)}
{used_block}{campaign_block}
请生成第 {idx}/{total} 篇原创小红书笔记。

切入角度备选（从未用过的中选一个）：
  · 选址避坑/踩坑复盘
  · 工厂/写字楼/工地场景收益对比
  · 一台机器的真实月收益拆解
  · 和物业/保安打交道的故事
  · 补货/维护/日常运营记录
  · 竞品分析（扭蛋机/无人零食柜/共享按摩椅）
  · 点位谈判技巧与心得
  · 闲置场地变现逻辑科普

必须输出合法 JSON，禁止任何多余文字。"""


def _parse_response(raw: str) -> Optional[dict]:
    """从 Kimi 响应中提取 JSON（多重 fallback）。"""
    if not raw:
        return None
    raw = raw.strip()
    raw = re.sub(r"```(?:json)?\s*", "", raw).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        return None
    candidate = raw[start:end + 1]
    for transform in [
        lambda s: s,
        lambda s: re.sub(r",\s*([}\]])", r"\1", s),
        lambda s: re.sub(r",\s*([}\]])", r"\1", s).replace("'", '"'),
    ]:
        try:
            return json.loads(transform(candidate))
        except Exception:
            pass
    # 兜底：逐 key 提取
    result: dict = {}
    for m in re.finditer(r'"(\w+)"\s*:\s*("(?:[^"\\]|\\.)*"|[\d.]+|\[.*?\])',
                            candidate, re.DOTALL):
        k, v = m.group(1), m.group(2)
        try:
            result[k] = json.loads(v)
        except Exception:
            result[k] = v.strip('"')
    return result or None


# ── 核心：生成单篇 ───────────────────────────────────────────────────────

def generate_one(top_notes: list[dict],
                   idx: int,
                   total: int,
                   used_angles: list[str],
                   system_prompt: str,
                   campaign_strategy: str = "") -> dict:
    """
    生成单篇笔记。返回 record dict（与原 content_generator 输出 schema 一致）。
    """
    from agent_tools.kimi import call_kimi

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prompt = _build_user_prompt(top_notes, idx, total, used_angles, campaign_strategy)
    raw, err = call_kimi(
        prompt=prompt,
        system=system_prompt,
        max_tokens=4096,
        temperature=0.9,
    )
    if err:
        return {"序号": idx, "主标题": "[请求失败]", "备选标题1": "", "备选标题2": "",
                "正文": "", "字数": 0, "标签": "", "最佳发布时间": "",
                "发布时间理由": "", "本次角度": "", "参考关键词": "",
                "生成时间": now_str, "_error": err}

    parsed = _parse_response(raw)
    if not parsed:
        return {"序号": idx, "主标题": "[解析失败]", "备选标题1": "", "备选标题2": "",
                "正文": "", "字数": 0, "标签": "", "最佳发布时间": "",
                "发布时间理由": "", "本次角度": "", "参考关键词": "",
                "生成时间": now_str, "_raw": raw[:500]}

    body = parsed.get("正文", "")
    tags = parsed.get("标签", [])
    tag_str = " ".join(f"#{t}" for t in tags) if isinstance(tags, list) else str(tags)
    return {
        "序号": idx,
        "主标题": parsed.get("主标题", ""),
        "备选标题1": parsed.get("备选标题1", ""),
        "备选标题2": parsed.get("备选标题2", ""),
        "正文": body,
        "字数": len(body),
        "标签": tag_str,
        "最佳发布时间": parsed.get("最佳发布时间", ""),
        "发布时间理由": parsed.get("发布时间理由", ""),
        "本次角度": parsed.get("本次角度", ""),
        "参考关键词": top_notes[0].get("关键词", "") if top_notes else "",
        "生成时间": now_str,
    }


def get_top_notes_from_df(df, n: int = 20) -> list[dict]:
    """从采集数据 DataFrame 中提取 Top N 高互动笔记。"""
    if df is None or len(df) == 0:
        return []

    def safe_int(v):
        try:
            import pandas as pd
            return int(float(v)) if pd.notna(v) else 0
        except Exception:
            return 0

    df = df.copy()
    df["互动"] = (df.get("点赞数", 0).apply(safe_int)
                   + df.get("收藏数", 0).apply(safe_int)
                   + df.get("评论数", 0).apply(safe_int))
    titled = df[df["标题"].notna() & (df["标题"].astype(str).str.strip() != "")]
    return [
        {"关键词": r.get("搜索关键词", ""),
         "标题": str(r["标题"]).strip(),
         "互动": int(r["互动"])}
        for _, r in titled.nlargest(n, "互动").iterrows()
    ]


# ── F13 fix: Chinese → English field mapping ────────────────────────────


def _to_english(rec: dict, lifecycle_args: dict) -> dict:
    return {
        "content_id": str(uuid.uuid4()),
        "title": rec.get("主标题", ""),
        "body": rec.get("正文", ""),
        "hashtags": rec.get("标签", "").split() if isinstance(rec.get("标签"), str) else [],
        "publish_at": rec.get("最佳发布时间", ""),
        "status": "draft",
        "topic_id": lifecycle_args.get("topic_id"),
        "strategy_id": lifecycle_args.get("strategy_id"),
        "calendar_item_id": lifecycle_args.get("calendar_item_id"),
        "knowledge_refs": lifecycle_args.get("knowledge_refs", []),
        "memory_refs": lifecycle_args.get("memory_refs", []),
        "meta": {k: rec[k] for k in ["备选标题1", "备选标题2", "发布时间理由", "本次角度",
                                      "参考关键词", "生成时间"] if k in rec},
    }


# ── Tool handler ─────────────────────────────────────────────────────────

def _generate_batch_handler(args: dict, ctx: ToolContext) -> dict:
    import time as _time
    import pandas as pd

    top_notes = args.get("top_notes") or []
    used_angles = args.get("used_angles", [])
    system_prompt = args["system_prompt"]
    batch_size = args["batch_size"]
    request_gap = args.get("request_gap", 2.0)
    campaign_strategy = args.get("campaign_strategy", "")
    lifecycle_args = {
        "topic_id": args.get("topic_id"),
        "strategy_id": args.get("strategy_id"),
        "calendar_item_id": args.get("calendar_item_id"),
        "knowledge_refs": args.get("knowledge_refs", []),
        "memory_refs": args.get("memory_refs", []),
    }

    records: list[dict] = []
    new_angles: list[str] = []
    for idx in range(1, batch_size + 1):
        rec = generate_one(
            top_notes=top_notes,
            idx=idx, total=batch_size,
            used_angles=used_angles + new_angles,
            system_prompt=system_prompt,
            campaign_strategy=campaign_strategy,
        )
        english_rec = _to_english(rec, lifecycle_args)
        records.append(english_rec)
        if rec.get("本次角度"):
            new_angles.append(rec["本次角度"])
        if idx < batch_size:
            _time.sleep(request_gap)

    saved_path = None
    if ctx.storage and records:
        df = pd.DataFrame(records, columns=[
            "content_id", "title", "body", "hashtags", "publish_at", "status",
            "topic_id", "strategy_id", "calendar_item_id",
            "knowledge_refs", "memory_refs", "meta",
        ])
        # P3.1.6 溯源：把来源会话 id 落进每条笔记，供「后台内容 ← 源对话」反向追溯。
        # 来自 orchestrator 的 AgentTask.extra → ctx.extra；无则不加该列，不污染其它调用方。
        sid = (ctx.extra or {}).get("source_session_id")
        if sid:
            df["source_session_id"] = sid
        try:
            saved_path = ctx.storage.save_generated_posts(
                tenant_id=ctx.tenant_id,
                df=df,
                meta={"goal_id": args.get("goal_id", "")},
            )
        except Exception:
            pass

    return {
        "ok": True,
        "data": {
            "records": records,
            "new_angles": new_angles,
            "stats": {
                "batch_size": batch_size,
                "successful": sum(1 for r in records if len(r.get("body", "") or "") > 0),
                "failed":      sum(1 for r in records if len(r.get("body", "") or "") == 0),
            },
            "saved_path": saved_path,
        },
    }


# ── 注册 ─────────────────────────────────────────────────────────────────

registry.register(
    name="content_gen.generate_batch",
    schema={
        "description": "Generate a batch of XHS notes using Kimi, based on top reference notes and persona.",
        "parameters": {
            "type": "object",
            "required": ["batch_size", "system_prompt"],
            "properties": {
                "batch_size":         {"type": "integer", "minimum": 1, "maximum": 30},
                "system_prompt":      {"type": "string", "description": "Persona system prompt"},
                "top_notes":          {"type": "array", "description": "Reference top-engagement notes"},
                "used_angles":        {"type": "array", "items": {"type": "string"}},
                "campaign_strategy":  {"type": "string"},
                "goal_id":            {"type": "string"},
                "topic_id":           {"type": "string"},
                "strategy_id":        {"type": "string"},
                "calendar_item_id":   {"type": "string"},
                "knowledge_refs":     {"type": "array", "items": {"type": "object"}},
                "memory_refs":        {"type": "array", "items": {"type": "object"}},
                "request_gap":        {"type": "number", "minimum": 0.5, "maximum": 30},
            },
        },
    },
    handler=_generate_batch_handler,
    cost_estimate=2048.0,
    description="Batch content generator with anti-repetition tracking",
)
