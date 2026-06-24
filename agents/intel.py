"""
Intel Agent — 情报收集与市场洞察。

职责：
- 调用搜索/热词工具采集小红书数据
- 必要时主动用浏览器兜底
- 输出结构化的情报摘要
"""

from __future__ import annotations

from agents.base import AgentBase


SYSTEM_PROMPT_TEMPLATE = """你是「情报 Agent」，小红书内容运营平台的市场洞察分析师。

你的职业身份：通用情报采集与市场洞察专家。
你服务的具体账号品牌信息会通过下方上下文动态注入，你需要按当前服务账号的关键词和方向工作。

你的职责：
1. 使用提供的工具采集小红书数据（笔记搜索 / 热词建议 / 浏览器兜底）
2. Cookie 失效时切到浏览器兜底工具
3. 把采集结果用结构化格式总结给用户

**重要：你必须使用提供给你的工具来完成任务，不要凭空回答。**
工具列表已通过 API 传给你，名字格式是 `xxx__yyy`（双下划线），按工具的 description 选用即可。

工作原则：
- 单次任务采集量控制合理（笔记 ≤ 50 条，关键词 ≤ 5 个），避免反爬
- 工具失败时检查错误信息再重试，不要无脑重试
- 完成后用简短中文总结：抓到多少 / 关键发现 / 异常情况

{shared_block}
{intel_findings_block}
{skills_block}"""


class IntelAgent(AgentBase):
    role = "intel"
    enabled_tool_patterns = [
        "search.*",
        "hot_monitor.*",
        "browser_fallback.*",
        "skills.read",
    ]

    def build_system_prompt(self, snapshot: dict[str, dict[str, str]]) -> str:
        shared = snapshot.get("shared", {})
        intel = snapshot.get("intel", {})

        # 拼接所有 shared 数据（含派生）
        shared_block = ""
        chunks = []
        for fname, content in shared.items():
            if content:
                chunks.append(content)
        if chunks:
            shared_block = "【账号背景、目标与基线数据】\n\n" + "\n\n".join(chunks)

        intel_block = ""
        if intel.get("findings.md"):
            intel_block = "\n\n【过往情报积累】\n" + intel["findings.md"]

        skills_block = intel.get("_derived__skills_block.md", "")

        return SYSTEM_PROMPT_TEMPLATE.format(
            shared_block=shared_block,
            intel_findings_block=intel_block,
            skills_block=skills_block,
        )
