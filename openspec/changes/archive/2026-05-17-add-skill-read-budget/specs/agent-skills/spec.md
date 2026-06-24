# Spec Delta: agent-skills

> 修改现有 capability `agent-skills`（2026-05-15 上线）。
> 在 `skills.read Tool` 需求基础上新增「读取预算」子需求，并修改原 `读取存在的 skill` 场景以包含熔断分支。

## ADDED Requirement: Skills 读取预算熔断

`skills.read` Tool SHALL 在 per-task 粒度上对**成功读取**次数计数；当计数达到配置阈值 `skill_read_budget`（默认 `2`，从 `config/settings.json` 读取）时，下一次成功路径的调用 MUST 返回带 `[SKILL_BUDGET_EXHAUSTED]` 标签的 `ok:false` 熔断体，**不**读取磁盘。

`AgentBase.run()` MUST 在 `try/finally` 退出路径上清理该任务对应的计数器，确保进程长期运行不泄漏。

失败路径（参数缺失、scope mismatch、memory layer 缺失、skill not found、idempotency 缓存命中）SHALL **不**计入预算。

### Scenario: 达到预算阈值后熔断

- **GIVEN** `skill_read_budget = 2`，IntelAgent 在 task_id="t-001" 上已成功 `skills.read` 两个不同 skill
- **WHEN** Agent 再次调用 `skills.read(scope="intel", name=<任意第三个 skill>)`
- **THEN** 返回 `{ok: False, error: "🚨 [SKILL_BUDGET_EXHAUSTED] 本任务的技能读取额度已耗尽。严禁更换参数或重复尝试本工具调用。请直接综合已读取的方法论输出最终答案（AgentResult.success）。"}`
- **AND** 计数器**不**进一步递增
- **AND** 磁盘**不**被读取

### Scenario: 任务结束后预算重置

- **GIVEN** task_id="t-001" 的预算已耗尽
- **WHEN** `AgentBase.run()` 在 `finally` 块中调用 `clear_budget("t-001")`
- **THEN** `_BUDGET_COUNTERS` 中不再存在 key="t-001"
- **AND** 下一次以 task_id="t-001" 调用 `skills.read` 的可用额度恢复为 `skill_read_budget`

### Scenario: 失败读取不占用预算

- **GIVEN** `skill_read_budget = 2`，task_id="t-002" 预算未消耗
- **WHEN** Agent 连续以错误 scope（cross-scope）或不存在的 name 调用 `skills.read` 三次
- **THEN** 三次均返回各自原有的 `ok:False` 错误（**不**是熔断文案）
- **AND** 该 task_id 仍可成功 read 两个真实 skill 后才触发熔断

### Scenario: Idempotency 缓存命中不重复计数

- **GIVEN** `skill_read_budget = 2`，task_id="t-003" 预算未消耗
- **WHEN** Agent 以相同参数 `skills.read(scope, name)` 调用三次（第二/三次命中 `agent_tools/idempotency.py` 缓存）
- **THEN** 计数器只在第一次（真实进入 handler）递增 1
- **AND** 该 task_id 仍可成功 read 另一个不同 skill 后再触发熔断（共 4 次调用、2 个不同 skill）

### Scenario: 配置缺失时使用默认阈值

- **GIVEN** `config/settings.json` 不存在或缺少 `skill_read_budget` 字段
- **WHEN** Agent 调用 `skills.read`
- **THEN** `_load_budget()` 返回默认值 `2`
- **AND** 熔断逻辑按 `budget=2` 生效

### Scenario: task_id 为空时跳过预算逻辑

- **GIVEN** `ToolContext.extra` 中 `task_id` 缺失或为空字符串（脚本直接调用、测试场景）
- **WHEN** `_read_skill_handler` 被调用
- **THEN** 跳过计数器逻辑，按原 ok:true / ok:false 路径返回
- **AND** `_BUDGET_COUNTERS` 不变

---

## MODIFIED Requirement: skills.read Tool

> 在原需求基础上，**追加**「计数生效晚于 `mem.read_skill` 返回有效 content」的实现约束。

注册的 tool `skills.read`：
- 参数：`scope: str`, `name: str`
- 返回：`{ok, data: {content: str, frontmatter: dict}}` 或 `{ok: False, error: str}`
- 实现位置：`agent_tools/skills.py`
- 自动加入 intel/content/analyst 的 `enabled_tool_patterns`
- **新增约束**：成功路径计数器递增 MUST 晚于 `mem.read_skill` 返回非 None content，确保 skill 文件不可读取时不占预算

### Scenario: 读取存在的 skill（成功且未到预算）

- **WHEN** IntelAgent 在 task_id="t-004" 首次调用 `skills.read(scope="intel", name="爆款规律分析")`
- **THEN** 返回完整 body 和 frontmatter
- **AND** `_BUDGET_COUNTERS["t-004"]` 由 0 变为 1
- **AND** audit 记录 `skills_read`，含 skill name

### Scenario: 读取不存在的 skill（保持原有行为）

- **WHEN** name 不存在
- **THEN** 返回 `{ok: False, error: "skill 'X' not found in scope 'Y'"}`
- **AND** 计数器**不**变化（与本次新增的「失败不计数」约束一致）
