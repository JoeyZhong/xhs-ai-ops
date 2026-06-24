"""LLM 调用心跳。

LLM 调用可能因模型慢 / 60s 超时重试而累计很久（实测主 Agent 首轮见过 ~266s：
DeepSeek 单次 >60s 超时 → 重试 3 次 + 8/24s 退避）。这期间编排器或子 agent 不产任何
事件，前端 120s 空闲计时器会误判"无响应"而断开。

`call_with_heartbeat` 在阻塞调用期间起一个后台 ticker 线程，按 interval 周期回调
`on_beat(tick)`（通常用来 emit 一个 heartbeat 事件），喂活前端空闲计时器并显示"正在思考"。
心跳来自承载调用的同一进程，调用一返回就停——不会在真卡死后还假装活着超过一个 interval。
"""
from __future__ import annotations

import threading
from typing import Any, Callable

DEFAULT_INTERVAL_S = 15.0


def call_with_heartbeat(call: Callable[[], Any],
                        on_beat: Callable[[int], None],
                        interval: float = DEFAULT_INTERVAL_S) -> Any:
    """运行阻塞的 `call()`，期间每 `interval` 秒回调一次 `on_beat(tick)`。返回 call() 的结果。"""
    stop = threading.Event()

    def _tick() -> None:
        n = 0
        while not stop.wait(interval):
            n += 1
            try:
                on_beat(n)
            except Exception:
                pass

    t = threading.Thread(target=_tick, daemon=True)
    t.start()
    try:
        return call()
    finally:
        stop.set()
        t.join(timeout=1.0)
