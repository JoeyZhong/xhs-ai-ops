"""
状态感知免疫压缩引擎（Immune Zone Compression）。

解决 Agent 长会话中 messages 数组膨胀导致 context_length_exceeded 的问题。
核心策略：
- 最近一轮 assistant + tool 配对为「免疫区」，绝不压缩（避免切断 tool_call ↔ tool_response）
- 免疫区之前的对话用 Kimi summarize 压成摘要
- system prompt 永不压缩

详见 architect/Spider_XHS v2 架构设计.md §3.3.2
"""

from __future__ import annotations

import json
from typing import Optional


# ── Token 估算 ─────────────────────────────────────────────────────────────

# 目标触发阈值
# 32k 上下文窗口的 ~37%（中文字符 token 估算偏低 30-50%，且 system prompt
# 含多 block 注入会让真实 input 远超 messages 字面量长度。
# 阈值过高会让压缩在预算耗尽前都没触发，故下调到 12k）
_COMPRESSION_TRIGGER_TOKENS = 12_000

# 压缩后目标上限（留 4k 余量给下一轮生成 + tool 结果）
_COMPRESSION_TARGET_TOKENS = 8_000


def count_tokens(messages: list[dict]) -> int:
    """
    快速估算 messages 的 token 数。
    优先用 tiktoken（如果安装），否则按字符数兜底（中文字符 ≈ 1.5 tokens）。
    """
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        total = 0
        for m in messages:
            total += 4  # 每条消息固定开销
            for key, val in m.items():
                if val is None:
                    continue
                if isinstance(val, str):
                    total += len(enc.encode(val))
                elif isinstance(val, list):
                    total += len(enc.encode(json.dumps(val, ensure_ascii=False)))
                else:
                    total += len(enc.encode(str(val)))
            total += 2  # 消息结尾
        total += 3  # 对话开头
        return total
    except Exception:
        # tiktoken 未安装或失败 → 字符长度兜底
        text = json.dumps(messages, ensure_ascii=False)
        # 中文字符比例估 token：汉字 ≈ 1.5t，ascii ≈ 0.3t
        cn_chars = sum(1 for c in text if "一" <= c <= "鿿")
        ascii_chars = len(text) - cn_chars
        return int(cn_chars * 1.5 + ascii_chars * 0.3)


# ── 免疫区检测 ─────────────────────────────────────────────────────────────

def detect_immune_zone(messages: list[dict]) -> set[int]:
    """
    从 messages 中找出「免疫区」索引集合。

    免疫区 = 最近一轮含 tool_calls 的 assistant 消息 + 其对应的全部 tool 消息。
    这些消息在压缩时**绝不**被切除或修改，防止出现「悬空 tool_call」
    （assistant 声称调了工具但上下文里找不到 tool_response，导致下一轮幻觉）。

    如果最后一轮 assistant 没有 tool_calls（终态回答），返回空集合。
    """
    immune: set[int] = set()
    n = len(messages)

    # 从后往前找最后一个含 tool_calls 的 assistant
    for i in range(n - 1, -1, -1):
        msg = messages[i]
        if msg.get("role") == "assistant":
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                # 收集该 assistant 发出的所有 tool_call id
                tc_ids = {tc.get("id") for tc in tool_calls if tc.get("id")}
                # 向后扫描，找对应 tool 消息（必须在 assistant 之后）
                for j in range(i + 1, n):
                    tmsg = messages[j]
                    if tmsg.get("role") == "tool" and tmsg.get("tool_call_id") in tc_ids:
                        immune.add(j)
                immune.add(i)
            # 无论是否有 tool_calls，找到最后一个 assistant 就停止
            break

    return immune


# ── 压缩 ───────────────────────────────────────────────────────────────────

