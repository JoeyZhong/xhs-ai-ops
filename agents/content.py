"""
Content Agent — 笔记内容生成。

职责：
- 根据人设和参考资料生成小红书笔记
- 读取 Analyst 写入的 playbook（反馈闭环关键）
- 不主动写 memory（只读）
"""

from __future__ import annotations

from agents.base import AgentBase
from agents.memory import parse_entries
from agents.playbook_learning import extract_auto_block


SYSTEM_PROMPT_TEMPLATE = """你是「内容 Agent」，小红书内容运营平台的内容创作专家。

你的职业身份：通用小红书笔记创作者，精通爆款标题公式与多维度选题方法论。
你服务的具体账号品牌人设会通过下方上下文动态注入，你需要严格遵守当前服务账号的风格、口吻和品牌定位。

## 核心职责
根据账号人设、公式工具、爆款参考、playbook 反馈，按用户**实际意图**输出对应形态：

| 用户说什么 | 你输出什么 |
|----------|----------|
| 「选题」「主题建议」「方向」「灵感」 | **只输出选题清单**（带角度/钩子的简短标题列表，5-10 个），不要写正文 |
| 「标题」「起标题」 | 只输出多个标题候选，不写正文 |
| 「写一篇」「生成笔记」「初稿」 | 输出单篇完整笔记（主标题 + 正文 + 标签），不需要调工具 |
| 「生成 N 篇」（N≥2）「批量生成」 | 调用 `content_gen__generate_batch` 工具，传入 batch_size=N |
| 「润色」「改」 | 只改用户给的内容，不要重写 |

**重要原则：**
- 任何要求生成 ≥2 篇笔记的请求都必须调用 `content_gen.generate_batch` 工具，不要在文本中手写多篇笔记
- 严格匹配用户的请求颗粒度——要选题就给选题，**不要主动扩展为完整笔记**
- 简单请求直接回答，无需调工具
- 输出语言用中文，结构清晰用 markdown 列表/标题

## 处理上游任务数据（重要）
当任务描述中包含 `---` 分隔的"参考数据"块时：
- 将其作为**背景信息**，提取主题方向和关键洞察
- **严禁把参考数据的原文句子写入笔记正文**，即使原文看起来像结论
- 把洞察转化为符合账号人设的自然口语表达

## 创作要点（生成具体内容时遵守）
- 标题字数 16-24，带 1-2 个 emoji
- 正文前 3 行必须有钩子（数字/痛点/悬念）
- 标签 3-8 个，大词+小词组合
- **严格遵守下方 playbook 中 Analyst 沉淀的优化建议（如果有）**
- 灵活使用下方注入的 5 大爆款标题公式

{persona_block}
{formulas_block}
{dimensions_block}
{playbook_block}
{skills_block}"""


class ContentAgent(AgentBase):
    role = "content"
    enabled_tool_patterns = [
        "content_gen.*",
        "skills.read",
    ]

    def build_system_prompt(self, snapshot: dict[str, dict[str, str]]) -> str:
        shared = snapshot.get("shared", {})
        content = snapshot.get("content", {})

        # 1. 账号品牌人设（来自 config/personas.json 通过 derived_persona.md 注入）
        #    persona.md 已经废弃，只在 _derived__persona.md 取
        persona_chunks = []
        if shared.get("_derived__persona.md"):
            persona_chunks.append(shared["_derived__persona.md"])
        if shared.get("_derived__goal.md"):
            persona_chunks.append(shared["_derived__goal.md"])
        if shared.get("benchmarks.md"):
            persona_chunks.append(shared["benchmarks.md"])
        if shared.get("_derived__benchmarks.md"):
            persona_chunks.append(shared["_derived__benchmarks.md"])

        persona_block = ("\n\n".join(persona_chunks) if persona_chunks
                          else "（当前未关联账号人设，请检查 goal.persona_id 配置）")

        # 2. 创作公式库（账号无关，所有内容创作通用）
        formulas_block = ""
        if shared.get("title_formulas.md"):
            formulas_block = ("\n\n【📚 五大爆款标题公式（必备工具）】\n"
                                + shared["title_formulas.md"])

        # 3. 维度细化（账号无关）
        dimensions_block = ""
        if shared.get("content_dimensions.md"):
            dimensions_block = ("\n\n【🎯 四大内容维度（选题策划工具）】\n"
                                  + shared["content_dimensions.md"])

        # 4. Analyst 反馈（最关键，跨 session 增量积累）
        #    ★ P3.2.D4: 仅注入 status=active 的 entry（跳过 draft / rejected）
        #    ★ P3.4.1: 额外注入 AnalystEvaluator 写的三态判定自动区
        playbook_block = ""
        if content.get("playbook.md"):
            pb_md = content["playbook.md"]
            _, entries = parse_entries(pb_md)
            active_entries = {
                eid: e for eid, e in entries.items()
                if e.status == "active"
            }
            chunks: list[str] = []
            if active_entries:
                chunks.append("\n\n".join(
                    f"[{eid}] {e.body}" for eid, e in active_entries.items()
                ))
            auto = extract_auto_block(pb_md)
            if auto:
                chunks.append("【角度表现判定】\n" + auto)
            if chunks:
                playbook_block = ("\n\n【★ 来自 Analyst 的反馈与优化建议（务必参考）】\n"
                                  + "\n\n".join(chunks))

        return SYSTEM_PROMPT_TEMPLATE.format(
            persona_block=persona_block,
            formulas_block=formulas_block,
            dimensions_block=dimensions_block,
            playbook_block=playbook_block,
            skills_block=content.get("_derived__skills_block.md", ""),
        )
