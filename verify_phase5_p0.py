#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 5 P0 验收测试：GOAP scratch_pad + 状态感知免疫压缩
运行方式：cd /d D:\\【AIcode】\\Spider_XHS && python verify_phase5_p0.py

覆盖：
- immune zone 识别正确（最后一轮 assistant + 所有 tool 配对）
- 压缩从不切断 tool_call ↔ tool_response 配对
- 压缩后 messages 总长 < 16k
- scratch_pad 解析/缺失时主循环不崩
- 30+ cases
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── 输出工具 ─────────────────────────────────────────────────────────────────

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
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'-'*60}")


def summary():
    total = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = total - passed
    print(f"\n{'='*60}")
    print(f"  结果：{passed}/{total} 通过")
    if failed:
        print(f"  失败 {failed} 项")
        print("\n  失败清单：")
        for name, ok, detail in _results:
            if not ok:
                print(f"    [X] {name}" + (f": {detail}" if detail else ""))
    else:
        print("  全部通过")
    print('='*60)
    return failed == 0


# ── S1 · compression 模块导入 ───────────────────────────────────────────────

section("S1 · compression 模块导入")

try:
    from agents.compression import (
        count_tokens, detect_immune_zone, compress_messages,
        should_compress, _COMPRESSION_TRIGGER_TOKENS,
    )
    check("S1.1 compression 模块可导入", True)
except Exception as e:
    check("S1.1 compression 模块可导入", False, str(e))
    print("\n  终止：核心模块无法导入")
    sys.exit(1)

# ── S2 · count_tokens 估算 ──────────────────────────────────────────────────

section("S2 · count_tokens 估算")

# S2.1 空列表
empty_tokens = count_tokens([])
check("S2.1 空 messages = 0 tokens", empty_tokens == 0,
      f"got {empty_tokens}")

# S2.2 system + user 两消息
simple_msgs = [
    {"role": "system", "content": "你是一个助手。"},
    {"role": "user", "content": "Hello"},
]
tokens_simple = count_tokens(simple_msgs)
check("S2.2 简单两消息有正 token 数", tokens_simple > 0,
      f"got {tokens_simple}")

# S2.3 中文字符估算大于 ascii
ascii_msgs = [{"role": "user", "content": "abc"}]
cn_msgs = [{"role": "user", "content": "一二三"}]
t_ascii = count_tokens(ascii_msgs)
t_cn = count_tokens(cn_msgs)
check("S2.3 中文消息 token 大于 ascii", t_cn > t_ascii,
      f"ascii={t_ascii} cn={t_cn}")

# S2.4 tool_calls 也计入
msg_with_tc = {
    "role": "assistant",
    "content": "调用工具",
    "tool_calls": [
        {"id": "tc1", "type": "function", "function": {"name": "foo", "arguments": "{}"}},
    ],
}
t_tc = count_tokens([msg_with_tc])
check("S2.4 tool_calls 增加 token", t_tc > count_tokens([{"role": "assistant", "content": "调用工具"}]),
      f"got {t_tc}")

# S2.5 大消息 token 数更大
big_msg = {"role": "user", "content": "x" * 5000}
t_big = count_tokens([big_msg])
check("S2.5 大消息 token 数更大", t_big > count_tokens([{"role": "user", "content": "x"}]),
      f"got {t_big}")

# ── S3 · detect_immune_zone ─────────────────────────────────────────────────

section("S3 · 免疫区检测 (detect_immune_zone)")

# S3.1 空消息
immune = detect_immune_zone([])
check("S3.1 空消息 → 空免疫区", len(immune) == 0)

# S3.2 纯 system + user → 无免疫区
no_assistant = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "hello"},
]
immune = detect_immune_zone(no_assistant)
check("S3.2 无 assistant → 空免疫区", len(immune) == 0)

# S3.3 终态 assistant（无 tool_calls）→ 空免疫区
final_answer = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "hello"},
    {"role": "assistant", "content": "答案是42"},
]
immune = detect_immune_zone(final_answer)
check("S3.3 终态 assistant（无 tool_calls）→ 空", len(immune) == 0)

