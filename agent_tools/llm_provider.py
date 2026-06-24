"""
LLMProvider 抽象（P1.3）。

解耦 Kimi 硬编码，支持：
- KimiProvider：当前生产实现
- MockProvider：固定响应，用于测试 + Kimi 故障兜底
- FailoverProvider：primary 限频/异常 → fallback 接管

call_kimi_with_tools 改为薄壳调 _default_provider().call_chat_completions(...)。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional, Protocol, Tuple

from openai import OpenAI


# ── Protocol ───────────────────────────────────────────────────────────────

class LLMProvider(Protocol):
    """LLM 提供商抽象接口。"""

    def call_chat_completions(
        self,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int,
        temperature: float,
        tool_choice: str = "auto",
    ) -> Tuple[Optional[object], Optional[str], int]:
        """
        返回 (message, error, tokens_used)：
            - message: ChatCompletionMessage（含 .content 和 .tool_calls）
            - error: 失败时返回错误字符串
            - tokens_used: 本次消耗的 total_tokens
        """
        ...


# ── KimiProvider ───────────────────────────────────────────────────────────

CONFIG_DIR = Path(__file__).parent.parent / "config"


def _load_settings() -> dict:
    path = CONFIG_DIR / "settings.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


class KimiProvider:
    """Moonshot / Kimi 官方 API 实现。"""

    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None,
                 max_retries: int = 3):
        settings = _load_settings()
        self.api_key = api_key or settings.get("kimi_api_key") or os.environ.get("KIMI_API_KEY") or ""
        self.base_url = base_url or settings.get("kimi_base_url") or ""
        self.model = model or settings.get("kimi_model") or ""
        self.max_retries = max_retries

    def call_chat_completions(
        self,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int,
        temperature: float,
        tool_choice: str = "auto",
    ) -> Tuple[Optional[object], Optional[str], int]:
        if not self.api_key:
            return None, "Kimi API key not configured", 0

        # 120s：让"60~120s 才出结果"的慢模型调用一次成功，而不是 60s 超时后白白重试到数分钟。
        # 期间由 call_with_heartbeat 周期发心跳喂活前端（前端空闲超时同为 120s，不冲突）。
        client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=120)
        delay = 8
        for attempt in range(self.max_retries):
            try:
                kwargs = dict(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = tool_choice
                resp = client.chat.completions.create(**kwargs)
                usage = getattr(resp, "usage", None)
                tokens_used = getattr(usage, "total_tokens", 0) if usage else 0
                return resp.choices[0].message, None, tokens_used
            except Exception as e:
                err = str(e)
                is_retryable = ("429" in err or "overload" in err.lower()
                                or "rate" in err.lower() or "timeout" in err.lower())
                if is_retryable and attempt < self.max_retries - 1:
                    time.sleep(delay)
                    delay *= 3
                    continue
                return None, err, 0
        return None, f"已重试 {self.max_retries} 次", 0


# ── MockProvider ───────────────────────────────────────────────────────────

class MockProvider:
    """
    固定响应 Provider，用于：
    1. 单元测试（不消耗真实 API quota）
    2. Kimi 故障兜底（Failover 的 fallback）
    """

    def __init__(self, *, fixed_content: str = "", fixed_tool_calls: Optional[list] = None):
        self.fixed_content = fixed_content
        self.fixed_tool_calls = fixed_tool_calls or []

    def call_chat_completions(
        self,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int,
        temperature: float,
        tool_choice: str = "auto",
    ) -> Tuple[Optional[object], Optional[str], int]:
        # 构造一个类似 OpenAI ChatCompletionMessage 的对象
        msg = type("MockMessage", (), {
            "content": self.fixed_content,
            "tool_calls": self.fixed_tool_calls or None,
        })()
        return msg, None, 0


# ── FailoverProvider ───────────────────────────────────────────────────────

class FailoverProvider:
    """
    主备切换 Provider。
    primary 连续失败或限频时切到 fallback。
    """

    def __init__(self, primary: LLMProvider, fallback: LLMProvider,
                 *, fail_threshold: int = 2):
        self.primary = primary
        self.fallback = fallback
        self.fail_threshold = fail_threshold
        self._consecutive_fails = 0

    def call_chat_completions(
        self,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int,
        temperature: float,
        tool_choice: str = "auto",
    ) -> Tuple[Optional[object], Optional[str], int]:
        use_fallback = self._consecutive_fails >= self.fail_threshold

        if not use_fallback:
            msg, err, tokens = self.primary.call_chat_completions(
                messages, tools, max_tokens, temperature, tool_choice,
            )
            if err:
                self._consecutive_fails += 1
                if self._consecutive_fails >= self.fail_threshold:
                    # 切到 fallback 重试一次
                    msg, err, tokens = self.fallback.call_chat_completions(
                        messages, tools, max_tokens, temperature, tool_choice,
                    )
                    if not err:
                        self._consecutive_fails = 0
                return msg, err, tokens
            else:
                self._consecutive_fails = 0
                return msg, err, tokens

        # 已经在 fallback 模式
        msg, err, tokens = self.fallback.call_chat_completions(
            messages, tools, max_tokens, temperature, tool_choice,
        )
        if not err:
            # fallback 成功，尝试切回 primary（下次调用）
            self._consecutive_fails = 0
        return msg, err, tokens


# ── 默认 Provider 解析（从 settings.json）──────────────────────────────────

_DEFAULT_PROVIDER: Optional[LLMProvider] = None
_DEFAULT_PROVIDER_KEY: str = ""  # 上次初始化时的 api_key，key 变化自动重建


def _default_provider() -> LLMProvider:
    """延迟初始化默认 Provider（根据 settings.json 的 llm_provider 字段）。
    settings.json 的 kimi_api_key 变化时自动重建，无需重启进程。
    """
    global _DEFAULT_PROVIDER, _DEFAULT_PROVIDER_KEY

    settings = _load_settings()
    current_key = settings.get("kimi_api_key", "")
    if _DEFAULT_PROVIDER is not None and current_key == _DEFAULT_PROVIDER_KEY:
        return _DEFAULT_PROVIDER

    provider_name = settings.get("llm_provider", "kimi")

    if provider_name == "kimi":
        _DEFAULT_PROVIDER = KimiProvider()
    elif provider_name == "mock":
        _DEFAULT_PROVIDER = MockProvider(fixed_content="[mock] 测试响应")
    elif provider_name == "failover":
        primary = KimiProvider()
        fallback = MockProvider(fixed_content="[failover] Kimi 不可用，返回兜底响应。")
        _DEFAULT_PROVIDER = FailoverProvider(primary, fallback)
    else:
        _DEFAULT_PROVIDER = KimiProvider()

    _DEFAULT_PROVIDER_KEY = current_key
    return _DEFAULT_PROVIDER


def set_provider(provider: LLMProvider) -> None:
    """运行时覆盖默认 Provider（主要用于测试）。"""
    global _DEFAULT_PROVIDER
    _DEFAULT_PROVIDER = provider
