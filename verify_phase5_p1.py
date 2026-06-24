#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 5 P1 验收测试：OCC + Idempotency + LLMProvider
运行方式：python verify_phase5_p1.py
"""

import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

_results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = ""):
    status = "PASS" if condition else "FAIL"
    mark = "[+]" if condition else "[X]"
    line = f"  {mark} {status}  {name}"
    if detail:
        line += f"  <- {detail}"
    print(line)
    _results.append((name, condition, detail))
    return condition


def section(title: str):
    print(f"\n{'='*60}\n  {title}\n{'-'*60}")


def summary():
    total = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = total - passed
    print(f"\n{'='*60}\n  结果：{passed}/{total} 通过")
    if failed:
        print(f"  失败 {failed} 项")
        for name, ok, detail in _results:
            if not ok:
                print(f"    [X] {name}" + (f": {detail}" if detail else ""))
    else:
        print("  全部通过")
    print('='*60)
    return failed == 0


# ── S1 · OCC 基础 ──────────────────────────────────────────────────────────

section("S1 · OCC 基础 (MemoryLayer v2)")

from agents.memory import (
    MemoryLayer, Entry, parse_entries, serialize_entries,
    WritePermissionDenied, MemoryInjectionDetected, WriteConflictError,
)

# S1.1 Entry 数据类
entry = Entry(id="test", body="hello", rev=3)
check("S1.1 Entry 有 rev", entry.rev == 3)

# S1.2 parse_entries 解析 §rev
md = "# Header\n\n§id: a-1 §rev: 2\nbody A\n\n§id: b-2 §rev: 5\nbody B"
header, entries = parse_entries(md)
check("S1.2 parse 解析 rev", entries["a-1"].rev == 2 and entries["b-2"].rev == 5)
check("S1.3 parse body 正确", entries["a-1"].body == "body A")

# S1.4 serialize 写 §rev
serialized = serialize_entries(header, entries)
check("S1.4 serialize 含 §rev", "§rev: 2" in serialized and "§rev: 5" in serialized)

# S1.5 往返一致
h2, e2 = parse_entries(serialized)
check("S1.5 往返 rev 一致", e2["a-1"].rev == 2 and e2["b-2"].rev == 5)
check("S1.6 往返 body 一致", e2["a-1"].body == "body A")

# S1.7 旧格式兼容（无 §rev）
old_md = "§id: old-entry\nold body"
h_old, e_old = parse_entries(old_md)
check("S1.7 旧 entry rev=0", e_old["old-entry"].rev == 0)

# S1.8 空内容
h_empty, e_empty = parse_entries("")
check("S1.8 空内容", h_empty == "" and e_empty == {})


# ── S2 · OCC 写入操作 ──────────────────────────────────────────────────────

section("S2 · OCC 写入操作")

from storage import get_backend

with tempfile.TemporaryDirectory() as tmpdir:
    backend = get_backend({"storage_backend": "local", "local_storage_root": tmpdir})
    mem = MemoryLayer(storage=backend)

    # S2.1 add_entry 设 rev=1
    mem.add_entry("default", "content", "playbook.md",
                   "entry-1", "content v1", "analyst")
    body, rev = mem.read_entry("default", "content", "playbook.md", "entry-1")
    check("S2.1 add_entry rev=1", rev == 1, f"got rev={rev}")

    # S2.2 replace_entry 返回新 rev
    new_rev = mem.replace_entry("default", "content", "playbook.md",
                                 "entry-1", "content v2", "analyst")
    check("S2.2 replace 返回 rev=2", new_rev == 2, f"got rev={new_rev}")

    # S2.3 replace 后 read_entry
    body, rev = mem.read_entry("default", "content", "playbook.md", "entry-1")
    check("S2.3 replace 后内容更新", body == "content v2" and rev == 2)

    # S2.4 replace 不存在 → rev=1
    new_rev = mem.replace_entry("default", "content", "playbook.md",
                                 "new-entry", "new body", "analyst")
    check("S2.4 replace 不存在 rev=1", new_rev == 1)

    # S2.5 expected_rev 匹配成功
    new_rev = mem.replace_entry("default", "content", "playbook.md",
                                 "entry-1", "content v3", "analyst",
                                 expected_rev=2)
    check("S2.5 expected_rev=2 匹配", new_rev == 3)

    # S2.6 expected_rev 不匹配 → WriteConflictError
    try:
        mem.replace_entry("default", "content", "playbook.md",
                           "entry-1", "x", "analyst", expected_rev=99)
        check("S2.6 expected_rev 不匹配抛异常", False)
    except WriteConflictError as e:
        check("S2.6 expected_rev 不匹配抛 WriteConflictError",
              e.expected == 99 and e.actual == 3)

    # S2.7 remove_entry expected_rev 匹配
    mem.add_entry("default", "content", "playbook.md",
                   "rm-test", "to remove", "analyst")
    op = mem.remove_entry("default", "content", "playbook.md",
                           "rm-test", "analyst", expected_rev=1)
    check("S2.7 remove expected_rev=1", op == "removed")

    # S2.8 remove_entry expected_rev 不匹配
    mem.add_entry("default", "content", "playbook.md",
                   "rm-test2", "to remove2", "analyst")
    try:
        mem.remove_entry("default", "content", "playbook.md",
                          "rm-test2", "analyst", expected_rev=99)
        check("S2.8 remove expected_rev 不匹配抛异常", False)
    except WriteConflictError as e:
        check("S2.8 remove expected_rev 不匹配", e.expected == 99)

    # S2.9 get_entry 向后兼容（只返回 body）
    mem.add_entry("default", "content", "playbook.md",
                   "get-test", "body only", "analyst")
    val = mem.get_entry("default", "content", "playbook.md", "get-test")
    check("S2.9 get_entry 返回 body", val == "body only")

    # S2.10 list_entries 返回 Entry 对象
    entries = mem.list_entries("default", "content", "playbook.md")
    check("S2.10 list_entries 返回 Entry",
          all(isinstance(e, Entry) for e in entries.values()))


# ── S3 · Idempotency ───────────────────────────────────────────────────────

section("S3 · Idempotency 中间件")

from agent_tools.idempotency import (
    compute_key, IdempotencyCache, is_idempotency_applicable,
)

# S3.1 compute_key 相同输入相同输出
k1 = compute_key("tool.a", {"x": 1}, "intel", "t1")
k2 = compute_key("tool.a", {"x": 1}, "intel", "t1")
check("S3.1 compute_key 相同", k1 == k2 and len(k1) == 32)

# S3.2 compute_key 不同输入不同输出
k3 = compute_key("tool.a", {"x": 2}, "intel", "t1")
k4 = compute_key("tool.a", {"x": 1}, "content", "t1")
check("S3.2 compute_key 不同 args", k1 != k3)
check("S3.3 compute_key 不同 role", k1 != k4)

# S3.3a compute_key 不同 task_id 应得相同 key（核心不变量：跨 task 复用缓存）
k_t1 = compute_key("tool.a", {"x": 1}, "intel", "task-001")
k_t2 = compute_key("tool.a", {"x": 1}, "intel", "task-999")
check("S3.3a compute_key task_id 不影响 hash", k_t1 == k_t2)

# S3.3 IdempotencyCache get 未命中
cache = IdempotencyCache("test-tenant")
cache.clear()
result = cache.get("nonexistent")
check("S3.4 cache get 未命中", result is None)

# S3.4 set/get 命中
cache.set("key1", {"ok": True, "data": "hello"}, "tool.a")
result = cache.get("key1")
check("S3.5 cache get 命中", result is not None and result["data"] == "hello")

# S3.5 失败结果不入 cache
cache.set("key2", {"ok": False, "error": "fail"}, "tool.a")
result = cache.get("key2")
check("S3.6 失败结果不入 cache", result is None)

# S3.6 白名单判断
check("S3.7 kimi.complete 在白名单", is_idempotency_applicable("kimi.complete"))
check("S3.8 search.collect_notes 不在白名单",
      not is_idempotency_applicable("search.collect_notes"))

# S3.7 持久化加载（重启后 cache 仍在）
cache3 = IdempotencyCache("test-tenant")
result = cache3.get("key1")
check("S3.9 持久化后仍命中", result is not None and result["data"] == "hello")

# 清理
cache.clear()
cache3.clear()


# ── S4 · registry.invoke idempotency 集成 ──────────────────────────────────

section("S4 · registry.invoke idempotency 集成")

from agent_tools import registry as tool_registry

# 清除之前测试的状态
tool_registry._reset_for_tests()

# 注册一个测试工具
def _dummy_handler(args, ctx):
    return {"ok": True, "data": args.get("value", "default")}

tool_registry.register(
    name="kimi.complete",
    schema={
        "description": "Test",
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
        },
    },
    handler=_dummy_handler,
)

# S4.1 第一次调用 → 执行 handler
ctx = tool_registry.ToolContext(tenant_id="tid", task_id="tk")
r1 = tool_registry.invoke("kimi.complete", {"value": "v1"}, ctx)
check("S4.1 第一次调用成功", r1["ok"] and r1["data"] == "v1")

# S4.2 第二次相同调用 → 命中 cache，返回上次结果（带 idempotency_hit）
r2 = tool_registry.invoke("kimi.complete", {"value": "v1"}, ctx)
check("S4.2 第二次命中 cache", r2.get("meta", {}).get("idempotency_hit") is True)
check("S4.3 cache 结果一致", r2["data"] == "v1")

# S4.3 不同 args → 不命中 cache
r3 = tool_registry.invoke("kimi.complete", {"value": "v2"}, ctx)
check("S4.4 不同 args 不命中",
      r3.get("meta", {}).get("idempotency_hit") is not True,
      f"meta={r3.get('meta')}")


# ── S5 · LLMProvider ───────────────────────────────────────────────────────

section("S5 · LLMProvider 抽象")

from agent_tools.llm_provider import (
    KimiProvider, MockProvider, FailoverProvider,
    _default_provider, set_provider, _load_settings,
)

# S5.1 KimiProvider 初始化
kp = KimiProvider(api_key="test-key", base_url="https://test.com", model="m")
check("S5.1 KimiProvider 属性", kp.api_key == "test-key" and kp.model == "m")

# S5.2 MockProvider 返回固定响应
mp = MockProvider(fixed_content="mock response", fixed_tool_calls=[])
msg, err, tokens = mp.call_chat_completions([], [], 100, 0.5)
check("S5.2 MockProvider content", msg.content == "mock response")
check("S5.3 MockProvider 无 error", err is None)

# S5.3 FailoverProvider primary 成功
primary = MockProvider(fixed_content="primary")
fallback = MockProvider(fixed_content="fallback")
fp = FailoverProvider(primary, fallback, fail_threshold=2)
msg, err, tokens = fp.call_chat_completions([], [], 100, 0.5)
check("S5.4 Failover primary 成功", msg.content == "primary")

# S5.4 FailoverProvider 连续失败切 fallback
class FailingProvider:
    def call_chat_completions(self, *args, **kwargs):
        return None, "always fail", 0

fp2 = FailoverProvider(FailingProvider(), MockProvider(fixed_content="fb"), fail_threshold=2)
# 第一次：primary 失败，consecutive_fails=1
msg, err, tokens = fp2.call_chat_completions([], [], 100, 0.5)
check("S5.5 第一次失败", err == "always fail")
# 第二次：primary 又失败，累计=2 ≥ threshold，切 fallback
msg, err, tokens = fp2.call_chat_completions([], [], 100, 0.5)
check("S5.6 第二次切 fallback", msg.content == "fb")

# S5.5 _default_provider 从配置解析
with tempfile.TemporaryDirectory() as tmpdir:
    fake_settings = Path(tmpdir) / "settings.json"
    fake_settings.write_text(json.dumps({
        "kimi_api_key": "sk-test",
        "kimi_base_url": "https://api.moonshot.cn/v1",
        "kimi_model": "moonshot-v1-32k",
        "llm_provider": "mock",
    }))
    with patch("agent_tools.llm_provider.CONFIG_DIR", Path(tmpdir)):
        with patch("agent_tools.llm_provider._DEFAULT_PROVIDER", None):
            provider = _default_provider()
            check("S5.8 配置 mock → MockProvider", isinstance(provider, MockProvider))


# ── S6 · call_kimi_with_tools 薄壳 ─────────────────────────────────────────

section("S6 · call_kimi_with_tools 薄壳")

from agent_tools.kimi import call_kimi_with_tools

# S6.1 薄壳转发到 provider
mp_shell = MockProvider(fixed_content="shell test", fixed_tool_calls=None)
set_provider(mp_shell)
msg, err, tokens = call_kimi_with_tools([{"role": "user", "content": "hi"}], [], 100, 0.5)
check("S6.1 薄壳转发", msg.content == "shell test")

# 恢复
tool_registry._reset_for_tests()


# ── 总结 ───────────────────────────────────────────────────────────────────

ok = summary()
sys.exit(0 if ok else 1)