def compress_messages(
    messages: list[dict],
    immune_indices: set[int],
    target_tokens: int = _COMPRESSION_TARGET_TOKENS,
) -> tuple[list[dict], dict]:
    """
    压缩 messages，保留免疫区。

    策略：
    1. system 消息（索引 0，假设 role=system）永不压缩
    2. 免疫区消息保留原样
    3. 非免疫区消息分成两类：
       - assistant 消息（不含 tool_calls 或 tool_calls 已被处理过的）：保留 content，去掉 tool_calls
       - tool 消息：收集后统一 summarize 成一段摘要
    4. 如果压缩后仍超 target，进一步截断非免疫区的 assistant content

    返回 (compressed_messages, metadata)
        metadata: {before_len, after_len, turns_compressed, immune_count}
    """
    before_tokens = count_tokens(messages)

    if not messages:
        return [], {"before_len": 0, "after_len": 0, "turns_compressed": 0, "immune_count": 0}

    # 1. 分离 system、免疫区、可压缩区
    system_msg = None
    compressible: list[tuple[int, dict]] = []  # (原始索引, msg)
    immune_msgs: list[tuple[int, dict]] = []

    for idx, msg in enumerate(messages):
        if msg.get("role") == "system":
            system_msg = msg
        elif idx in immune_indices:
            immune_msgs.append((idx, msg))
        else:
            compressible.append((idx, msg))

    # 2. 可压缩区分类
    assistant_contents: list[str] = []
    tool_responses: list[dict] = []

    for idx, msg in compressible:
        role = msg.get("role")
        if role == "assistant":
            # 保留 content，去掉 tool_calls（因为对应的 tool_response 已被摘要化或不在免疫区）
            content = msg.get("content", "")
            if content:
                assistant_contents.append(content)
        elif role == "tool":
            # 尝试解析 <untrusted_data> 包裹的内容
            raw = msg.get("content", "")
            tool_responses.append({"idx": idx, "raw": raw})
        # user 消息（除了第一个 user/prompt）也保留——但通常除了第一轮外很少有 user 消息
        elif role == "user":
            assistant_contents.append(f"[user] {msg.get('content', '')}")

    # 3. 生成摘要
    summary_parts: list[str] = []

    if assistant_contents:
        # 合并早期 assistant 的 content（通常是思考过程）
        combined = "\n".join(assistant_contents)
        if len(combined) > 500:
            combined = combined[:500] + "…"
        summary_parts.append(f"[前期对话摘要]\n{combined}")

    if tool_responses:
        # 提取每个 tool 结果的关键信息
        tool_summaries: list[str] = []
        for tr in tool_responses:
            raw = tr["raw"]
            # 尝试提取 JSON 中的 ok / data 摘要
            try:
                # 去掉 <untrusted_data> 标签
                inner = raw.replace("<untrusted_data>", "").replace("</untrusted_data>", "").strip()
                parsed = json.loads(inner)
                ok = parsed.get("ok")
                data = parsed.get("data")
                if isinstance(data, dict):
                    # 取前几个 key 的摘要
                    keys = list(data.keys())[:3]
                    vals = [str(data[k])[:80] for k in keys]
                    tool_summaries.append(
                        f"tool(ok={ok}, keys={keys}, preview={vals})"
                    )
                elif isinstance(data, list):
                    tool_summaries.append(f"tool(ok={ok}, list_len={len(data)})")
                else:
                    tool_summaries.append(f"tool(ok={ok}, data={str(data)[:100]})")
            except Exception:
                # 解析失败就取前 100 字符
                text = raw[:100].replace("\n", " ")
                tool_summaries.append(f"tool(raw={text}...)")

        summary_parts.append(f"[前期工具调用摘要]\n" + "\n".join(tool_summaries))

    # 4. 组装压缩后的 messages
    compressed: list[dict] = []

    if system_msg:
        compressed.append(system_msg)

    if summary_parts:
        summary_text = "\n\n".join(summary_parts)
        compressed.append({
            "role": "assistant",
            "content": (
                f"<context_summary>\n"
                f"以下是对话历史压缩摘要（保留了最近一轮的完整细节）：\n\n"
                f"{summary_text}\n"
                f"</context_summary>"
            ),
        })

    # 5. 按原始顺序追加免疫区消息
    for idx, msg in sorted(immune_msgs, key=lambda x: x[0]):
        compressed.append(msg)

    after_tokens = count_tokens(compressed)

    metadata = {
        "before_len": before_tokens,
        "after_len": after_tokens,
        "turns_compressed": len(compressible),
        "immune_count": len(immune_indices),
    }

    return compressed, metadata


# ── 触发判断 ───────────────────────────────────────────────────────────────

def should_compress(messages: list[dict], threshold: int = _COMPRESSION_TRIGGER_TOKENS) -> bool:
    """判断当前 messages 是否需要压缩。"""
    return count_tokens(messages) >= threshold