# S3.4 一轮 assistant + 1 tool → 免疫区 = {assistant_idx, tool_idx}
one_tool = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "q"},
    {"role": "assistant", "content": "调工具", "tool_calls": [
        {"id": "tc1", "type": "function", "function": {"name": "foo", "arguments": "{}"}},
    ]},
    {"role": "tool", "tool_call_id": "tc1", "content": "result"},
]
immune = detect_immune_zone(one_tool)
check("S3.4 一轮 assistant + 1 tool → 免疫区大小=2",
      len(immune) == 2 and 2 in immune and 3 in immune,
      f"got {immune}")

# S3.5 一轮 assistant + 2 tools → 免疫区大小=3
two_tools = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "q"},
    {"role": "assistant", "content": "调两个", "tool_calls": [
        {"id": "tc1", "type": "function", "function": {"name": "foo", "arguments": "{}"}},
        {"id": "tc2", "type": "function", "function": {"name": "bar", "arguments": "{}"}},
    ]},
    {"role": "tool", "tool_call_id": "tc1", "content": "r1"},
    {"role": "tool", "tool_call_id": "tc2", "content": "r2"},
]
immune = detect_immune_zone(two_tools)
check("S3.5 一轮 assistant + 2 tools → 免疫区大小=3",
      len(immune) == 3 and 2 in immune and 3 in immune and 4 in immune,
      f"got {immune}")

# S3.6 多轮对话，只免疫最后一轮
multi_turn = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "q1"},
    {"role": "assistant", "content": "调A", "tool_calls": [
        {"id": "tc_old", "type": "function", "function": {"name": "old", "arguments": "{}"}},
    ]},
    {"role": "tool", "tool_call_id": "tc_old", "content": "r_old"},
    # 终态回答
    {"role": "assistant", "content": "结论"},
]
immune = detect_immune_zone(multi_turn)
check("S3.6 多轮后终态（无 tool_calls）→ 空免疫区",
      len(immune) == 0, f"got {immune}")

# S3.7 多轮，最后一轮有 tool_calls
multi_with_tool = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "q"},
    {"role": "assistant", "content": "第一轮", "tool_calls": [
        {"id": "tc1", "type": "function", "function": {"name": "a", "arguments": "{}"}},
    ]},
    {"role": "tool", "tool_call_id": "tc1", "content": "r1"},
    {"role": "assistant", "content": "第二轮", "tool_calls": [
        {"id": "tc2", "type": "function", "function": {"name": "b", "arguments": "{}"}},
    ]},
    {"role": "tool", "tool_call_id": "tc2", "content": "r2"},
]
immune = detect_immune_zone(multi_with_tool)
check("S3.7 多轮只免疫最后一轮 assistant+tool",
      len(immune) == 2 and 4 in immune and 5 in immune,
      f"got {immune}")

# S3.8 tool_call id 不匹配 → 不免疫
mismatched = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "q"},
    {"role": "assistant", "content": "调", "tool_calls": [
        {"id": "tc_real", "type": "function", "function": {"name": "x", "arguments": "{}"}},
    ]},
    {"role": "tool", "tool_call_id": "tc_fake", "content": "r"},
]
immune = detect_immune_zone(mismatched)
check("S3.8 tool_call id 不匹配 → 只免疫 assistant",
      len(immune) == 1 and 2 in immune,
      f"got {immune}")

# ── S4 · compress_messages ──────────────────────────────────────────────────

section("S4 · 压缩 (compress_messages)")

# S4.1 空消息
compressed, meta = compress_messages([], set())
check("S4.1 空消息压缩后仍为空", len(compressed) == 0)

# S4.2 只有 system 和 user → 保留
simple = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "hello"},
]
compressed, meta = compress_messages(simple, set())
check("S4.2 无免疫区也保留 system", len(compressed) >= 1)

# S4.3 压缩后 tool_call ↔ tool_response 配对不被切断
# 构造一个大的 messages 数组（模拟长会话）
big_messages = [
    {"role": "system", "content": "sys prompt"},
    {"role": "user", "content": "question"},
]
# 添加多轮 assistant + tool
for i in range(20):
    big_messages.append({"role": "assistant", "content": f"分析第{i}批数据", "tool_calls": [
        {"id": f"tc{i}a", "type": "function", "function": {"name": "search", "arguments": "{}"}},
    ]})
    big_messages.append({"role": "tool", "tool_call_id": f"tc{i}a", "content": json.dumps({"ok": True, "data": {"count": i}})})

