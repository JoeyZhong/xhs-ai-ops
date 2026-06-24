"""
小红书意图信号采集 Tool（lead-intent-radar V1 · Phase 1）。

契约：collect.xhs_intent(keywords, limit) -> Signal[]
  Signal = {source, source_url, signal_key, author, posted_at, post_text}

采集器可替换（用户「解耦+共享契约」原则）：
  - FixtureCollector：离线，从内置/JSON fixture 返回信号，让流水线现在就能端到端跑。
  - SidecarCollector：HTTP 调独立采集 sidecar（MediaCrawler-CDP / ReaJason），真实抓取。
  选择由 env `XHS_COLLECTOR` 决定（默认 fixture，避免无 sidecar 时报错）。

⚠️ License 闸门（决策 B）：免费版 MediaCrawler 禁止商用，仅本地 spike；
   上线真实获客前须切到 ReaJason/xhs(MIT) 或购买 Pro。见 sidecar-contract.md。
"""

from __future__ import annotations

import os
import json
import time
import random
import hashlib
from pathlib import Path
from typing import Optional

from agent_tools import registry
from agent_tools.registry import ToolContext


CONFIG_DIR = Path(__file__).parent.parent / "config"

# 采集 sidecar 契约（HTTP）
SIDECAR_URL = os.environ.get("XHS_SIDECAR_URL", "http://localhost:8800")
SIDECAR_TIMEOUT = float(os.environ.get("XHS_SIDECAR_TIMEOUT", "30"))

# 搜索排序 → search_some_note 的 sort_type_choice（0综合/1最新/2点赞/3评论/4收藏）。
# 线索雷达默认「最新」：每次扫描取当天新发的求购帖，把"综合排序重扫=同一批老帖=0新增"
# 变成"重扫=新帖累积"。单次请求数不变（仍每词 1 页），不增加封号风险维度。env 可覆盖。
_SORT_CHOICES = {"latest": 1, "general": 0, "popular": 2, "comment": 3, "collect": 4}
# 时间窗 → note_time（0不限/1一天内/2一周内/3半年内）。默认不限以保召回；
# 量大的词可设 XHS_SEARCH_TIME_WINDOW=week 聚焦新鲜窗口、少跑老帖省 Kimi。
_TIME_WINDOWS = {"all": 0, "day": 1, "week": 2, "halfyear": 3}


def _signal_key(source: str, url: str, fallback: str) -> str:
    """稳定去重键：优先用 note_id（url 尾段），否则 hash(url 或正文)。"""
    base = url or fallback
    if base:
        h = hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]
        return f"{source}:{h}"
    return f"{source}:{hashlib.sha1(fallback.encode('utf-8')).hexdigest()[:16]}"


def _normalize(raw: dict, source: str = "xhs") -> dict:
    """把采集器返回的一条原始记录归一为 Signal。"""
    url = raw.get("source_url") or raw.get("url") or raw.get("note_url") or ""
    text = raw.get("post_text") or raw.get("text") or raw.get("desc") or raw.get("title") or ""
    key = raw.get("signal_key") or _signal_key(source, url, text)
    return {
        "source": source,
        "source_url": url,
        "signal_key": key,
        "author": raw.get("author") or raw.get("nickname") or raw.get("user") or "",
        "posted_at": raw.get("posted_at") or raw.get("time") or "",
        "post_text": text,
    }


# ── Fixture 采集器（离线）───────────────────────────────────────────────────

_BUILTIN_FIXTURES: list[dict] = [
    {"author": "@小敏的店", "source_url": "https://www.xiaohongshu.com/explore/fx_loan01",
     "post_text": "急求一份审计报告办银行贷款，有没有靠谱又便宜的，坐标深圳，本周就要交给银行。第一次弄完全不懂要准备啥"},
    {"author": "@阿强", "source_url": "https://www.xiaohongshu.com/explore/fx_bid01",
     "post_text": "投标要审计报告，时间很紧，三天内能出吗？正规能盖章的那种，价格好说"},
    {"author": "@Lina", "source_url": "https://www.xiaohongshu.com/explore/fx_hitech01",
     "post_text": "高新认定的专项审计报告找谁做比较稳？第一次弄不太懂流程，想找个有经验的"},
    {"author": "@同行财税", "source_url": "https://www.xiaohongshu.com/explore/fx_ad01",
     "post_text": "我们专业代办各类审计报告，全国低价，需要的私我，量大从优！"},  # 噪声：同行广告
    {"author": "@路人甲", "source_url": "https://www.xiaohongshu.com/explore/fx_noise01",
     "post_text": "今天深圳天气真不错，分享一下我的周末vlog～"},  # 噪声：无关
]


class FixtureCollector:
    """从内置样本或 config/xhs_signal_fixtures.json 返回信号。"""

    def collect(self, keyword: str, limit: int) -> list[dict]:
        path = CONFIG_DIR / "xhs_signal_fixtures.json"
        raw: list[dict]
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                raw = list(_BUILTIN_FIXTURES)
        else:
            raw = list(_BUILTIN_FIXTURES)
        # fixture 不做真实关键词检索，全量返回（前 limit 条）
        return [_normalize(r) for r in raw[:limit]]


# ── Native 采集器（项目自带 scraper，无需 sidecar 进程）─────────────────────

