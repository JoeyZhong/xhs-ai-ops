"""
知乎高意图提问采集 Tool（lead-intent-radar V2 · 扩源）。

契约：collect.zhihu_question(keywords, limit) -> Signal[]
  Signal = {source='zhihu', source_url, signal_key, author, posted_at, post_text}

与 collect.xhs_intent 同契约、同可替换盒子模式：
  - FixtureCollector：离线内置样本（让多源流水线现在就能端到端跑）。
  - SidecarCollector：HTTP 调独立采集 sidecar（真实抓取）。
  env `ZHIHU_COLLECTOR`（默认 fixture）；sidecar 不可达降级返 []（雷达不崩）。

知乎=「高意图提问」：提问标题+描述合成 post_text；触达走人工（写端本轮不做）。
"""

from __future__ import annotations

import os
import json
import hashlib
from pathlib import Path

from agent_tools import registry
from agent_tools.registry import ToolContext


CONFIG_DIR = Path(__file__).parent.parent / "config"

SIDECAR_URL = os.environ.get("ZHIHU_SIDECAR_URL",
                             os.environ.get("XHS_SIDECAR_URL", "http://localhost:8800"))
SIDECAR_TIMEOUT = float(os.environ.get("ZHIHU_SIDECAR_TIMEOUT", "30"))


def _signal_key(url: str, fallback: str) -> str:
    base = url or fallback
    h = hashlib.sha1((base or fallback).encode("utf-8")).hexdigest()[:16]
    return f"zhihu:{h}"


def _normalize(raw: dict) -> dict:
    """归一一条知乎提问为 Signal。问题标题 + 描述拼成 post_text。"""
    url = raw.get("source_url") or raw.get("url") or raw.get("question_url") or ""
    title = raw.get("title") or raw.get("question") or ""
    desc = raw.get("post_text") or raw.get("desc") or raw.get("detail") or raw.get("content") or ""
    text = raw.get("post_text") or ("\n".join(p for p in (title, desc) if p)).strip() or title
    key = raw.get("signal_key") or _signal_key(url, text)
    return {
        "source": "zhihu",
        "source_url": url,
        "signal_key": key,
        "author": raw.get("author") or raw.get("nickname") or raw.get("asker") or "",
        "posted_at": raw.get("posted_at") or raw.get("created") or raw.get("time") or "",
        "post_text": text,
    }


_BUILTIN_FIXTURES: list[dict] = [
    {"author": "匿名用户", "source_url": "https://www.zhihu.com/question/zh_bid01",
     "title": "投标要审计报告，三天内能出吗？",
     "desc": "公司要投个标，招标文件要求提供近一年的审计报告，正规能盖章的那种。时间很紧，三天内来得及做吗？大概什么价？"},
    {"author": "创业小白", "source_url": "https://www.zhihu.com/question/zh_hitech01",
     "title": "高新技术企业认定的专项审计报告找谁做比较稳？",
     "desc": "第一次申报高新，不太懂研发费用专项审计的流程，想找个有经验的事务所，求推荐靠谱的。"},
    {"author": "知乎用户aBcd", "source_url": "https://www.zhihu.com/question/zh_foreign01",
     "title": "外资公司每年的年审审计是不是必须做？",
     "desc": "新设的外商独资企业，听说每年要做法定审计，是不是必须的？不做有什么后果？找哪种所？"},
    {"author": "财税爱好者", "source_url": "https://www.zhihu.com/question/zh_kb01",
     "title": "审计报告和验资报告有什么区别？",
     "desc": "纯科普求知，想搞清楚两者的概念区别，不是要做。"},  # 噪声：单纯科普
    {"author": "某事务所", "source_url": "https://www.zhihu.com/question/zh_ad01",
     "title": "推荐一家全国可做的审计机构",
     "desc": "我们所专业出具各类审计报告，全国接单，价格优惠，有需要的可以了解。"},  # 噪声：同行广告
]


class FixtureCollector:
    def collect(self, keyword: str, limit: int) -> list[dict]:
        path = CONFIG_DIR / "zhihu_signal_fixtures.json"
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
                json={"source": "zhihu", "keyword": keyword, "limit": limit},
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
    mode = os.environ.get("ZHIHU_COLLECTOR", "fixture").lower()
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
    name="collect.zhihu_question",
    schema={
        "description": (
            "从知乎采集高意图求购提问，按关键词检索并归一为 Signal 列表（source=zhihu）。"
            "采集器可替换（fixture/sidecar），由 env ZHIHU_COLLECTOR 决定。"
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
    description="知乎高意图提问采集（可替换采集器）",
)
