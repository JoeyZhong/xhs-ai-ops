# Proposal: 为每个 Sub Agent 引入 Skills 系统

## Why

当前架构里每个 Sub Agent 已经有：
- **独立记忆**（`memory/{tenant}/{role}/*.md`，写入权限矩阵保护）
- **独立工具**（`enabled_tool_patterns` + `ToolPolicy`）

但缺少**可复用的工作流知识**。当用户提一个非平凡需求（"分析爆款规律"），模型需要自己临时拼工具、定步骤，质量取决于模型的临场发挥，结果不稳定、也不能积累。

Skills 解决这个问题：把"遇到 X 场景按 Y 步骤做"沉淀成 markdown 文件，agent 启动时读到自己 scope 下的所有 skills，遇到匹配场景按 skill 走。每个 agent 的 skills 是它自己的"长期工作经验"，独立演进。

## What

为每个 Sub Agent 加 **Skills** 子系统：

```
memory/{tenant}/
├── shared/
├── intel/
│   ├── findings.md
│   └── skills/                      ← 新增
│       ├── 爆款规律分析.md
│       └── 关键词扩展.md
├── content/
│   ├── playbook.md
│   └── skills/                      ← 新增
│       ├── 钩子三段式写作.md
│       └── 评论引导句库.md
└── analyst/
    ├── methodology.md
    └── skills/                      ← 新增
        ├── 10-3-1实操.md
        └── 流量异常排查.md
```

**Skill 文件格式（YAML frontmatter + markdown body）：**

```markdown
---
name: 爆款规律分析
when_to_use: 用户问「为什么这些笔记火了」「找共性」「找规律」
tools_referenced: [search.collect_notes, data_analysis.run_10_3_1_model]
---

# 步骤
1. ...
2. ...

# 输出格式
...
```

**运行机制：**
1. Agent 启动时枚举自身 scope 下 `skills/*.md`，解析 frontmatter
2. system prompt 里注入精简的"可用 skills 列表"（只放 name + when_to_use，正文按需加载）
3. Agent 主循环里，模型可以"读取 skill 全文"——通过新工具 `skills.read(name)`
4. 模型遇到匹配的 when_to_use 时，主动调 `skills.read` 拿完整步骤，按步骤执行

## Impact

**新增能力（capability）：**
- `agent-skills` — Skills 加载、检索、读取的全流程

**改造现有代码：**
- `agents/memory.py`：新增 `list_skills(scope)` 方法
- `agents/base.py`：`_collect_memory_snapshot` 把 skills 摘要注入；新增 skills.read tool
- `agents/{intel,content,analyst}.py`：prompt builder 加入 "Available Skills" 区段
- `dashboard.py`：在「人设管理」页面之外新增「🎯 Skills 管理」（每个 agent 一个 tab，可视化增删改 skill 文件）

**不影响：**
- 现有 memory 文件（playbook / findings / methodology）
- 现有 tool registry（skills.read 是新加的一个 tool）
- 现有 policy 系统（only analyst 等权限规则继续生效）

## Risk

| 风险 | 缓解 |
|------|------|
| Skills 太多撑爆 system prompt | 只注入 name + when_to_use 摘要，全文按需 `skills.read` |
| Skill 内容质量参差 → agent 误用 | 提供 3-5 个高质量初始 skill 作为模板；Skills 管理页加 lint 检查 |
| Skills 跨 scope 误读（content agent 读到 intel 的 skill）| Skills 走 memory 权限矩阵，每个 scope 各自隔离 |
| Skill 内容被注入攻击 | 复用已有 `MemoryInjectionDetected` 检测 |
| Frontmatter 格式不严谨 | 加严格 schema 校验，无效 skill 跳过并记录到 audit |

## 开发量估算

- 单个 capability，4-5 个文件改动
- 不涉及 LLM 调用模式变化
- 预计 1 个 phase 即可完成（约 2-3 小时实施 + 验证）
