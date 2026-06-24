"""
小红书笔记采集 Tool（包装 run_search.py 的核心逻辑）。

设计：把原 run_search.search_and_collect() 拆出可复用的 collect_for_keyword()，
Tool 调用此函数；CLI 入口（run_search.py）也调用此函数。
"""

from __future__ import annotations

import random
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from agent_tools import registry
from agent_tools.registry import ToolContext


# ── 核心可复用函数（CLI 和 Tool 共用） ───────────────────────────────────

def collect_for_keyword(keyword: str,
                          max_per_keyword: int,
                          cookies_str: str,
                          enable_browser_fallback: bool = True,
                          account_id: str = "default",
                          goal_id: str = "") -> tuple[list[dict], str]:
    """
    采集单个关键词的笔记。

    Args:
        keyword:                 搜索词
        max_per_keyword:         本关键词笔记上限
        cookies_str:             当前 Cookie（来自 cookie_manager）
        enable_browser_fallback: API 失败时是否切到浏览器
        account_id:              浏览器兜底成功后写入哪个账号

    Returns:
        (笔记列表, 错误消息, 生效Cookie)。成功 (notes, "", cookie)；失败 ([], error_msg, cookie)。
        生效Cookie：若浏览器兜底刷新了 Cookie 则为新值，否则为传入值——供批量复用。
    """
    from apis.xhs_pc_apis import XHS_Apis
    api = XHS_Apis()

    effective_cookie = cookies_str  # 浏览器兜底刷新后回传，供 collect_batch 复用走快路径

    success, msg, notes = api.search_some_note(
        keyword, max_per_keyword, cookies_str, "general", 0,
    )

    if not success and enable_browser_fallback:
        from browser_search import search_notes as browser_search
        bsuccess, bmsg, notes, new_ck = browser_search(
            keyword, max_per_keyword, cookies_str,
            headless=True, account_id=account_id,
        )
        # 浏览器内部会把新 Cookie 持久化到 cookie_manager；这里同时回传，
        # 让 collect_batch 后续关键词改走快的 API 路径，而非每个词都重启浏览器。
        if not bsuccess:
            return [], f"API: {msg} | Browser: {bmsg}", effective_cookie
        if new_ck:
            effective_cookie = new_ck
        success = True

    if not success:
        return [], msg, effective_cookie

    # 标准化为 result dict
    results = []
    for item in notes:
        note_card = item.get("note_card", {}) or {}
        user = note_card.get("user", {}) or {}
        interact = note_card.get("interact_info", {}) or {}
        note_id = item.get("id", "")

        publish_time = ""
        for tag in note_card.get("corner_tag_info", []) or []:
            if tag.get("type") == "publish_time":
                publish_time = tag.get("text", "")
                break

        results.append({
            "采集时间":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "搜索关键词": keyword,
            "笔记ID":    note_id,
            "标题":      note_card.get("display_title", ""),
            "笔记类型":  note_card.get("type", ""),
            "发布时间":  publish_time,
            "作者昵称":  user.get("nick_name") or user.get("nickname", ""),
            "作者ID":    user.get("user_id", ""),
            "点赞数":    interact.get("liked_count", ""),
            "收藏数":    interact.get("collected_count", ""),
            "评论数":    interact.get("comment_count", ""),
            "分享数":    interact.get("shared_count", ""),
            "笔记链接":  f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else "",
            "封面图":    note_card.get("cover", {}).get("url_default", ""),
            "goal_id":  goal_id,
        })
    return results, "", effective_cookie


def collect_batch(keywords: list[str],
                    max_per_keyword: int,
                    cookies_str: str,
                    delay_min: float = 3.0,
                    delay_max: float = 6.0,
                    progress_print: bool = False,
                    account_id: str = "default",
                    goal_id: str = "",
                    time_budget_s: float = 60.0,
                    progress_cb=None) -> dict:
    """
    批量采集多个关键词。返回 {records, errors, stats}。
    account_id 仅用于浏览器兜底成功时把新 Cookie 写到对应账号。

    串行是刻意的反封号设计（XHS 对单账号高频/并发搜索敏感），不并行。两点提速 / 防卡：
    ① 浏览器兜底刷新 Cookie 后，后续关键词复用它走快的 API 路径，不再每个词都重启浏览器；
    ② time_budget_s 墙钟预算：超预算即停止开新关键词、剩余标记跳过，
       避免单次工具调用把整轮拖过前端 120s 空闲超时（>0 生效，<=0 关闭）。
    """
    all_records: list[dict] = []
    errors: dict[str, str] = {}
    current_cookie = cookies_str
    start = time.monotonic()

    for kw_idx, keyword in enumerate(keywords):
        # 墙钟预算：第 2 个词起，超预算就停止开新词（首词无论如何先跑完）
        if (kw_idx > 0 and time_budget_s > 0
                and time.monotonic() - start > time_budget_s):
            for remaining in keywords[kw_idx:]:
                errors[remaining] = "skipped: time budget exceeded"
            if progress_print:
                print(f"   ⏱️ 超 {time_budget_s:.0f}s 预算，跳过剩余 {len(keywords) - kw_idx} 个关键词")
            break

        # 每个关键词发一次进度心跳：喂活前端空闲计时器（采集是单次工具调用、耗时长，
        # 否则子 agent 一个 iteration 内无事件 → 易触发 120s 超时），同时给用户进度反馈。
        if progress_cb:
            try:
                progress_cb("running", kw_idx + 1,
                            f"采集「{keyword}」{kw_idx + 1}/{len(keywords)}")
            except Exception:
                pass

        if progress_print:
            print(f"\n[{kw_idx+1}/{len(keywords)}] 搜索关键词：{keyword}")

        try:
            notes, err, current_cookie = collect_for_keyword(
                keyword, max_per_keyword, current_cookie,
                account_id=account_id, goal_id=goal_id,
            )
            if err:
                errors[keyword] = err
                if progress_print:
                    print(f"   ⚠️ {err}")
            else:
                all_records.extend(notes)
                if progress_print:
                    print(f"   ✓ {len(notes)} 条")
                    for note in notes[:3]:
                        print(f"      · {note['标题'][:40]}")
        except Exception as e:
            errors[keyword] = str(e)
            if progress_print:
                print(f"   ❌ 异常: {e}")

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
            "total_notes": len(all_records),
        },
    }


