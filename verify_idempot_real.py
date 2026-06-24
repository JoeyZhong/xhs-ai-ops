#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P1.3 Idempotency 真实场景验收
============================

设计意图：在 registry.invoke 层，相同 args 第二次必须命中缓存、
不消耗真 Kimi API、duration_ms=0、meta.idempotency_hit=True。

为什么需要这个脚本：
- verify_phase5_p1.py 的 S4 用 mock handler 验证了机制本身。
- 但用户跑完整 agent loop 时缓存永不命中，根因是 LLM 在
  temperature=0.6 下生成的 tool_call args 每次都不同——这不是
  P1.3 的问题，是测试方法错配。
- 本脚本直接在 registry 层用确定性 args 触发真 Kimi handler，
  对应 P1.3 真实使用场景（Streamlit 表单 rerun 重复提交）。

运行：python verify_idempot_real.py
代价：第一次会消耗 1 次真 Kimi API（约 3-5 秒）。第二次起命中缓存。
"""

from __future__ import annotations

import sys
import time

# 触发 kimi.complete 注册（导入即注册）
from agent_tools import kimi  # noqa: F401
from agent_tools import registry as tool_registry
from agent_tools.idempotency import IdempotencyCache
from agent_tools.registry import ToolContext


TEST_TENANT = "idempot_real_test"


def _section(title: str) -> None:
    print(f"\n{'='*60}\n  {title}\n{'-'*60}")


_results: list[tuple[str, bool, str]] = []


def _check(name: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    mark = "[+]" if condition else "[X]"
    line = f"  {mark} {status}  {name}"
    if detail:
        line += f"  <- {detail}"
    print(line)
    _results.append((name, condition, detail))
    return condition


# ── 准备：清空测试租户的缓存（不污染默认租户） ──────────────────────────
_section("准备 · 清空 idempot_real_test 租户缓存")

cache = IdempotencyCache(TEST_TENANT)
cache.clear()
# 同时清掉 registry 内的 cache 实例引用
tool_registry._IDEMPOT_CACHES.pop(TEST_TENANT, None)
_check("缓存已清空", cache.get("any-key") is None)


# ── 第一次调用：真 Kimi，期待真实耗时 ──────────────────────────────────
_section("第 1 次调用 · 应触发真 Kimi API")

ctx = ToolContext(tenant_id=TEST_TENANT, task_id="task-real-001")
args = {"prompt": "用一句话介绍小红书。", "max_tokens": 200, "temperature": 0.6}

t0 = time.perf_counter()
r1 = tool_registry.invoke("kimi.complete", args, ctx)
t1_elapsed = int((time.perf_counter() - t0) * 1000)

_check("第 1 次 ok=True", r1.get("ok") is True, f"error={r1.get('error')}")
_check("第 1 次有真实耗时 (>500ms)", t1_elapsed > 500, f"elapsed={t1_elapsed}ms")
_check("第 1 次未命中缓存",
       r1.get("meta", {}).get("idempotency_hit") is not True,
       f"meta={r1.get('meta')}")

content_1 = (r1.get("data") or {}).get("content", "")
print(f"  > 第 1 次返回内容（前 60 字）: {content_1[:60]}...")


# ── 第二次调用：完全相同 args，应命中缓存 ────────────────────────────
_section("第 2 次调用 · 完全相同 args，应命中缓存（无真 API）")

t0 = time.perf_counter()
r2 = tool_registry.invoke("kimi.complete", args, ctx)
t2_elapsed = int((time.perf_counter() - t0) * 1000)

_check("第 2 次 ok=True", r2.get("ok") is True)
_check("第 2 次 idempotency_hit=True",
       r2.get("meta", {}).get("idempotency_hit") is True,
       f"meta={r2.get('meta')}")
_check("第 2 次耗时近 0ms (<100ms)", t2_elapsed < 100,
       f"elapsed={t2_elapsed}ms")
_check("第 2 次 meta.duration_ms=0",
       r2.get("meta", {}).get("duration_ms") == 0)

content_2 = (r2.get("data") or {}).get("content", "")
_check("第 2 次内容与第 1 次完全一致", content_1 == content_2,
       "内容不一致 → 没命中或命中错记录")


# ── 第三次调用：不同 args，应不命中 ──────────────────────────────────
_section("第 3 次调用 · 不同 prompt，应不命中（再触发真 API）")

args_diff = dict(args, prompt="用一句话介绍微博。")
t0 = time.perf_counter()
r3 = tool_registry.invoke("kimi.complete", args_diff, ctx)
t3_elapsed = int((time.perf_counter() - t0) * 1000)

_check("第 3 次 ok=True", r3.get("ok") is True)
_check("第 3 次未命中缓存",
       r3.get("meta", {}).get("idempotency_hit") is not True)
_check("第 3 次有真实耗时 (>500ms)", t3_elapsed > 500,
       f"elapsed={t3_elapsed}ms")


# ── 第四次调用：回到第一组 args，验证仍可命中 ────────────────────────
_section("第 4 次调用 · 回到第 1 组 args，应仍命中缓存")

t0 = time.perf_counter()
r4 = tool_registry.invoke("kimi.complete", args, ctx)
t4_elapsed = int((time.perf_counter() - t0) * 1000)

_check("第 4 次 idempotency_hit=True",
       r4.get("meta", {}).get("idempotency_hit") is True)
_check("第 4 次内容仍等于第 1 次",
       (r4.get("data") or {}).get("content") == content_1)


# ── 总结 ─────────────────────────────────────────────────────────────
print(f"\n{'='*60}\n  耗时对照\n{'-'*60}")
print(f"  第 1 次（真 API）       : {t1_elapsed:>6} ms")
print(f"  第 2 次（cache hit）    : {t2_elapsed:>6} ms")
print(f"  第 3 次（不同 args）    : {t3_elapsed:>6} ms")
print(f"  第 4 次（cache hit 再）: {t4_elapsed:>6} ms")

total = len(_results)
passed = sum(1 for _, ok, _ in _results if ok)
failed = total - passed
print(f"\n{'='*60}\n  结果：{passed}/{total} 通过")
if failed:
    print(f"  失败 {failed} 项：")
    for name, ok, detail in _results:
        if not ok:
            print(f"    [X] {name}" + (f": {detail}" if detail else ""))
else:
    print("  P1.3 在设计意图下工作正常 ✓")
print('='*60)

# 清理测试租户的缓存文件
cache.clear()

sys.exit(0 if failed == 0 else 1)
