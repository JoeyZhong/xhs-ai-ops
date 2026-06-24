"""
小红书热词监控 Tool（包装 hot_trend_monitor.py 的核心逻辑）。
"""

from __future__ import annotations

import random
import time
from datetime import datetime

from agent_tools import registry
from agent_tools.registry import ToolContext


def fetch_suggestions(keyword: str, cookies_str: str,
                        enable_browser_fallback: bool = True,
                        account_id: str = "default") -> tuple[list[dict], str]:
    """
    获取单个关键词的搜索建议词。
    account_id 仅用于浏览器兜底成功时把新 Cookie 写到对应账号。
    返回 (建议列表, 错误消息)。
    """
    from apis.xhs_pc_apis import XHS_Apis
    api = XHS_Apis()
    success, msg, res_json = api.get_search_keyword(keyword, cookies_str)

    if (not success or res_json is None) and enable_browser_fallback:
        from browser_search import get_keyword_suggestions
        bsuccess, bmsg, results, _new_ck = get_keyword_suggestions(
            keyword, cookies_str, headless=True, account_id=account_id,
        )
        # 新 Cookie 由浏览器内部自动写入 cookie_manager
        if bsuccess:
            return results, ""
        return [], f"API: {msg} | Browser: {bmsg}"

    if not success or res_json is None:
        return [], msg or "empty response"

    data = res_json.get("data") or {}
    words_list = (
        data.get("sug_items")
        or data.get("suggest_words")
        or data.get("recommend_words")
        or data.get("words")
        or data.get("keywords")
        or (data if isinstance(data, list) else [])
    )

    results = []
    for item in words_list:
        if isinstance(item, str):
            word, score = item, ""
        elif isinstance(item, dict):
            word = (item.get("text") or item.get("words") or item.get("word")
                    or item.get("keyword") or item.get("name") or "")
            score = (item.get("score") or item.get("heat")
                     or item.get("search_volume") or item.get("hot_value") or "")
        else:
            continue
        if word:
            results.append({"热搜词": str(word), "搜索热度": score})
    return results, ""


def monitor_batch(keywords: list[str],
                    cookies_str: str,
                    delay_min: float = 2.0,
                    delay_max: float = 5.0,
                    progress_print: bool = False,
                    account_id: str = "default") -> dict:
    """批量监控热词，返回 {records, errors, stats}。account_id 用于兜底回写。"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    all_records: list[dict] = []
    errors: dict[str, str] = {}

    for kw_idx, keyword in enumerate(keywords):
        if progress_print:
            print(f"\n🔍 关键词：{keyword}")

        items, err = fetch_suggestions(keyword, cookies_str,
                                          account_id=account_id)
        if err:
            errors[keyword] = err
            if progress_print:
                print(f"   ⚠️ {err}")
        else:
            for item in items:
                all_records.append({
                    "关键词":   keyword,
                    "热搜词":   item["热搜词"],
                    "搜索热度": item["搜索热度"],
                    "采集时间": now_str,
                })
            if progress_print:
                preview = "、".join(i["热搜词"] for i in items[:5])
                print(f"   ✓ {len(items)} 个：{preview}")

        if kw_idx < len(keywords) - 1:
            delay = random.uniform(delay_min, delay_max)
            if progress_print:
                print(f"   ⏳ 等待 {delay:.1f}s...")
            time.sleep(delay)

    return {
        "records": all_records,
        "errors": errors,
        "stats": {
            "total_keywords": len(keywords),
            "successful_keywords": len(keywords) - len(errors),
            "total_suggestions": len(all_records),
        },
    }


# ── Tool handler ─────────────────────────────────────────────────────────

def _suggest_handler(args: dict, ctx: ToolContext) -> dict:
    # 优先级：args.account_id > ctx.extra["account_id"] > ctx.tenant_id > "default"
    account_id = (args.get("account_id")
                    or (ctx.extra or {}).get("account_id")
                    or (ctx.tenant_id if ctx.tenant_id and ctx.tenant_id != "default"
                        else None)
                    or "default")

    cookies_str = args.get("cookies_str") or ""
    if not cookies_str:
        try:
            from storage.cookie_manager import get_cookie
            cookies_str = get_cookie(account_id) or ""
        except Exception:
            cookies_str = ""
        if not cookies_str:
            import os as _os
            cookies_str = _os.environ.get("COOKIES", "")

    if not cookies_str:
        return {
            "ok": False,
            "error": (f"no cookie for account_id='{account_id}'. "
                       "Add it via Dashboard ⚙️ API 配置 or set env COOKIES."),
        }

    result = monitor_batch(
        keywords=args["keywords"],
        cookies_str=cookies_str,
        delay_min=args.get("delay_min", 2.0),
        delay_max=args.get("delay_max", 5.0),
        progress_print=False,
        account_id=account_id,
    )

    saved_path = None
    if ctx.storage and result["records"]:
        import pandas as pd
        df = pd.DataFrame(result["records"], columns=["关键词", "热搜词", "搜索热度", "采集时间"])
        try:
            saved_path = ctx.storage.save_hot_keywords(ctx.tenant_id, df)
        except Exception as e:
            result["errors"]["_storage"] = str(e)

    return {
        "ok": True,
        "data": {**result, "saved_path": saved_path, "account_id": account_id},
    }


# ── 注册 ─────────────────────────────────────────────────────────────────

registry.register(
    name="hot_monitor.suggest_keywords",
    schema={
        "description": "Get XHS search suggestions for given keywords. Auto-fallback to browser.",
        "parameters": {
            "type": "object",
            "required": ["keywords"],
            "properties": {
                "keywords":    {"type": "array", "items": {"type": "string"},
                                  "minItems": 1, "maxItems": 20},
                "cookies_str": {"type": "string",
                                  "description": ("XHS cookies (optional). "
                                                     "If empty, read from cookie_manager by account_id, "
                                                     "then env COOKIES.")},
                "account_id":  {"type": "string",
                                  "description": ("Account id for cookie_manager lookup. "
                                                     "Defaults to ctx.tenant_id or 'default'.")},
                "delay_min":   {"type": "number", "minimum": 1.0},
                "delay_max":   {"type": "number", "minimum": 1.0},
            },
        },
    },
    handler=_suggest_handler,
    cost_estimate=15.0,
    description="XHS keyword suggestion fetcher",
)
