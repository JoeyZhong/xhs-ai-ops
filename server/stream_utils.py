"""
采集流工具 — 生产者端。

sync_collect_worker 运行在线程池线程中，通过
loop.call_soon_threadsafe + asyncio.Queue 将进度推送给 SSE 消费者，
避免阻塞 FastAPI 事件循环。
"""

from __future__ import annotations

import asyncio
import os
import random
import threading
from datetime import datetime
from typing import Optional

import pandas as pd

from apis.xhs_pc_apis import XHS_Apis
from storage.cookie_manager import get_cookie

try:
    from browser_search import search_notes
except ImportError:
    search_notes = None  # type: ignore[assignment]


def _parse_note_row(keyword: str, item: dict, goal_id: str = "") -> dict:
    nc = item.get("note_card", {})
    user = nc.get("user", {})
    interact = nc.get("interact_info", {})
    note_id = item.get("id", "")
    publish_time = next(
        (t.get("text", "") for t in nc.get("corner_tag_info", []) if t.get("type") == "publish_time"),
        "",
    )
    return {
        "采集时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "搜索关键词": keyword,
        "笔记ID": note_id,
        "标题": nc.get("display_title", ""),
        "笔记类型": nc.get("type", ""),
        "发布时间": publish_time,
        "作者昵称": user.get("nick_name") or user.get("nickname", ""),
        "作者ID": user.get("user_id", ""),
        "点赞数": interact.get("liked_count", ""),
        "收藏数": interact.get("collected_count", ""),
        "评论数": interact.get("comment_count", ""),
        "分享数": interact.get("shared_count", ""),
        "笔记链接": f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else "",
        "封面图": nc.get("cover", {}).get("url_default", ""),
        "goal_id": goal_id,
    }


def sync_collect_worker(
    keywords: list[str],
    queue: asyncio.Queue,
    loop: asyncio.AbstractEventLoop,
    account_id: str = "default",
    stop_event: Optional[threading.Event] = None,
    skip_api: bool = False,
    goal_id: str = "default",
    tenant_id: str = "default",
) -> None:
    """
    同步采集 Worker（线程池线程内运行）。

    每条笔记 emit 一个 progress 事件；关键词间用 stop_event.wait 替代
    time.sleep，确保客户端断连后能瞬间响应。finally 块保证无论是否被
    打断都会落盘并发送 done 信号。
    """
    if stop_event is None:
        stop_event = threading.Event()

    def emit(msg: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, msg)

    cookies_str = get_cookie(account_id, tenant_id=tenant_id) or os.environ.get("COOKIES", "")
    api = XHS_Apis()
    all_rows: list[dict] = []

    try:
        for kw_idx, keyword in enumerate(keywords):
            if skip_api:
                success, msg, notes = False, "跳过API，直接走浏览器兜底", []
                emit({"type": "fallback", "msg": f"跳过API，直接浏览器兜底: {keyword}"})
            else:
                success, msg, notes = api.search_some_note(keyword, 10, cookies_str, "general", 0)

            if not success:
                if not skip_api:
                    emit({"type": "fallback", "msg": f"API失败({msg})，切换浏览器兜底: {keyword}"})
                if search_notes is not None:
                    try:
                        success, msg, notes, _ = search_notes(
                            keyword, 10, cookies_str, headless=True, account_id=account_id
                        )
                        if not success:
                            emit({"type": "error", "msg": f"浏览器兜底失败: {msg}"})
                            notes = []
                    except Exception as exc:
                        emit({"type": "error", "msg": f"浏览器兜底异常: {exc}"})
                        notes = []
                else:
                    notes = []

            for item in notes:
                row = _parse_note_row(keyword, item, goal_id=goal_id)
                all_rows.append(row)
                emit({"type": "progress", "msg": f"抓取到笔记: {row['标题'][:30]}", "data": row})

            # interruptible inter-keyword delay (skip after last keyword)
            if kw_idx < len(keywords) - 1:
                if stop_event.wait(timeout=random.uniform(3.0, 6.0)):
                    break

    finally:
        # 落盘统一经 storage backend（消除双写路径）：
        #   - local 模式 → 文件名按 goal_id 前缀，与 list_collected_data 读路径对齐
        #   - postgres 模式 → 写入 collected_notes，否则 SSE 采集会静默丢数据
        # 不在写入时去重——保证 SSE 日志每条 = 落盘每行；跨文件去重在读取侧（notes API）完成。
        saved = None
        if all_rows:
            try:
                from storage.factory import get_backend
                backend = get_backend()
                saved = backend.save_collected_data(
                    tenant_id=tenant_id,
                    source="collect.stream",
                    df=pd.DataFrame(all_rows),
                    meta={"keywords": keywords, "goal_id": goal_id},
                )
            except Exception as exc:  # 落盘失败不阻断 done 信号
                emit({"type": "error", "msg": f"落盘失败: {exc}"})
        emit({
            "type": "done",
            "count": len(all_rows),
            "saved": str(saved) if saved else None,
        })
