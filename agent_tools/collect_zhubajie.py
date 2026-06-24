"""
猪八戒需求单采集 Tool（lead-intent-radar V2 · 扩源）。

契约：collect.zhubajie_demand(keywords, limit) -> Signal[]
  Signal = {source='zhubajie', source_url, signal_key, author, posted_at, post_text, meta}

与 collect.xhs_intent 同契约、同可替换盒子模式：
  - FixtureCollector：离线内置样本。
  - SidecarCollector：HTTP 调独立采集 sidecar。
  env `ZHUBAJIE_COLLECTOR`（默认 fixture）；sidecar 不可达降级返 []。

猪八戒=「发需求单」：雇主直接发审计需求，是平台原生高转化场景。
  平台特有结构化字段（预算 / 交付周期 / 是否已接单）归入 Signal.meta，
  随 lead.meta 持久化，供前端选择性展示（红书/知乎无此结构）。
  触达本轮只读：接单是结构化动作，非「发评论」，不自动化。
"""

from __future__ import annotations

import os
import json
import hashlib
from pathlib import Path

from agent_tools import registry
from agent_tools.registry import ToolContext


CONFIG_DIR = Path(__file__).parent.parent / "config"

SIDECAR_URL = os.environ.get("ZHUBAJIE_SIDECAR_URL",
                             os.environ.get("XHS_SIDECAR_URL", "http://localhost:8800"))
SIDECAR_TIMEOUT = float(os.environ.get("ZHUBAJIE_SIDECAR_TIMEOUT", "30"))


def _signal_key(url: str, fallback: str) -> str:
    base = url or fallback
    h = hashlib.sha1((base or fallback).encode("utf-8")).hexdigest()[:16]
    return f"zhubajie:{h}"


def _extract_meta(raw: dict) -> dict:
    """提取猪八戒结构化字段进 meta。缺省字段不放入（前端缺省不渲染）。"""
    meta: dict = {}
    if raw.get("budget") is not None:
        meta["budget"] = raw["budget"]
    if raw.get("delivery") is not None:
        meta["delivery"] = raw["delivery"]            # 交付周期，如 "7天内"
    if raw.get("taken") is not None:
        meta["taken"] = bool(raw["taken"])            # 是否已接单
    return meta


def _normalize(raw: dict) -> dict:
    """归一一条猪八戒需求单为 Signal。需求标题 + 描述拼成 post_text。"""
    url = raw.get("source_url") or raw.get("url") or raw.get("demand_url") or ""
    title = raw.get("title") or raw.get("demand_title") or ""
    desc = raw.get("post_text") or raw.get("desc") or raw.get("demand_desc") or raw.get("content") or ""
    text = raw.get("post_text") or ("\n".join(p for p in (title, desc) if p)).strip() or title
    key = raw.get("signal_key") or _signal_key(url, text)
    sig = {
        "source": "zhubajie",
        "source_url": url,
        "signal_key": key,
        "author": raw.get("author") or raw.get("employer") or raw.get("user") or "",
        "posted_at": raw.get("posted_at") or raw.get("published") or raw.get("time") or "",
        "post_text": text,
    }
    meta = _extract_meta(raw)
    if meta:
        sig["meta"] = meta
    return sig


_BUILTIN_FIXTURES: list[dict] = [
    {"employer": "@某企业服务", "source_url": "https://task.zbj.com/demand/zbj_hitech01",
     "title": "高新认定专项审计报告",
     "desc": "高新认定需要研发费用专项审计报告，需正规事务所盖章，预算有限，希望尽快出表。",
     "budget": "2000-3000", "delivery": "7天内", "taken": False},
    {"employer": "@小微财务", "source_url": "https://task.zbj.com/demand/zbj_loan01",
     "title": "银行贷款审计报告",
     "desc": "公司办贷款，银行要审计报告，近两年账，能加急的优先，价格实在就行。",
     "budget": "3000-5000", "delivery": "3天内", "taken": False},
    {"employer": "@创业者老王", "source_url": "https://task.zbj.com/demand/zbj_cancel01",
     "title": "注销清算审计报告",
     "desc": "小公司要注销，需要清算审计报告，没几笔账，问下大概多少钱多久能出。",
     "budget": "2000以内", "delivery": "面议", "taken": True},  # 已接单（仍入库，标状态）
    {"employer": "@同行接单", "source_url": "https://task.zbj.com/demand/zbj_ad01",
     "title": "承接各类审计报告业务",
     "desc": "本所承接全国审计报告，价格优惠，欢迎合作分包。",
     "budget": "面议", "delivery": "面议", "taken": False},  # 噪声：同行而非买家
]


class FixtureCollector:
    def collect(self, keyword: str, limit: int) -> list[dict]:
        path = CONFIG_DIR / "zhubajie_signal_fixtures.json"
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                raw = list(_BUILTIN_FIXTURES)
        else:
            raw = list(_BUILTIN_FIXTURES)
        return [_normalize(r) for r in raw[:limit]]


class SidecarCollector:
    def collect(self, keyword: str, limit: int) -> list[dict]:
        try:
            import requests
            resp = requests.post(
                f"{SIDECAR_URL}/collect",
                json={"source": "zhubajie", "keyword": keyword, "limit": limit},
                timeout=SIDECAR_TIMEOUT,
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            items = data.get("items", data if isinstance(data, list) else [])
            return [_normalize(r) for r in items][:limit]
        except Exception:
            return []


def _get_collector():
    mode = os.environ.get("ZHUBAJIE_COLLECTOR", "fixture").lower()
    if mode == "sidecar":
        return SidecarCollector()
    return FixtureCollector()


def _collect_handler(args: dict, ctx: ToolContext) -> dict:
    keywords = args.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    keywords = [k for k in (s.strip() for s in keywords) if k]
    if not keywords:
        return {"ok": False, "error": "keywords is required (监控关键词列表)"}

    limit = int(args.get("limit", 20))
    collector = _get_collector()

    seen: set[str] = set()
    signals: list[dict] = []
    for kw in keywords:
        for sig in collector.collect(kw, limit):
            if sig["signal_key"] in seen:
                continue
            seen.add(sig["signal_key"])
            sig["matched_keyword"] = kw
            signals.append(sig)

    return {"ok": True,
            "data": {"signals": signals, "count": len(signals),
                     "collector": collector.__class__.__name__}}


registry.register(
    name="collect.zhubajie_demand",
    schema={
        "description": (
            "从猪八戒采集审计需求单，按关键词检索并归一为 Signal 列表（source=zhubajie，"
            "结构化字段 预算/交付周期/接单状态 进 meta）。采集器可替换（fixture/sidecar），"
            "由 env ZHUBAJIE_COLLECTOR 决定。"
        ),
        "parameters": {
            "type": "object",
            "required": ["keywords"],
            "properties": {
                "keywords": {"type": "array", "items": {"type": "string"},
                             "description": "监控关键词列表"},
                "limit":    {"type": "integer", "minimum": 1, "maximum": 100,
                             "description": "每个关键词返回上限，默认 20"},
            },
        },
    },
    handler=_collect_handler,
    cost_estimate=0.0,
    description="猪八戒需求单采集（可替换采集器，带结构化 meta）",
)
