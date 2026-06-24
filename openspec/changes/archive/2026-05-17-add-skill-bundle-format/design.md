# Design Notes: add-skill-bundle-format

> 本文件记录设计阶段拍板，便于接手实施时不需要回溯讨论。

## 1. 为什么用 SKILL.md + skill.json 双文件，而不是 frontmatter-in-SKILL.md

锚定后 user 的原话：「一个 Skill 的核心物理载体是一份高度结构化的 `SKILL.md` 纯文本文件（**以及一个声明元数据的 `skill.json`**）」—— 明确点名两个独立文件。

收益：
1. **关注点分离**：SKILL.md 是给 LLM 阅读的方法论本体；skill.json 是给系统消费的元数据。各司其职。
2. **lint / 编辑器工具友好**：JSON 走 JSON schema 校验，markdown 走 markdown 工具，不需要混合 frontmatter parser。
3. **未来扩展元数据**：加 `author`、`license`、`hub_source_url` 等字段不污染 SKILL.md。
4. **与 references/ 子目录天然搭配**：`SKILL.md` 主纲领 + `references/*.md` 补充材料，目录形态合理。

放弃 frontmatter-in-SKILL.md 的代价：需要解析两个文件而非一个。可接受。

## 2. skill.json schema（v1）

```json
{
  "name": "钩子三段式写作",
  "description": "用户写笔记时遇到「开头怎么钩住读者」「3秒钩子」「反直觉钩子」",
  "version": "1.0.0",
  "suggested_for": ["content"]
}
```

| 字段 | 必需 | 类型 | 说明 |
|---|---|---|---|
| `name` | ✅ | string | 唯一标识，建议与目录名一致（不强校验，但 dashboard 创建时自动同步） |
| `description` | ✅ | string | 自然语言描述「何时该用这个方法论」，由 LLM 在 Methodology Library 中匹配 |
| `version` | ✅ | string | semver；缺省 `"1.0.0"`；本提案不实施版本比较，仅为 hub 提案铺垫 |
| `suggested_for` | ❌ | list[string] | **advisory only**，dashboard 安装时默认勾选哪些 role 用；agent 加载逻辑**完全无视该字段**（决策 C 锁定） |

**解析未识别字段**：静默忽略（向前兼容）。

**禁止字段**（本提案明确排除）：
- `tools_referenced`（per 锚定原则，方法论在 SKILL.md body 中自然语言描述即可）
- 任何指向可执行文件路径的字段
- 任何宣告"运行时副作用"的字段

## 3. 字段名映射表（迫迁参考）

| 旧 frontmatter (`*.md` YAML) | 新 `skill.json` | 处理 |
|---|---|---|
| `name: <X>` | `"name": "<X>"` | 原样复制 |
| `when_to_use: <Y>` | `"description": "<Y>"` | 重命名 |
| `tools_referenced: [...]` | — | 删除 |
| —（不存在） | `"version": "1.0.0"` | 新增，默认值 |
| —（不存在） | `"suggested_for": [<role>]` | 新增，按文件当前所在 role 派生 |

## 4. 引导句定稿（不可改）

`_build_skills_block()` 引导句**字面量**：

```
当某条方法论的 description 匹配当前任务场景时，调用 skills.read 把它注入你的工作记忆；
随后依方法论指导你的 Tool 调用与思考过程。方法论本身不执行任何动作，只指导你如何选择 Tool。
```

逐句解释：

| 句子 | 作用 | 改动风险 |
|---|---|---|
| 「当某条方法论的 description 匹配当前任务场景时」 | 锚定后概念：方法论 ≠ skill 这种名词；description 是字段名 | 改回 `when_to_use` 与 skill.json schema 脱节 |
| 「调用 skills.read 把它注入你的工作记忆」 | 明确动作是「注入」而非「读取并执行」；强化 Skill 与 Tool 边界 | 改成「执行 skill」违背锚定 |
| 「随后依方法论指导你的 Tool 调用与思考过程」 | 把 Skill 的"认知指导"角色与 Tool 的"物理动作"角色显式分离 | 删除此句 LLM 仍可能耦合理解 |
| 「方法论本身不执行任何动作，只指导你如何选择 Tool」 | 防御性 disclaimer，锁死 Skill ≠ Tool 心智模型 | 删除等于放弃概念锚定的强化 |