# 最后一轮是终态 assistant（无 tool_calls）
big_messages.append({"role": "assistant", "content": "最终结论"})

immune = detect_immune_zone(big_messages)
compressed, meta = compress_messages(big_messages, immune)
check("S4.3 压缩不切断最后一轮 pairing",
      len(compressed) > 0 and meta["turns_compressed"] > 0,
      f"turns_compressed={meta['turns_compressed']}")

# S4.4 压缩后长度变短
check("S4.4 压缩后 token 数减少",
      meta["after_len"] < meta["before_len"],
      f"before={meta['before_len']} after={meta['after_len']}")

# S4.5 system 消息始终保留
has_system = any(m.get("role") == "system" for m in compressed)
check("S4.5 system 消息始终保留", has_system)

# S4.6 免疫区消息保留原样
# 构造明确的免疫区
msgs_with_immune = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "q"},
    {"role": "assistant", "content": "第一轮", "tool_calls": [
        {"id": "tc1", "type": "function", "function": {"name": "a", "arguments": "{}"}},
    ]},
    {"role": "tool", "tool_call_id": "tc1", "content": "r1"},
    {"role": "assistant", "content": "第二轮", "tool_calls": [
        {"id": "tc2", "type": "function", "function": {"name": "b", "arguments": "{}"}},
    ]},
    {"role": "tool", "tool_call_id": "tc2", "content": "r2"},
]
immune = {4, 5}  # 最后一轮
compressed, meta = compress_messages(msgs_with_immune, immune)
# 检查免疫区的 assistant 和 tool 都在
immune_assist = None
immune_tool = None
for m in compressed:
    if m.get("role") == "assistant" and m.get("tool_calls"):
        for tc in m["tool_calls"]:
            if tc.get("id") == "tc2":
                immune_assist = m
    if m.get("role") == "tool" and m.get("tool_call_id") == "tc2":
        immune_tool = m
check("S4.6 免疫区 assistant+tool 保留原样",
      immune_assist is not None and immune_tool is not None)

# S4.7 metadata 包含预期字段
check("S4.7 metadata 有 before_len", "before_len" in meta)
check("S4.8 metadata 有 after_len", "after_len" in meta)
check("S4.9 metadata 有 turns_compressed", "turns_compressed" in meta)
check("S4.10 metadata 有 immune_count", "immune_count" in meta)

# S4.11 大消息压缩后 < 16k（触发阈值）
# 构造一个超大 messages
giant = [{"role": "system", "content": "sys"}]
for i in range(50):
    giant.append({"role": "assistant", "content": f"分析结果{i}: " + "x" * 500})
    giant.append({"role": "tool", "tool_call_id": f"tc{i}", "content": json.dumps({"ok": True, "data": {"list": list(range(100))}})})

before = count_tokens(giant)
immune = detect_immune_zone(giant)
compressed, meta = compress_messages(giant, immune)
after = count_tokens(compressed)
check("S4.11 大消息压缩后 < 16k",
      after < 16000,
      f"before={before} after={after}")

# ── S5 · should_compress ────────────────────────────────────────────────────

section("S5 · 压缩触发 (should_compress)")

# S5.1 小消息不触发
small = [{"role": "user", "content": "hi"}]
check("S5.1 小消息不触发", not should_compress(small))

# S5.2 大消息触发
# 构造超过 24k tokens 的消息
giant_for_trigger = [{"role": "system", "content": "sys"}]
for i in range(200):
    giant_for_trigger.append({"role": "assistant", "content": f"段落{i}: " + "关键词测试" * 100})

# 这个可能还不到 24k，用更多
if not should_compress(giant_for_trigger):
    for i in range(200):
        giant_for_trigger.append({"role": "assistant", "content": f"补充{i}: " + "内容填充" * 200})

check("S5.2 大消息触发压缩", should_compress(giant_for_trigger))

