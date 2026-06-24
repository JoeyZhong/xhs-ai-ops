# Spec Delta: agent-skills

> 修改现有 capability `agent-skills`（2026-05-15 上线）。
> 五处 MODIFIED + 一处 ADDED + 一处 REMOVED，全面对齐 Claude Code 标准的 SKILL.md + skill.json bundle 格式。

## MODIFIED Requirement: Skills 文件结构与解析

**原条款**：每个 skill 是 `memory/{tenant}/{role}/skills/<name>.md` 单文件，YAML frontmatter 必填 `name` / `when_to_use`，可选 `tools_referenced`。

**新条款**：每个 skill 是 `memory/{tenant}/{role}/skills/<skill-id>/` **目录**，目录内 MUST 包含：

- `SKILL.md` — 纯方法论文本（markdown body，**禁止**含 YAML frontmatter）
- `skill.json` — 元数据 sidecar，schema 见下文
- `references/` — **可选**，存放纯文本补充材料（仅允许 `*.md`）

`skill.json` schema：

| 字段 | 必需 | 类型 | 说明 |
|---|---|---|---|
| `name` | ✅ | string | skill 名称，建议与目录名一致 |
| `description` | ✅ | string | 自然语言描述何时使用本方法论 |
| `version` | ✅ | string | semver，缺省 `"1.0.0"` |
| `suggested_for` | ❌ | list[string] | **advisory only**，Dashboard 安装时默认勾选；agent 加载逻辑必须无视此字段 |

未识别字段 SHALL 被解析器静默忽略（向前兼容）。

### Scenario: 缺失必需文件

- **GIVEN** skill 目录缺少 `SKILL.md` 或 `skill.json`
- **WHEN** Agent 启动加载 skills
- **THEN** 跳过该目录，不向 system prompt 注入
- **AND** audit 记录 `invalid_skill_bundle`，包含具体缺失文件名

### Scenario: skill.json 语法错误

- **WHEN** `skill.json` 不是合法 JSON 或必填字段（`name` / `description`）缺失
- **THEN** parser 抛 `SkillParseError`，list_skills 跳过该目录
- **AND** audit 记录 `skill_json_invalid`

### Scenario: SKILL.md 含 YAML frontmatter

- **WHEN** SKILL.md 内容以 `---` 开头
- **THEN** 加载时仍然按整文件原样作为 body 处理（**不**做 frontmatter 解析）
- **AND** audit 记录 warning `skill_md_contains_frontmatter`，但不阻断加载

### Scenario: references/ 子目录存在

- **WHEN** skill 目录含 `references/` 子目录
- **THEN** 加载流程**不**自动注入 references 内容到 system prompt
- **AND** references 文件仅供后续 hub 提案 / dashboard 查看，本 capability 不消费

---

## MODIFIED Requirement: System prompt 注入 skills 索引

**原条款**：注入 `[{name, when_to_use}, ...]` 索引，body 按需 read。

**新条款**：注入 `[{name, description}, ...]` 索引，引导文案 SHALL 明确「方法论」与「Tool」的概念分离。

### Scenario: skills_block 文案锁定

- **WHEN** `_build_skills_block()` 渲染索引块
- **THEN** 标题 SHALL 为 `【🎯 可用方法论 (Methodology Library)】`
- **AND** 每条 skill 摘要 SHALL 展示 `description` 前 80 字（不含换行）
- **AND** 引导句 MUST 包含以下三个语义点：
  - 「方法论 description 匹配任务时调用 skills.read」
  - 「skills.read 把方法论注入工作记忆」
  - 「方法论不执行动作，只指导 Tool 选择」

### Scenario: 引导句不得包含完整工具调用字面量

- **WHEN** 引导文案被构造
- **THEN** 文案 MUST NOT 包含 `skills.read(scope=<val>, name=<val>)` 形态的完整调用字面量
- **AND** 以防触发 `agents/base.py` 中 `_YAML_DESC_CALL_RE` / `_JSON_DESC_CALL_RE` 等 pseudo-tool-call stuck-detection 误判

---

## MODIFIED Requirement: skills.read Tool

**原条款**：返回 `{ok, data: {content: str, frontmatter: dict}}`，content 来自 .md frontmatter+body。

**新条款**：返回 `{ok, data: {content: str}}`，content 来自 `<dir>/SKILL.md` 纯 body 文本，**不**含 frontmatter。

工具 schema description SHALL 更新为：「Inject a skill's methodology text into context, by name and scope.」

### Scenario: 读取成功

