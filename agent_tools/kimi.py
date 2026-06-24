"""
Kimi / Moonshot LLM Tool。

封装 Kimi API 调用，加重试 + JSON 模式。
dashboard.py 中原有的 kimi_call 会切到这里。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from openai import OpenAI

from agent_tools import registry
from agent_tools.registry import ToolContext, ToolEnvironmentError, ToolExecutionError


# ── 配置读取（与 dashboard.get_settings 兼容） ─────────────────────────────

CONFIG_DIR = Path(__file__).parent.parent / "config"


def _load_settings() -> dict:
    path = CONFIG_DIR / "settings.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _get_client():
    settings = _load_settings()
    api_key = (settings.get("kimi_api_key", "")
                or os.environ.get("KIMI_API_KEY", ""))
    if not api_key:
        raise ToolEnvironmentError("Kimi API key not configured (config/settings.json)")
    return OpenAI(
        api_key=api_key,
        base_url=settings.get("kimi_base_url") or "",
        timeout=120,  # 原先无超时，慢/卡的调用会无限等；与 provider 对齐 120s
    ), settings


# ── 核心：可被 dashboard.kimi_call 直接调用的函数 ──────────────────────────

def call_kimi(prompt: str,
              system: str = "你是小红书内容策划专家。",
              max_tokens: int = 2000,
              max_retries: int = 3,
              json_mode: bool = False,
              temperature: float = 0.8) -> tuple[Optional[str], Optional[str]]:
    """
    返回 (content, error_message)。
    成功：(content, None)；失败：(None, error_str)
    """
    try:
        client, settings = _get_client()
    except ToolEnvironmentError as e:
        return None, str(e)

    delay = 8
    for attempt in range(max_retries):
        try:
            kwargs = dict(
                model=settings.get("kimi_model") or "",
                messages=[{"role": "system", "content": system},
                            {"role": "user",   "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            resp = client.chat.completions.create(**kwargs)
            content = resp.choices[0].message.content
            if json_mode and not (content or "").strip():
                return None, "JSON mode requested but model returned empty content"
            return content, None
        except Exception as e:
            err = str(e)
            is_retryable = ("429" in err or "overload" in err.lower()
                              or "rate" in err.lower() or "timeout" in err.lower())
            if is_retryable and attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 3
                continue
            return None, err
    return None, f"已重试 {max_retries} 次，Kimi 服务仍繁忙"


# ── Tool calling 模式（给 AgentBase 主循环用） ───────────────────────────

def call_kimi_with_tools(messages: list[dict],
                           tools: list[dict],
                           max_tokens: int = 2000,
                           max_retries: int = 3,
                           temperature: float = 0.7,
                           tool_choice: str = "auto") -> tuple[Optional[object], Optional[str], int]:
    """
    带 tool calling 的 Kimi 调用。

    返回 (message, error, tokens_used)：
        - message 是 OpenAI ChatCompletionMessage（含 .content 和 .tool_calls）
        - error 失败时返回错误字符串
        - tokens_used 实际本次消耗的 total_tokens（失败时为 0）
    """
    """
    带 tool calling 的 Kimi 调用。
    P1.3 后改为薄壳：内部转发到 _default_provider().call_chat_completions()。
    保留本函数作为向后兼容入口。
    """
    from agent_tools.llm_provider import _default_provider
    return _default_provider().call_chat_completions(
        messages=messages,
        tools=tools,
        max_tokens=max_tokens,
        temperature=temperature,
        tool_choice=tool_choice,
    )


# ── 流式 tool calling（给 orchestrator 真·token 流式用） ──────────────────

class _StreamedFunc:
    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments


class _StreamedTC:
    """重建出与 OpenAI ChatCompletionMessageToolCall 同形的对象（.id/.function.name/.arguments）。"""
    def __init__(self, id: str, name: str, arguments: str):
        self.id = id
        self.type = "function"
        self.function = _StreamedFunc(name, arguments)


class _StreamedMsg:
    """与非流式返回同形（.content / .tool_calls），供 orchestrator 主循环无差别消费。"""
    def __init__(self, content: str, tool_calls: Optional[list]):
        self.content = content
        self.tool_calls = tool_calls or None


def call_kimi_with_tools_stream(*, messages: list[dict], tools: list[dict],
                                max_tokens: int = 2000, temperature: float = 0.7,
                                tool_choice: str = "auto",
                                on_delta: Optional[callable] = None,
                                max_retries: int = 3) -> tuple[Optional[object], Optional[str], int]:
    """带 tool calling 的**流式** Kimi 调用。

    返回 (message, error, tokens_used)——与 call_kimi_with_tools 同形，故 orchestrator
    主循环拿到的 message 一样有 .content / .tool_calls，逐工具分支无需改。

    区别：流式接收，**内容 token 边到边经 on_delta(text) 回调吐出**（用于 final_delta 真流式）。
    一旦本轮出现 tool_call（=要调子 agent/追问，不是最终答复），content 不再经 on_delta 吐
    （避免把"思考前言"误当最终答案流出去；前端另有兜底丢弃）。
    """
    settings = _load_settings()
    api_key = settings.get("kimi_api_key", "") or os.environ.get("KIMI_API_KEY", "")
    if not api_key:
        return None, "Kimi API key not configured (config/settings.json)", 0
    client = OpenAI(api_key=api_key, base_url=settings.get("kimi_base_url") or "", timeout=120)
    model = settings.get("kimi_model") or ""

    delay = 8
    for attempt in range(max_retries):
        try:
            kwargs = dict(model=model, messages=messages, temperature=temperature,
                          max_tokens=max_tokens, stream=True,
                          stream_options={"include_usage": True})
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = tool_choice
            stream = client.chat.completions.create(**kwargs)

            content_parts: list[str] = []
            tool_acc: dict[int, dict] = {}   # index -> {id,name,args}
            saw_tool = False
            tokens_used = 0
            for chunk in stream:
                usage = getattr(chunk, "usage", None)
                if usage:
                    tokens_used = getattr(usage, "total_tokens", 0) or tokens_used
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = choices[0].delta
                tcs = getattr(delta, "tool_calls", None)
                if tcs:
                    saw_tool = True
                    for tc in tcs:
                        slot = tool_acc.setdefault(tc.index, {"id": None, "name": "", "args": ""})
                        if getattr(tc, "id", None):
                            slot["id"] = tc.id
                        fn = getattr(tc, "function", None)
                        if fn is not None:
                            if getattr(fn, "name", None):
                                slot["name"] = fn.name
                            if getattr(fn, "arguments", None):
                                slot["args"] += fn.arguments
                piece = getattr(delta, "content", None)
                if piece:
                    content_parts.append(piece)
                    if on_delta and not saw_tool:
                        try:
                            on_delta(piece)
                        except Exception:
                            pass

            tool_calls = None
            if tool_acc:
                tool_calls = [
                    _StreamedTC(slot["id"] or f"call_{idx}", slot["name"], slot["args"])
                    for idx, slot in sorted(tool_acc.items())
                ]
            return _StreamedMsg("".join(content_parts), tool_calls), None, tokens_used
        except Exception as e:
            err = str(e)
            is_retryable = ("429" in err or "overload" in err.lower()
                            or "rate" in err.lower() or "timeout" in err.lower())
            if is_retryable and attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 3
                continue
            return None, err, 0
    return None, f"已重试 {max_retries} 次，Kimi 服务仍繁忙", 0


# ── Tool handler ─────────────────────────────────────────────────────────

def _kimi_complete_handler(args: dict, ctx: ToolContext) -> dict:
    content, err = call_kimi(
        prompt=args["prompt"],
        system=args.get("system", "你是小红书内容策划专家。"),
        max_tokens=args.get("max_tokens", 2000),
        max_retries=args.get("max_retries", 3),
        json_mode=args.get("json_mode", False),
        temperature=args.get("temperature", 0.8),
    )
    if err:
        return {"ok": False, "error": err}
    return {"ok": True, "data": {"content": content}}


def _kimi_summarize_handler(args: dict, ctx: ToolContext) -> dict:
    """专为 Analyst 准备的摘要变体：温度低、强制 JSON。"""
    summary_prompt = (
        f"请用结构化方式总结以下内容，输出 JSON：{{summary, key_points, recommendations}}\n\n"
        f"原文：\n{args['content']}"
    )
    content, err = call_kimi(
        prompt=summary_prompt,
        system=args.get("system", "你是数据分析师，擅长从数据中找出关键模式和可执行建议。"),
        max_tokens=args.get("max_tokens", 1500),
        json_mode=True,
        temperature=0.3,
    )
    if err:
        return {"ok": False, "error": err}
    return {"ok": True, "data": {"content": content}}


# ── 注册 ─────────────────────────────────────────────────────────────────

registry.register(
    name="kimi.complete",
    schema={
        "description": "Generic Kimi/Moonshot LLM completion with retry and optional JSON mode.",
        "parameters": {
            "type": "object",
            "required": ["prompt"],
            "properties": {
                "prompt":      {"type": "string", "description": "User prompt"},
                "system":      {"type": "string", "description": "System message", "default": "你是小红书内容策划专家。"},
                "max_tokens":  {"type": "integer", "minimum": 1, "maximum": 8000},
                "max_retries": {"type": "integer", "minimum": 1, "maximum": 5},
                "json_mode":   {"type": "boolean"},
                "temperature": {"type": "number", "minimum": 0, "maximum": 2},
            },
        },
    },
    handler=_kimi_complete_handler,
    cost_estimate=2000.0,
    description="LLM completion with built-in retry/backoff",
)

registry.register(
    name="kimi.summarize",
    schema={
        "description": "Structured summary using low temperature + JSON output (for Analyst).",
        "parameters": {
            "type": "object",
            "required": ["content"],
            "properties": {
                "content":    {"type": "string", "description": "Text/data to summarize"},
                "system":     {"type": "string"},
                "max_tokens": {"type": "integer", "minimum": 1, "maximum": 8000},
            },
        },
    },
    handler=_kimi_summarize_handler,
    cost_estimate=1500.0,
    description="Structured summary for analytical use",
)