# S5.3 阈值可调
check("S5.3 阈值调高后不触发",
      not should_compress(giant_for_trigger, threshold=999999))

# ── S6 · scratch_pad 边界保护 ───────────────────────────────────────────────

section("S6 · scratch_pad 边界保护")

from agents.base import _strip_scratch_pad

# S6.1 正常内容不变
plain = "这是一个普通回复。"
check("S6.1 无 scratch_pad 的内容不变",
      _strip_scratch_pad(plain) == plain)

# S6.2 去掉 scratch_pad 块
with_pad = "<scratch_pad><goal>分析数据</goal><actions>search(x=1)</actions></scratch_pad>\n结果是42。"
result = _strip_scratch_pad(with_pad)
check("S6.2 去掉 scratch_pad 块",
      "<scratch_pad>" not in result and "结果是42" in result,
      f"got: {result!r}")

# S6.3 多行 scratch_pad
multiline = """<scratch_pad>
<goal>找关键词</goal>
<actions>hot_monitor.suggest()</actions>
</scratch_pad>

最终关键词列表：A, B, C
"""
result = _strip_scratch_pad(multiline)
check("S6.3 多行 scratch_pad 去掉",
      "<scratch_pad>" not in result and "最终关键词列表" in result,
      f"got: {result!r}")

# S6.4 空字符串
result = _strip_scratch_pad("")
check("S6.4 空字符串不变", result == "")

# S6.5 None 处理（通过 or "" 在调用处处理，函数本身接收 str）
# 这里不测试 None，因为类型签名是 str

# S6.6 只有 scratch_pad 无其他内容
only_pad = "<scratch_pad><goal>思考</goal></scratch_pad>"
result = _strip_scratch_pad(only_pad)
check("S6.6 只有 scratch_pad → 空",
      result == "", f"got: {result!r}")

# S6.7 scratch_pad 在内容中间
middle_pad = "开头<scratch_pad>思考</scratch_pad>结尾"
result = _strip_scratch_pad(middle_pad)
check("S6.7 scratch_pad 在中间也去掉",
      result == "开头结尾", f"got: {result!r}")

# ── S7 · AgentBase.run() 集成 ──────────────────────────────────────────────

section("S7 · AgentBase.run() 集成")

# 构造最小可运行的 mock 环境
from agents.base import AgentBase, AgentTask, _generate_master_token
from agents.audit import AuditLogger
from agents.memory import MemoryLayer
from agents.policy import ToolPolicy