class NativeCollector:
    """使用项目自身的 apis/xhs_pc_apis.py 真实搜索小红书。

    Cookie 来源：cookie_manager → env COOKIES。无 Cookie 时降级返 []。
    API 失败时自动尝试浏览器兜底（Playwright）。

    反封号：同一次扫描内多关键词连发会形成机器人式突发节奏，故请求之间插随机
    抖动（首个不等），打散固定节奏。env 调参：
      XHS_NATIVE_JITTER=off 关闭；XHS_NATIVE_JITTER_MIN/MAX 抖动秒数（默认 3~8）。
    频次上限（≥2h/≤3次每日）由 scheduler cron 节奏保证，此处只管单次扫描内的节奏。
    """

    def __init__(self) -> None:
        self._last_request_ts: float = 0.0

    def _throttle(self) -> None:
        """已有前序请求时，sleep 一段随机抖动（已扣除上次请求自身耗时）。"""
        if os.environ.get("XHS_NATIVE_JITTER", "on").lower() in ("off", "0", "false"):
            return
        if self._last_request_ts <= 0:
            return  # 首个关键词不等待
        try:
            lo = float(os.environ.get("XHS_NATIVE_JITTER_MIN", "3"))
            hi = float(os.environ.get("XHS_NATIVE_JITTER_MAX", "8"))
        except ValueError:
            lo, hi = 3.0, 8.0
        if hi < lo:
            hi = lo
        delay = random.uniform(lo, hi) - (time.time() - self._last_request_ts)
        if delay > 0:
            time.sleep(delay)

    def collect(self, keyword: str, limit: int) -> list[dict]:
        # Cookie
        cookies_str = ""
        try:
            from storage.cookie_manager import get_cookie
            cookies_str = get_cookie("default") or ""
        except Exception:
            pass
        if not cookies_str:
            cookies_str = os.environ.get("COOKIES", "")
        if not cookies_str:
            return []  # 无 Cookie，降级

        # API 采集
        try:
            from apis.xhs_pc_apis import XHS_Apis
            api = XHS_Apis()
            # 排序默认「最新」、时间窗默认「不限」，均可由 env 覆盖（见模块顶部常量）。
            sort_choice = _SORT_CHOICES.get(
                os.environ.get("XHS_SEARCH_SORT", "latest").lower(), 1)
            note_time = _TIME_WINDOWS.get(
                os.environ.get("XHS_SEARCH_TIME_WINDOW", "all").lower(), 0)
            self._throttle()  # 反封号：请求间随机抖动
            success, msg, notes = api.search_some_note(
                keyword, limit, cookies_str,
                sort_type_choice=sort_choice, note_time=note_time)
            self._last_request_ts = time.time()
            if not success:
                # 浏览器兜底
                try:
                    from browser_search import search_notes
                    success2, msg2, notes2, _ = search_notes(
                        keyword, limit, cookies_str, headless=True, account_id="default")
                    if success2:
                        notes = notes2
                    else:
                        return []
                except Exception:
                    return []
        except Exception:
            return []

        signals: list[dict] = []
        for item in notes:
            note_card = item.get("note_card", {})
            user = note_card.get("user", {})
            note_id = item.get("id", "")

            publish_time = ""
            for tag in note_card.get("corner_tag_info", []):
                if tag.get("type") == "publish_time":
                    publish_time = tag.get("text", "")
                    break

            title = note_card.get("display_title", "")
            desc = note_card.get("desc", "")
            full_text = f"{title}\n{desc}".strip() if desc else title

            # 必须过 _normalize：补 signal_key/source，与 Fixture/Sidecar 同形，
            # 否则 _collect_handler 的 sig["signal_key"] 会 KeyError（native 采集全失败）。
            signals.append(_normalize({
                "source_url": f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else "",
                "author": user.get("nick_name") or user.get("nickname", ""),
                "posted_at": publish_time,
                "post_text": full_text,
            }, "xhs"))
        return signals


# ── Sidecar 采集器（真实，HTTP 调独立进程）──────────────────────────────────

class SidecarCollector:
    """HTTP 调用采集 sidecar。契约见 sidecar-contract.md。

    sidecar 失败/不可达 → 返回 []（雷达降级，不崩溃）。
    """

    def collect(self, keyword: str, limit: int) -> list[dict]:
        try:
            import requests
            resp = requests.post(
                f"{SIDECAR_URL}/collect",
                json={"source": "xhs", "keyword": keyword, "limit": limit},
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
    mode = os.environ.get("XHS_COLLECTOR", "fixture").lower()
    if mode == "native":
        return NativeCollector()
    if mode == "sidecar":
        return SidecarCollector()
    return FixtureCollector()


# ── Tool handler ───────────────────────────────────────────────────────────

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

    return {
        "ok": True,
        "data": {"signals": signals, "count": len(signals),
                 "collector": collector.__class__.__name__},
    }


# ── 注册 ─────────────────────────────────────────────────────────────────

registry.register(
    name="collect.xhs_intent",
    schema={
        "description": (
            "从小红书采集主动求购信号，按关键词检索并归一为 Signal 列表。"
            "采集器可替换（fixture/sidecar），由 env XHS_COLLECTOR 决定。"
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
    description="小红书意图信号采集（可替换采集器）",
)
