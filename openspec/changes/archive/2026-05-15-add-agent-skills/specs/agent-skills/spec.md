# Spec Delta: agent-skills

> 新建 capability。所有条款 `## ADDED`。

## ADDED Requirement: Skills 文件结构与解析

每个 Sub Agent SHALL 拥有自己的 skills 目录：`memory/{tenant}/{role}/skills/*.md`。
每个 skill 文件 MUST 包含 YAML frontmatter，至少含字段：
- `name`：唯一标识，与文件名一致
- `when_to_use`：自然语言描述何时使用（≤ 100 字）
- `tools_referenced`（可选）：该 skill 涉及的 tool 名列表

frontmatter 后是 markdown body，描述具体步骤。

### Scenario: 缺失必需字段
- **GIVEN** skill 文件缺少 `name` 或 `when_to_use`
- **WHEN** Agent 启动加载 skills
- **THEN** 跳过该 skill，写审计日志（reason=`invalid_frontmatter`）
- **AND** 不向 system prompt 注入该 skill

### Scenario: name 与文件名不一致
- **WHEN** frontmatter `name: "X"` 但文件叫 `Y.md`
- **THEN** 跳过加载，audit 记录 `name_filename_mismatch`

---

## ADDED Requirement: Skills 跨 scope 隔离

Sub Agent SHALL 仅能列出和读取自身 role 对应 scope 下的 skills。
Tool `skills.read(scope, name)` MUST 校验调用方 role 与 scope 匹配。

### Scenario: Content Agent 试图读 Intel 的 skill
- **WHEN** ContentAgent 调用 `skills.read(scope="intel", name="爆款规律分析")`
- **THEN** Tool 返回 `{ok: False, error: "cross-scope read denied"}`
- **AND** audit 记录 `skills_cross_scope_denied`

### Scenario: 共享 skills 不存在
- 当前不引入 `shared/skills/`。如未来需要，单独提案

---

## ADDED Requirement: System prompt 注入 skills 索引

Sub Agent SHALL 在启动时读取自身 scope 下所有有效 skills，
把 `[{name, when_to_use}, ...]` 列表注入 system prompt。
**不**注入 skill body（避免 token 膨胀）。

### Scenario: 无 skills 时优雅降级
- **WHEN** scope 下无 skill 文件
- **THEN** system prompt 中该区段为空字符串
- **AND** Agent 正常运行（仅失去 skill 引导能力）

### Scenario: Skills 列表长度限制
- 如某 scope 下超过 30 个 skill
- **THEN** 取前 30 个（按文件名字母序），其余忽略
- **AND** audit 记录 `skills_overflow_truncated`

---

## ADDED Requirement: skills.read Tool

注册新 tool `skills.read`：
- 参数：`scope: str`, `name: str`
- 返回：`{ok, data: {content: str, frontmatter: dict}}`
- 实现位置：`agent_tools/skills.py`
- 自动加入 intel/content/analyst 的 `enabled_tool_patterns`

### Scenario: 读取存在的 skill
- **WHEN** IntelAgent 调用 `skills.read(scope="intel", name="爆款规律分析")`
- **THEN** 返回完整 body 和 frontmatter
- **AND** audit 记录 `skills_read`，含 skill name

### Scenario: 读取不存在的 skill
- **WHEN** name 不存在
- **THEN** 返回 `{ok: False, error: "skill not found"}`

---

## ADDED Requirement: Skills 写入复用 Memory 层注入检测

通过 dashboard 或 memory.write 创建/编辑 skill 时，
内容 MUST 经过现有 `MemoryInjectionDetected` 检测（`agents/memory.py`）。

### Scenario: skill 含注入模式
- **WHEN** 用户在 dashboard 编辑 skill 内容含 "Ignore previous instructions"
- **THEN** 保存时被拒，UI 显示错误

---

## ADDED Requirement: Dashboard Skills 管理页

侧边栏 SHALL 提供 `🎯 Skills 管理` 入口。该页 SHALL：
- 三个 tab 对应 intel/content/analyst
- 每个 tab 列出当前 scope 下所有 skill（name / when_to_use / 文件大小 / 修改时间）
- 支持 view（查看完整内容）/ edit（编辑保存）/ create（新建）/ delete（删除）

### Scenario: 编辑后保存
- **WHEN** 用户修改某 skill 并点保存
- **THEN** 调 `memory.write` 保存（自动经过注入检测）
- **AND** 显示成功提示
- **AND** 下次该 agent 启动新 session 时读到新内容（冻结快照原则）

### Scenario: 创建无效 frontmatter
- **WHEN** 用户保存的 skill 缺 `when_to_use`
- **THEN** 显示 lint 错误，不允许保存