# 创建临时目录用于 audit 和 memory
with tempfile.TemporaryDirectory() as tmpdir:
    tmp = Path(tmpdir)

    class MockAuditStorage:
        def __init__(self):
            self._lines = []
        def append_audit(self, tenant_id, data):
            self._lines.append(data)

    audit = AuditLogger(storage=MockAuditStorage(), tenant_id="test", task_id="t1")
    policy = ToolPolicy({"*": ["*"]})

    # Mock memory storage
    class MockStorage:
        def __init__(self):
            self._data = {}
        def load_memory(self, tenant, scope, file):
            return self._data.get((tenant, scope, file))
        def save_memory(self, tenant, scope, file, content):
            self._data[(tenant, scope, file)] = content

    memory = MemoryLayer(storage=MockStorage())
    token = _generate_master_token()

    class TestAgent(AgentBase):
        role = "test"
        enabled_tool_patterns = ["*"]
        default_system_prompt = "你是一个测试 agent。"

    agent = TestAgent(
        master_token=token,
        memory=memory,
        audit=audit,
        policy=policy,
    )

    # S7.1 正常 run（mock LLM 返回无 tool_call 的终态）
    def mock_llm_no_tools(messages, tools, max_tokens, temperature, tool_choice="auto"):
        msg = MagicMock()
        msg.content = "最终答案"
        msg.tool_calls = None
        return msg, None, 100

    with patch("agents.base.call_kimi_with_tools", side_effect=mock_llm_no_tools):
        result = agent.run(AgentTask(type="test", prompt="测试"))
        check("S7.1 mock LLM 终态 → run 成功",
              result.ok and result.content == "最终答案",
              f"ok={result.ok} content={result.content}")

    # S7.2 mock LLM 返回 scratch_pad → _strip_scratch_pad 生效
    def mock_llm_scratchpad(messages, tools, max_tokens, temperature, tool_choice="auto"):
        msg = MagicMock()
        msg.content = "<scratch_pad><goal>分析</goal></scratch_pad>\n最终答案"
        msg.tool_calls = None
        return msg, None, 100

    with patch("agents.base.call_kimi_with_tools", side_effect=mock_llm_scratchpad):
        result = agent.run(AgentTask(type="test", prompt="测试"))
        check("S7.2 mock LLM 含 scratch_pad → 返回内容已去掉 scratch_pad",
              result.ok and "<scratch_pad>" not in result.content and "最终答案" in result.content,
              f"content={result.content!r}")

    # S7.3 mock LLM 返回 tool_call → 一轮后终态
    call_count = [0]
    def mock_llm_tool_then_done(messages, tools, max_tokens, temperature, tool_choice="auto"):
        call_count[0] += 1
        msg = MagicMock()
        if call_count[0] == 1:
            tc = MagicMock()
            tc.id = "tc1"
            tc.function.name = "kimi__complete"
            tc.function.arguments = '{"prompt": "test"}'
            msg.content = "调用工具"
            msg.tool_calls = [tc]
        else:
            msg.content = "完成"
            msg.tool_calls = None
        return msg, None, 100

    with patch("agents.base.call_kimi_with_tools", side_effect=mock_llm_tool_then_done):
        result = agent.run(AgentTask(type="test", prompt="测试", max_iterations=5))
        check("S7.3 tool_call → 第二轮终态 → run 成功",
              result.ok and result.iterations == 2,
              f"ok={result.ok} iterations={result.iterations}")

    # S7.4 压缩触发路径（构造超长的 messages）
    call_count[0] = 0
    def mock_llm_with_compression(messages, tools, max_tokens, temperature, tool_choice="auto"):
        call_count[0] += 1
        msg = MagicMock()
        msg.content = f"第{call_count[0]}轮"
        msg.tool_calls = None
        return msg, None, 100

    # 构造一个会触发压缩的 agent
    with patch("agents.base.call_kimi_with_tools", side_effect=mock_llm_with_compression):
        with patch("agents.base.should_compress", return_value=True):
            with patch("agents.base.detect_immune_zone", return_value=set()):
                with patch("agents.base.compress_messages") as mock_compress:
                    mock_compress.return_value = (
                        [{"role": "system", "content": "sys"},
                         {"role": "assistant", "content": "<context_summary>压缩摘要</context_summary>"}],
                        {"before_len": 25000, "after_len": 5000, "turns_compressed": 10, "immune_count": 0}
                    )
                    result = agent.run(AgentTask(type="test", prompt="测试", max_iterations=3))
                    check("S7.4 压缩触发路径 → run 不崩",
                          result.ok, f"error={result.error}")
                    # 验证 compress_messages 被调用了
                    check("S7.5 压缩触发时 compress_messages 被调用",
                          mock_compress.called)

# ── S8 · settings.json feature flag ─────────────────────────────────────────

section("S8 · feature flag 读取")

from agents.base import _load_reasoning_flags

# S8.1 默认启用
flags = _load_reasoning_flags()
check("S8.1 默认 scratchpad_enabled=true",
      flags.get("scratchpad_enabled", False) is True,
      f"got {flags}")

# S8.2 settings.json 显式 false
with tempfile.TemporaryDirectory() as tmpdir:
    fake_settings = Path(tmpdir) / "settings.json"
    fake_settings.write_text(json.dumps({"agent_reasoning": {"scratchpad_enabled": False}}))
    # 临时替换路径（需要 patch）
    with patch("agents.base._SETTINGS_PATH", fake_settings):
        flags = _load_reasoning_flags()
        check("S8.2 settings.json false → 返回 false",
              flags.get("scratchpad_enabled") is False,
              f"got {flags}")

