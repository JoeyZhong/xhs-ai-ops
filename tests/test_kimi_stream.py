"""
call_kimi_with_tools_stream 的流式分块解析单测（不打真实 API）。

monkeypatch agent_tools.kimi.OpenAI → 假客户端，yield 出 OpenAI 流式同形的 chunk，
验证：内容 token 经 on_delta 边到边吐、tool_call 跨 chunk 拼装、usage 取 token 数、
以及"出现 tool_call 后内容不再经 on_delta"（避免思考前言被误当最终答案流出）。
"""

from __future__ import annotations

from typing import Any

import pytest


class _Box:
    """通用属性容器，仿 OpenAI streaming chunk 的鸭子类型。"""
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


def _content_chunk(text: str) -> _Box:
    return _Box(choices=[_Box(delta=_Box(content=text, tool_calls=None))], usage=None)


def _tool_chunk(index: int, name: str | None, args: str | None, call_id: str | None = None) -> _Box:
    fn = _Box(name=name, arguments=args)
    tc = _Box(index=index, id=call_id, function=fn)
    return _Box(choices=[_Box(delta=_Box(content=None, tool_calls=[tc]))], usage=None)


def _usage_chunk(total: int) -> _Box:
    return _Box(choices=[], usage=_Box(total_tokens=total))


def _patch_openai(monkeypatch: pytest.MonkeyPatch, chunks: list[_Box]) -> None:
    import agent_tools.kimi as kimi_mod

    class _FakeCompletions:
        def create(self, **kwargs: Any):
            return iter(chunks)

    class _FakeChat:
        def __init__(self) -> None:
            self.completions = _FakeCompletions()

    class _FakeOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            self.chat = _FakeChat()

    monkeypatch.setattr(kimi_mod, "OpenAI", _FakeOpenAI)
    monkeypatch.setattr(kimi_mod, "_load_settings",
                        lambda: {"kimi_api_key": "test-key", "kimi_model": "test-model"})


def test_content_stream_calls_on_delta_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_tools.kimi import call_kimi_with_tools_stream

    _patch_openai(monkeypatch, [
        _content_chunk("点位"), _content_chunk("招商"), _content_chunk("思路"),
        _usage_chunk(123),
    ])
    seen: list[str] = []
    msg, err, tokens = call_kimi_with_tools_stream(
        messages=[{"role": "user", "content": "x"}], tools=[],
        on_delta=seen.append,
    )
    assert err is None
    assert seen == ["点位", "招商", "思路"]           # 逐 token、按序
    assert msg.content == "点位招商思路"                # 拼装完整
    assert msg.tool_calls is None
    assert tokens == 123


def test_tool_call_assembled_across_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_tools.kimi import call_kimi_with_tools_stream

    # 工具名 + 参数分两块到达，需拼装
    _patch_openai(monkeypatch, [
        _tool_chunk(0, "run_subagent", '{"archetype":"int', call_id="call_1"),
        _tool_chunk(0, None, 'el","task":"采集"}'),
        _usage_chunk(50),
    ])
    seen: list[str] = []
    msg, err, tokens = call_kimi_with_tools_stream(
        messages=[{"role": "user", "content": "x"}],
        tools=[{"type": "function", "function": {"name": "run_subagent"}}],
        on_delta=seen.append,
    )
    assert err is None
    assert seen == []                                  # 工具轮不吐 final_delta
    assert msg.tool_calls and len(msg.tool_calls) == 1
    tc = msg.tool_calls[0]
    assert tc.id == "call_1"
    assert tc.function.name == "run_subagent"
    assert tc.function.arguments == '{"archetype":"intel","task":"采集"}'


def test_content_before_toolcall_streams_then_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    """内容前言先到 → 吐；随后出现 tool_call → 其后的内容不再吐（saw_tool 闸）。"""
    from agent_tools.kimi import call_kimi_with_tools_stream

    _patch_openai(monkeypatch, [
        _content_chunk("先想一下"),
        _tool_chunk(0, "run_subagent", "{}", call_id="c1"),
        _content_chunk("这段不该流出"),
    ])
    seen: list[str] = []
    msg, err, _ = call_kimi_with_tools_stream(
        messages=[{"role": "user", "content": "x"}], tools=[], on_delta=seen.append,
    )
    assert err is None
    assert seen == ["先想一下"]                         # 仅 tool_call 之前的内容流出
    assert msg.tool_calls and len(msg.tool_calls) == 1


def test_missing_api_key_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_tools.kimi as kimi_mod
    from agent_tools.kimi import call_kimi_with_tools_stream

    monkeypatch.setattr(kimi_mod, "_load_settings", lambda: {})
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    msg, err, tokens = call_kimi_with_tools_stream(messages=[], tools=[])
    assert msg is None
    assert err and "API key" in err
    assert tokens == 0
