# Spec Delta: feedback-loop

> 新建 capability。所有条款 `## ADDED`。

## ADDED Requirement: Memory 文件分层与权限

系统 SHALL 把 memory 组织为四个 scope，每个 scope 有明确的写入权限：

| Scope | 写入者 | 读取者 | 用途 |
|-------|------|------|------|
| `shared/` | Master / 用户 | 所有 Agent | 人设、benchmark 数据 |
| `intel/` | IntelAgent | IntelAgent + Master | 情报积累 |
| `content/playbook.md` | **AnalystAgent** | **ContentAgent** | 反馈闭环关键文件 |
| `analyst/` | AnalystAgent | AnalystAgent | 分析方法论 |

### Scenario: 跨 scope 写入被拒
- **WHEN** ContentAgent 尝试写 `intel/findings.md`
- **THEN** Memory 层抛 `WritePermissionDenied`

### Scenario: Analyst 写 playbook 触发 hook
- **WHEN** AnalystAgent 写 `content/playbook.md`
- **THEN** 触发 `on_memory_write` hook，hook 同步内容到 Storage backend

---

## ADDED Requirement: 冻结快照模式

Sub Agent SHALL 在 session 启动时**一次性**读取所有相关 memory 并构造 system prompt。
session 进行过程中 memory 文件被修改，**不会**影响当前 session 的 system prompt。

### Scenario: 同 session 内 memory 修改不生效
- **GIVEN** Content session 已启动，已读取 playbook.md v1
- **WHEN** Analyst 在该 Content session 进行中写入 playbook.md v2
- **THEN** 当前 Content session 的 system prompt 仍然是基于 v1
- **AND** 下次 Content session 启动时读取到 v2

### Scenario: 缓存 system prompt
- **WHEN** session 启动并构造完 system prompt
- **THEN** prompt 缓存在 session 对象上，session 内不重新构造（保护 LLM prefix cache）

---

## ADDED Requirement: Entry 模式管理 playbook

`memory/content/playbook.md` SHALL 用 `§` 作为 entry 分隔符，每个 entry 顶部有 `§id: <unique-id>` 标记。
Memory 层 SHALL 提供 `add_entry`、`replace_entry`、`remove_entry` 三个操作。

### Scenario: replace 现有 entry
- **GIVEN** playbook 含 `§id: ces-pattern-001`
- **WHEN** Analyst 调用 `replace_entry(file, "ces-pattern-001", new_content)`
- **THEN** 该条目被原地替换，其他条目不变

### Scenario: 新增 entry
- **WHEN** Analyst 用一个新 id 调用 `add_entry(file, "tip-2026-04-29", content)`
- **THEN** 在文件末尾追加新 entry，前面用 `§id: tip-2026-04-29` 标识

---

## ADDED Requirement: 注入攻击防护

Memory 写入 SHALL 拒绝包含可疑指令模式的内容。检测规则（任一匹配即拒绝）：
- 包含 `IGNORE PREVIOUS INSTRUCTIONS` 或中文等价
- 包含 `<system>` / `<\|im_start\|>` 等模型标记
- 包含 `[SYSTEM]:` / `### system:` 等伪 prompt 标记
- 包含连续 5 个以上反引号或异常长的连续相同字符（>50）

### Scenario: 检测到注入尝试
- **WHEN** 尝试写入含 "Ignore previous instructions and..."  的 entry
- **THEN** 抛 `MemoryInjectionDetected`，写审计

---

## ADDED Requirement: Agent 角色人设与账号人设分离

系统 SHALL 把 **Agent 角色身份** 和 **账号品牌人设** 严格分开：

| 类别 | 存放位置 | 范围 |
|------|---------|------|
| Agent 角色人设 | `agents/{intel,content,analyst}.py` 的 SYSTEM_PROMPT_TEMPLATE | 通用职业身份（情报分析师/内容创作者/数据分析师），跨账号不变 |
| 账号品牌人设 | `config/personas.json`（多账号容器） | 账号昵称、背景故事、风格备注、品牌口吻 |
| 账号关联 | `goals.json` 的 `persona_id` 字段 | 多个 goal 可共享一个 persona |

**约束**：
- Agent 模板中 MUST NOT 硬编码任何具体账号名（"示例品牌" 等）
- 账号信息 SHALL 通过 memory snapshot / derived context 在运行时注入
- 同一组 Agent 实例 MAY 被切换到不同账号（通过更换 active goal/persona）服务

### Scenario: 切换 active goal 后 Agent 服务于不同账号
- **GIVEN** goal_001 关联 persona_id="puji_paidang"，goal_002 关联 persona_id="another_brand"
- **WHEN** 用户切换到 goal_002 并提交 Content 任务
- **THEN** Content Agent 的 system prompt 中注入的是 another_brand 的人设
- **AND** Agent 角色描述（"内容 Agent"）保持不变

### Scenario: persona_id 找不到时回退
- **WHEN** goals.json.persona_id 在 personas.json 中不存在
- **THEN** 回退到 personas.json 的 `active_id`
- **AND** 再次找不到时回退到 legacy `config/persona.json`
- **AND** 仍找不到时 Agent 仍能启动（无账号信息块）

---

## ADDED Requirement: Content Agent system prompt 构造

ContentAgent 的 system prompt builder SHALL 按以下顺序拼接：
1. Agent 角色身份（来自代码模板，generic）
2. 账号人设（来自 `config/personas.json` 中 active goal.persona_id 关联的 persona）
3. `shared/title_formulas.md` + `shared/content_dimensions.md`（公式库与维度，账号无关）
4. `shared/benchmarks.md` 或派生 benchmarks（爆款标题库）
5. `content/playbook.md`（★ Analyst 反馈，最关键）
6. JSON 输出格式约束

### Scenario: playbook 缺失时优雅降级
- **WHEN** `content/playbook.md` 不存在或为空
- **THEN** Content Agent 仍能启动
- **AND** system prompt 中该段为空字符串
- **AND** Master 在审计中记录 `playbook_empty` warning

### Scenario: playbook 内容被注入到 prompt
- **GIVEN** playbook.md 含 entry "标题用数字法时，5 比 3 更好"
- **WHEN** Content Agent 启动
- **THEN** system prompt 中能看到该条建议
- **AND** Kimi 生成的标题倾向于使用数字 5

---

## ADDED Requirement: Analyst 自动写 playbook

AnalystAgent 完成性能分析任务后 SHALL 自动调用 memory 写入 playbook，规则：
- 发现 Top 3 高 CES 笔记的共性 → 写为 entry
- 发现某角度持续表现差 → 写为 entry（含负面 signal）
- 发现某发布时段效果突出 → 写为 entry
- 每次写入前 MUST 经过注入检测

### Scenario: 性能数据不足
- **WHEN** 已发布笔记 < 3 篇
- **THEN** AnalystAgent 跳过 playbook 写入
- **AND** 审计记录 `insufficient_data`
