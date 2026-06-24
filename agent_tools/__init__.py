"""
agent_tools — 工具注册中心 + 现有脚本的 Tool 包装。

导入本模块时，会触发所有子模块的自注册。
新增工具：在 agent_tools/ 下加一个 .py 文件，模块顶部用 registry.register(...) 即可。
"""

from agent_tools import registry  # noqa: F401

# 触发所有 Tool 模块自注册。失败时记录警告但不阻断（便于开发期增量加 tool）。
import importlib as _il
import warnings as _warn

_TOOL_MODULES = [
    "agent_tools.kimi",
    "agent_tools.search",
    "agent_tools.hot_monitor",
    "agent_tools.browser_fallback",
    "agent_tools.content_gen",
    "agent_tools.data_analysis",
    "agent_tools.memory_tools",
    "agent_tools.skills",
    "agent_tools.intel_evidence",
    "agent_tools.intent_classifier",
    "agent_tools.lead_outreach",
    "agent_tools.collect_xhs_intent",
    "agent_tools.collect_zhihu",       # V2 扩源
    "agent_tools.collect_zhubajie",    # V2 扩源
    "agent_tools.outreach_send",       # V2 写端（一键发送）
]

for _name in _TOOL_MODULES:
    try:
        _il.import_module(_name)
    except ImportError as _e:
        _warn.warn(f"agent_tools: skip {_name} ({_e})")

__all__ = ["registry"]