# ── Tool handler ─────────────────────────────────────────────────────────

def _collect_notes_handler(args: dict, ctx: ToolContext) -> dict:
    # account_id 解析优先级：args > ctx.extra["account_id"] > ctx.tenant_id > "default"
    account_id = (args.get("account_id")
                    or (ctx.extra or {}).get("account_id")
                    or (ctx.tenant_id if ctx.tenant_id and ctx.tenant_id != "default"
                        else None)
                    or "default")

    # Cookie 解析
    cookies_str = args.get("cookies_str") or ""
    if not cookies_str:
        try:
            from storage.cookie_manager import get_cookie
            cookies_str = get_cookie(account_id) or ""
        except Exception as e:
            cookies_str = ""
        if not cookies_str:
            import os as _os
            cookies_str = _os.environ.get("COOKIES", "")

    goal_id = args.get("goal_id", "")

    result = collect_batch(
        keywords=args["keywords"],
        max_per_keyword=args.get("max_per_keyword", 10),
        cookies_str=cookies_str,
        delay_min=args.get("delay_min", 3.0),
        delay_max=args.get("delay_max", 6.0),
        progress_print=False,
        account_id=account_id,
        goal_id=goal_id,
        progress_cb=(ctx.extra or {}).get("progress_cb"),
    )

    # P3.3: output_dir 覆盖 — 写到指定目录而非 storage
    output_dir = args.get("output_dir")
    if output_dir and result["records"]:
        import pandas as pd
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        xlsx = out_path / f"health_check_{ts}.xlsx"
        pd.DataFrame(result["records"]).to_excel(xlsx, index=False)
        # 保留最近 3 份快照，清理更早的
        _clean_old_snapshots(out_path, keep=3)

    # 写入 storage（保留 Excel 兼容，仅当 output_dir 未设置时）
    saved_path = None
    if not output_dir and ctx.storage and result["records"]:
        import pandas as pd
        df = pd.DataFrame(result["records"])
        try:
            saved_path = ctx.storage.save_collected_data(
                tenant_id=ctx.tenant_id,
                source="search.collect_notes",
                df=df,
                meta={"keywords": args["keywords"], "goal_id": goal_id},
            )
        except Exception as e:
            result["errors"]["_storage"] = str(e)

    return {
        "ok": True,
        "data": {
            "records": result["records"],
            "errors": result["errors"],
            "stats":  result["stats"],
            "saved_path": saved_path,
            "account_id": account_id,
        },
    }


def _clean_old_snapshots(directory: Path, keep: int = 3) -> None:
    """保留最近 keep 份 health_check_*.xlsx，更早的删除。"""
    files = sorted(directory.glob("health_check_*.xlsx"), reverse=True)
    for f in files[keep:]:
        try:
            f.unlink()
        except Exception:
            pass


# ── 注册 ─────────────────────────────────────────────────────────────────

registry.register(
    name="search.collect_notes",
    schema={
        "description": "Collect XHS notes for given keywords. Auto-fallback to browser when API cookies fail.",
        "parameters": {
            "type": "object",
            "required": ["keywords"],
            "properties": {
                "keywords":        {"type": "array", "items": {"type": "string"},
                                       "minItems": 1, "maxItems": 20,
                                       "description": "Keywords to search"},
                "max_per_keyword": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                "cookies_str":     {"type": "string",
                                       "description": ("XHS cookies (optional). "
                                                          "If empty, read from cookie_manager by account_id, "
                                                          "then fall back to env COOKIES.")},
                "account_id":      {"type": "string",
                                       "description": ("Account id for cookie_manager lookup. "
                                                          "Defaults to ctx.tenant_id or 'default'.")},
                "delay_min":       {"type": "number", "minimum": 1.0},
                "delay_max":       {"type": "number", "minimum": 1.0},
                "output_dir":      {"type": "string",
                                       "description": ("(cookie_health only) Save results to this directory "
                                                          "instead of default storage. Auto-cleans old snapshots, "
                                                          "keeping the 3 most recent.")},
                "goal_id":         {"type": "string", "default": "",
                                       "description": "Goal ID to tag collected data (for multi-goal isolation)"},
            },
        },
    },
    handler=_collect_notes_handler,
    cost_estimate=30.0,  # ~30s per call
    description="XHS note collector with browser fallback",
)
