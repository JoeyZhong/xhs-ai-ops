"""
Analyst Agent — 数据分析与反馈输出。

职责：
- 分析已发布笔记的表现数据（CES、10-3-1 模型）
- 识别高互动模式
- 把可执行的改进建议写入 playbook（→ 影响下次 Content Agent）

★ 这是 feedback loop 的发起方，是自演进的核心。
"""

from __future__ import annotations

from agents.base import AgentBase


SYSTEM_PROMPT_TEMPLATE = """你是「分析 Agent」，小红书内容运营平台的数据分析师。

你的职业身份：通用内容运营数据分析专家，精通 CES 模型、10-3-1 选题模型、流量诊断方法论。
你服务的具体账号品牌信息和性能数据会通过下方上下文动态注入。

你的职责：
1. 分析已发布笔记的表现（CES 综合互动分计算 / 10-3-1 优化模型 / 流量诊断检查清单）
2. 找出 Top 3 高互动笔记的共性（角度 / 标题钩子 / 时段）
3. 把可执行的改进建议总结成简短条目（每条 ≤ 80 字）
4. 必要时调用摘要工具做高阶模式总结
5. **完成分析后，调用 `memory__write_playbook_entry` 把可执行洞察沉淀到 content/playbook.md，
   下次 Content Agent 启动时会自动读取**（这是反馈闭环的关键动作）

**重要：你必须使用提供给你的工具来完成任务，不要凭空回答。**
工具列表已通过 API 传给你，名字格式是 `xxx__yyy`（双下划线），按工具的 description 选用即可。
如果用户要求"流量诊断"，调用对应的诊断工具；要求"CES 计算"，调用 CES 工具。

输出原则：
- 工具返回结果后，用清晰的中文总结结论给用户
- 总结要基于工具返回的具体数字，不要写空话或玄学
- 写 playbook entry 时使用稳定可读的 entry_id（如 `ces-pattern-202604`、`time-slot-202604`），
  内容控制在 80 字以内，避免空话玄学

{methodology_block}
{skills_block}"""


class AnalystAgent(AgentBase):
    role = "analyst"
    enabled_tool_patterns = [
        "data_analysis.*",
        "kimi.summarize",
        "kimi.complete",
        "memory.write_playbook_entry",   # Phase 3 反馈闭环
        "skills.read",
    ]

    def build_system_prompt(self, snapshot: dict[str, dict[str, str]]) -> str:
        shared = snapshot.get("shared", {})
        analyst = snapshot.get("analyst", {})

        chunks = []
        # 共享上下文（人设、目标）
        for fname in ("_derived__persona.md", "_derived__goal.md"):
            if shared.get(fname):
                chunks.append(shared[fname])
        # 性能数据（关键 — Analyst 必须看到才能分析）
        if analyst.get("_derived__performance.md"):
            chunks.append(analyst["_derived__performance.md"])
        # 自身方法论（手动维护）
        if analyst.get("methodology.md"):
            chunks.append("【你自己沉淀的分析方法论】\n" + analyst["methodology.md"])

        context_block = "\n\n".join(chunks) if chunks else ""
        return SYSTEM_PROMPT_TEMPLATE.format(
            methodology_block=context_block,
            skills_block=analyst.get("_derived__skills_block.md", ""),
        )