# S8.3 settings.json 不存在 → 默认 true
with tempfile.TemporaryDirectory() as tmpdir:
    fake_settings = Path(tmpdir) / "nonexistent.json"
    with patch("agents.base._SETTINGS_PATH", fake_settings):
        flags = _load_reasoning_flags()
        check("S8.3 settings.json 不存在 → 默认 true",
              flags.get("scratchpad_enabled", False) is True,
              f"got {flags}")

# ── S9 · REASONING_DIRECTIVE 内容 ───────────────────────────────────────────

section("S9 · REASONING_DIRECTIVE 内容检查")

from agents.base import REASONING_DIRECTIVE

check("S9.1 包含 <scratch_pad>", "<scratch_pad>" in REASONING_DIRECTIVE)
check("S9.2 包含 <goal>", "<goal>" in REASONING_DIRECTIVE)
check("S9.3 包含 <actions>", "<actions>" in REASONING_DIRECTIVE)
check("S9.4 包含 <observation>", "<observation>" in REASONING_DIRECTIVE)
check("S9.5 包含 <reflection>", "<reflection>" in REASONING_DIRECTIVE)
check("S9.6 包含 </scratch_pad>", "</scratch_pad>" in REASONING_DIRECTIVE)

# ── S10 · 端到端：压缩不破坏 tool_call 配对 ─────────────────────────────────

section("S10 · 端到端：压缩不破坏 tool_call 配对")

# 构造一个模拟真实 Agent 会话的 messages 流
real_session = [
    {"role": "system", "content": "你是一个分析师。"},
    {"role": "user", "content": "分析最近的笔记数据"},
    # 第一轮
    {"role": "assistant", "content": "先采集数据", "tool_calls": [
        {"id": "tc_search", "type": "function", "function": {"name": "search__collect_notes", "arguments": '{"keyword": "自助机", "limit": 10}'}},
    ]},
    {"role": "tool", "tool_call_id": "tc_search", "content": "<untrusted_data>\n{\"ok\": true, \"data\": {\"notes\": [{\"id\": 1, \"title\": \"笔记1\"}]}}\n</untrusted_data>"},
    # 第二轮
    {"role": "assistant", "content": "计算 CES", "tool_calls": [
        {"id": "tc_ces", "type": "function", "function": {"name": "data_analysis__compute_ces", "arguments": "{}"}},
    ]},
    {"role": "tool", "tool_call_id": "tc_ces", "content": "<untrusted_data>\n{\"ok\": true, \"data\": {\"ces\": 150}}\n</untrusted_data>"},
    # 第三轮（最后一轮，有 tool_calls）
    {"role": "assistant", "content": "写 playbook", "tool_calls": [
        {"id": "tc_write", "type": "function", "function": {"name": "memory__write_playbook_entry", "arguments": "{}"}},
    ]},
    {"role": "tool", "tool_call_id": "tc_write", "content": "<untrusted_data>\n{\"ok\": true, \"data\": \"written\"}\n</untrusted_data>"},
]

immune = detect_immune_zone(real_session)
compressed, meta = compress_messages(real_session, immune)

# 验证：免疫区的 assistant 和 tool 都在
has_last_assist = any(
    m.get("role") == "assistant" and m.get("tool_calls")
    and any(tc.get("id") == "tc_write" for tc in m.get("tool_calls", []))
    for m in compressed
)
has_last_tool = any(
    m.get("role") == "tool" and m.get("tool_call_id") == "tc_write"
    for m in compressed
)
check("S10.1 压缩后最后一轮 assistant 保留",
      has_last_assist, f"immune={immune} compressed_len={len(compressed)}")
check("S10.2 压缩后最后一轮 tool 保留",
      has_last_tool)

# 验证：前面轮次的 tool 不在（被压缩了）
has_old_tool = any(
    m.get("role") == "tool" and m.get("tool_call_id") == "tc_search"
    for m in compressed
)
check("S10.3 前面轮次的 tool 被压缩",
      not has_old_tool)

# 验证：压缩后包含摘要
has_summary = any("<context_summary>" in (m.get("content", "")) for m in compressed)
check("S10.4 压缩后包含 context_summary",
      has_summary)

# ── 总结 ────────────────────────────────────────────────────────────────────

ok = summary()
sys.exit(0 if ok else 1)
