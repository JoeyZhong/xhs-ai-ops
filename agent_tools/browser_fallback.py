"""
浏览器兜底 Tool（直接调用 browser_search 模块）。

通常情况下，agent 不会直接调用这两个 Tool —— search/hot_monitor 会内部 fallback。
这里注册它们是为了：
1. 让 Agent 有需要时能显式调用（比如已知 Cookie 失效，跳过 API 直接走浏览器）
2. 统一审计入口
3. 浏览器拿到新 Cookie 由 browser_search 内部自动持久化到 cookie_manager
   （工具层无需再做任何 Cookie 写入）
"""

from __future__ import annotations

from agent_tools import registry
from agent_tools.registry import ToolContext


def _resolve_account(args: dict, ctx: ToolContext) -> str:
    # 优先级：args.account_id > ctx.extra["account_id"] > ctx.tenant_id > "default"
    return (args.get("account_id")
            or (ctx.extra or {}).get("account_id")
            or (ctx.tenant_id if ctx.tenant_id and ctx.tenant_id != "default"
                else None)
            or "default")


def _search_notes_handler(args: dict, ctx: ToolContext) -> dict:
    from browser_search import search_notes
    account_id = _resolve_account(args, ctx)
    success, msg, notes, new_ck = search_notes(
        keyword=args["keyword"],
        max_results=args.get("max_results", 10),
        cookies_str=args.get("cookies_str", ""),
        headless=args.get("headless", True),
        account_id=account_id,
    )
    return {
        "ok": success,
        "data": {
            "notes": notes,
            "new_cookies_persisted": bool(new_ck),
            "account_id": account_id,
        },
        "error": None if success else msg,
    }


def _suggest_handler(args: dict, ctx: ToolContext) -> dict:
    from browser_search import get_keyword_suggestions
    account_id = _resolve_account(args, ctx)
    success, msg, suggestions, new_ck = get_keyword_suggestions(
        keyword=args["keyword"],
        cookies_str=args.get("cookies_str", ""),
        headless=args.get("headless", True),
        account_id=account_id,
    )
    return {
        "ok": success,
        "data": {
            "suggestions": suggestions,
            "new_cookies_persisted": bool(new_ck),
            "account_id": account_id,
        },
        "error": None if success else msg,
    }


# ── 注册 ─────────────────────────────────────────────────────────────────

registry.register(
    name="browser_fallback.search_notes",
    schema={
        "description": ("Browser-based XHS note search via Playwright. "
                          "New cookies are auto-persisted to cookie_manager."),
        "parameters": {
            "type": "object",
            "required": ["keyword"],
            "properties": {
                "keyword":     {"type": "string"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 30, "default": 10},
                "cookies_str": {"type": "string"},
                "account_id":  {"type": "string",
                                   "description": "Account id for cookie persistence."},
                "headless":    {"type": "boolean", "default": True},
            },
        },
    },
    handler=_search_notes_handler,
    cost_estimate=45.0,
    description="Playwright-based XHS search fallback",
)

registry.register(
    name="browser_fallback.suggest_keywords",
    schema={
        "description": ("Browser-based XHS keyword suggestion fetcher. "
                          "New cookies are auto-persisted to cookie_manager."),
        "parameters": {
            "type": "object",
            "required": ["keyword"],
            "properties": {
                "keyword":     {"type": "string"},
                "cookies_str": {"type": "string"},
                "account_id":  {"type": "string",
                                   "description": "Account id for cookie persistence."},
                "headless":    {"type": "boolean", "default": True},
            },
        },
    },
    handler=_suggest_handler,
    cost_estimate=30.0,
    description="Playwright-based keyword suggestion fallback",
)
