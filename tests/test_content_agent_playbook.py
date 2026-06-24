"""P3.4.1 · ContentAgent system prompt 注入 playbook 自动区。

ContentAgent.build_system_prompt 除了注入 active entry，还要注入
<!-- analyst-auto: v2 --> 块（AnalystEvaluator 写的三态判定规律）。
"""
from __future__ import annotations

from agents.content import ContentAgent
from agents.playbook_learning import AUTO_BEGIN, AUTO_END


def _snapshot(playbook_md: str) -> dict:
    return {"shared": {}, "content": {"playbook.md": playbook_md}}


def test_auto_block_injected():
    agent = ContentAgent.__new__(ContentAgent)
    pb = f"# 手写区\n忽略我。\n\n{AUTO_BEGIN}\nAUTO_MARKER 反直觉型已验证\n{AUTO_END}"
    prompt = agent.build_system_prompt(_snapshot(pb))
    assert "AUTO_MARKER" in prompt


def test_no_auto_block_no_crash():
    agent = ContentAgent.__new__(ContentAgent)
    prompt = agent.build_system_prompt(_snapshot("# 只有手写区，无自动块"))
    assert isinstance(prompt, str)
    assert "analyst-auto" not in prompt  # 注释标记本身不应漏进 prompt


def test_empty_playbook_no_crash():
    agent = ContentAgent.__new__(ContentAgent)
    prompt = agent.build_system_prompt({"shared": {}, "content": {}})
    assert isinstance(prompt, str)
