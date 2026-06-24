"""
Tool 权限策略（参考 OpenClaw tool-policy.ts 三层结构）。

检查顺序（优先级从高到低）：
1. deny_patterns      — 黑名单一票否决
2. also_allow[agent]  — agent 级额外许可
3. allow_patterns     — 全局允许
4. default_action     — 兜底默认
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Literal


@dataclass
class ToolPolicy:
    default_action: Literal["allow", "deny"] = "deny"
    allow_patterns: list[str] = field(default_factory=list)
    deny_patterns:  list[str] = field(default_factory=list)
    also_allow:     dict[str, list[str]] = field(default_factory=dict)

    def check(self, agent_name: str, tool_name: str) -> bool:
        # 1. deny 优先
        for p in self.deny_patterns:
            if fnmatch(tool_name, p):
                return False
        # 2. agent 级额外许可
        for p in self.also_allow.get(agent_name, []):
            if fnmatch(tool_name, p):
                return True
        # 3. 全局 allow
        for p in self.allow_patterns:
            if fnmatch(tool_name, p):
                return True
        # 4. 默认
        return self.default_action == "allow"


# ── 预设 policy（Master 用） ─────────────────────────────────────────────

def policy_for_intel() -> ToolPolicy:
    return ToolPolicy(
        default_action="deny",
        allow_patterns=[
            "search.*",
            "hot_monitor.*",
            "browser_fallback.*",
            "intel.extract_evidence",
            "skills.read",
        ],
        deny_patterns=[
            "*.delete_*",
            "*.drop_*",
        ],
    )


def policy_for_content() -> ToolPolicy:
    return ToolPolicy(
        default_action="deny",
        allow_patterns=[
            "content_gen.*",
            "skills.read",
        ],
        deny_patterns=[
            "search.*",
            "hot_monitor.*",
            "browser_fallback.*",
            "*.delete_*",
        ],
    )


def policy_for_analyst() -> ToolPolicy:
    return ToolPolicy(
        default_action="deny",
        allow_patterns=[
            "data_analysis.*",
            "kimi.summarize",
            "kimi.complete",                # 允许做 free-form 分析
            "memory.write_playbook_entry",  # Phase 3 反馈闭环关键工具
            "skills.read",
        ],
        deny_patterns=[
            "search.*",
            "hot_monitor.*",
            "*.delete_*",
        ],
    )