- **WHEN** IntelAgent 在 task_id="t-100" 调用 `skills.read(scope="intel", name="爆款规律分析")`
- **THEN** 返回 SKILL.md 纯 body 文本（无 frontmatter / 无 skill.json 内容）
- **AND** audit 记录 `skills_read`，含 skill name

### Scenario: 读取的 body 必须是纯方法论文本

- **WHEN** Agent 调用 skills.read 成功
- **THEN** 返回的 content 字符串 MUST NOT 以 `---` 开头（即不含 YAML frontmatter）
- **AND** content 字符串 MUST 为完整 SKILL.md 文件内容

---

## MODIFIED Requirement: MemoryLayer.list_skills / read_skill

**原条款**：扫描 `skills/*.md` 文件。

**新条款**：扫描 `skills/` 下的**子目录**，每个含 `SKILL.md` 的子目录视为一个 skill。

### Scenario: 子目录枚举

- **WHEN** `MemoryLayer.list_skills(tenant_id, "intel")` 被调用
- **THEN** 返回 `<tenant>/intel/skills/` 下所有含 `SKILL.md` 的子目录对应的 ParsedSkill 列表
- **AND** 排序按子目录名字母序

### Scenario: 子目录无 SKILL.md 被跳过

- **GIVEN** `<scope>/skills/test_drafts/` 子目录存在但无 SKILL.md
- **WHEN** list_skills 枚举
- **THEN** test_drafts 被跳过，不出现在返回列表

### Scenario: read_skill 返回 SKILL.md 路径

- **WHEN** `MemoryLayer.read_skill(tenant_id, "intel", "爆款规律分析")` 被调用
- **THEN** 返回 `<tenant>/intel/skills/爆款规律分析/SKILL.md` 的全部文本

---

## ADDED Requirement: Skills 元数据 schema 版本管理

`skill.json` 文件 SHALL 包含 `version` 字段（semver 格式字符串）。本 capability 阶段**不实施**版本比较或升级逻辑；该字段仅作为占位，为后续 hub 提案的版本管理铺垫。

### Scenario: version 字段缺省

- **WHEN** `skill.json` 不包含 `version` 字段
- **THEN** parser SHALL 自动填充 `"1.0.0"`
- **AND** **不**抛错、**不**记 warning

### Scenario: version 非合法 semver

- **WHEN** `skill.json` 的 `version` 字段不是合法 semver 字符串（如 `"abc"`）
- **THEN** 本阶段**不**做格式校验，原样接受
- **AND** 留待 hub 提案实施时再加严

---

## REMOVED Requirement: tools_referenced 字段

**原条款**：YAML frontmatter 中可选 `tools_referenced: [tool_name, ...]` 字段，描述性，不构成执行权。

**移除理由**：

- 按锚定后 Skill 概念，方法论应在 SKILL.md body 中以自然语言描述涉及的 Tool
- 单独字段属冗余元数据，且其"描述 vs 授权"语义易引起 LLM 误解
- 完全参照 Claude Code 标准 schema（仅 `name` / `description`）

迫迁时所有现有 skill 的 `tools_referenced` 字段直接丢弃，不迁移到 `skill.json`。

### Scenario: 迫迁时丢弃 tools_referenced

- **WHEN** `scripts/migrate_skills_to_bundle.py` 处理含 `tools_referenced` 的旧 frontmatter
- **THEN** 字段被丢弃
- **AND** `skill.json` 中**不**生成 `tools_referenced` 字段

### Scenario: 新 skill.json 含 tools_referenced 字段

- **GIVEN** 用户在 Dashboard 创建 skill 时，skill.json 误填了 `tools_referenced`
- **WHEN** parser 解析
- **THEN** 字段被静默忽略（向前兼容策略，不阻断加载）
- **AND** 不出现在 ParsedSkill 数据结构里

---

## 兼容性矩阵

| 调用方 | 原行为 | 新行为 |
|---|---|---|
| Agent system prompt 注入 | 看到 frontmatter 字段 `when_to_use` | 看到 skill.json 字段 `description` |
| `skills.read` Tool 返回 | content 含完整 .md（frontmatter + body） | content 仅 SKILL.md body（纯方法论） |
| 单元测试 fixture | 创建 `.md` 文件 | 创建 `<dir>/{SKILL.md, skill.json}` |
| Dashboard 编辑界面 | YAML frontmatter 表单 + body 文本框 | skill.json 字段表单 + SKILL.md 文本框 |
| 内置 skill 加载 | 读 `*.md` | 读 `<dir>/SKILL.md` + `<dir>/skill.json` |