**避坑**：本次引导句**不**含 `skills.read(scope=, name=)` 完整调用格式字面量（参考 `add-skill-read-budget` design.md §3，避免 stuck-detection 正则误伤）。

## 5. 迫迁策略：一次性硬切换，不留兼容窗口

不做「同时支持单文件与目录两种格式」的双轨过渡。理由：

- 内置 skill 只有 6 个，迫迁工作量 < 1 小时
- 双轨意味着 parser 要分支判断，技术债立即诞生
- 用户场景里这个项目无多人协作冲突压力
- 一次硬切换让代码库始终只有一种 skill 形态

迫迁脚本是**一次性工具**，迫迁完即移入 `scripts/archive/`，不进入产品代码路径。

## 6. Dashboard 改造的最小集

本提案 Dashboard 只动「编辑表单从 frontmatter 改 skill.json」。**不**做：

- references/ 子目录的新增/编辑/删除 UI（留给 hub 提案）
- 多 skill 批量操作
- skill 版本号 UI（留给 hub 提案）
- ZIP 包上传按钮（留给 hub 提案）

最小集足以让现有运营人员继续单 skill CRUD，不破坏使用体验。

## 7. 不在本次 change 范围（明确划线）

- **不**新建 `_skill_hub/` 武器库目录
- **不**实现 ZIP 解压安装器
- **不**实现 skill 卸载 / 重新激活 流程
- **不**实现 assignment matrix（指派给哪些 agent）的 dashboard 矩阵 UI
- **不**实现 URL 拉取 / git clone skill 来源
- **不**新增 `skills.write` Agent 端工具
- **不**改动 `agents/policy.py` 的 `enabled_tool_patterns`（`skills.read` 仍在白名单）
- **不**改动 `add-skill-read-budget` 的熔断逻辑
- **不**实施 skill 版本号比较（version 字段只是占位）

以上任一需求若在实施过程中被发现是真痛点，**单独提案**，不要顺手做。

## 8. 与 `add-skill-read-budget` 的关系

两份提案**互相独立可并行实施**：

| 改动面 | add-skill-read-budget | add-skill-bundle-format |
|---|---|---|
| `agent_tools/skills.py` 计数器逻辑 | ✅ 修改 | ❌ 不动 |
| `agents/skills.py` parser | ❌ 不动 | ✅ 重写 |
| `agents/memory.py` list/read | ❌ 不动 | ✅ 重写 |
| `agents/base.py` finally hook | ✅ 新增 | ❌ 不动 |
| `agents/base.py` `_build_skills_block` 措辞 | ❌ 不动 | ✅ 重写 |
| `config/settings.json` | ✅ 新增字段 | ❌ 不动 |

**推荐实施顺序**：先 read-budget（小且独立）、后 bundle-format（牵涉迫迁），但反过来也可以。

## 9. 兼容性影响评估

- **运行时**：进程重启后新 parser 立即生效，无运行期热切换问题
- **数据**：6 个内置 skill 一次性迫迁，git 提交即完成
- **测试**：原 18 个测试用例约 60% 需要重写（涉及 frontmatter parsing 部分）
- **外部依赖**：无（不需要安装新 Python 包；保留 `pyyaml` 仅供其他模块使用）
- **API contract**：`skills.read` Tool 的入参与返回结构**不变**，仅返回 content 字段的来源从 .md frontmatter+body 改为纯 SKILL.md body

## 10. 为什么不在本提案统一 dashboard CRUD 的「人在回路」UX 改动

Dashboard 当前是单 skill 编辑形态，运营人员可以快速改一个 skill 的 description 或 body。本提案保持这个体验**不变**，只换底层文件存储格式。

未来 hub 提案会引入：
- 武器库整体视图
- 跨 role 的 assignment 矩阵
- ZIP 上传 / 一键安装

那些是**运营心智模型的大变**，应该在 hub 提案里集中处理。本提案保持 dashboard 用户感知"几乎无变化"。
