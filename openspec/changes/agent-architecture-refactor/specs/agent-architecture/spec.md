# Spec Delta: agent-architecture

> 这是一个**新建** capability（项目此前没有 Agent 架构）。所有条款都是 `## ADDED`。

## ADDED Requirement: Master Agent 是唯一调度入口

系统 SHALL 通过 `agents.master.HermesMaster` 作为所有 Agent 任务的唯一入口。
任何 Sub Agent 调用都 MUST 经由 Master 提交，禁止外部代码直接实例化 Sub Agent。

### Scenario: 直接调用 Sub Agent 被禁止
- **WHEN** 外部代码尝试 `IntelAgent().run(task)`
- **THEN** 抛出 `DirectInvocationError`
- **AND** Sub Agent 的 `__init__` 检查调用栈，仅允许来自 `HermesMaster`

### Scenario: 通过 Master 提交任务
- **WHEN** 调用 `master.submit(task)`
- **THEN** Master 返回 `task_id`
- **AND** 写入审计日志（动作=`submit`）
- **AND** 路由到正确的 Sub Agent
- **AND** 委托执行后返回 `TaskResult`

---

## ADDED Requirement: Sub Agent 必须实现统一主循环

每个 Sub Agent SHALL 继承 `agents.base.AgentBase`，并实现以下循环：
1. 启动时构造 system prompt（含 memory 冻结快照）
2. 进入 `while iteration < max_iterations and not budget.exhausted` 循环
3. 每轮调用 LLM，处理 tool_calls，调用前经 policy 检查
4. 返回 `Result(content, messages, cost, iterations)`

### Scenario: 达到 max_iterations 限制
- **WHEN** Sub Agent 循环超过 `max_iterations`（默认 20）
- **THEN** 返回 `Result.timeout()`，不再继续调用
- **AND** Master 收到后写入审计（reason=`max_iterations`）

### Scenario: token budget 耗尽
- **WHEN** 累计 tokens 超过任务 budget
- **THEN** Sub Agent 立即停止当前轮，返回 `Result.budget_exhausted()`

### Scenario: Tool policy 拒绝调用
- **WHEN** LLM 请求调用某 tool，但 policy 判定 deny
- **THEN** 抛出 `ToolPolicyViolation(tool_name, agent_name)`
- **AND** Master 捕获并返回 `TaskResult.denied()`

---

## ADDED Requirement: 三个 Sub Agent 的角色边界

系统 SHALL 提供且仅提供三个 Sub Agent：

| Agent | role | 允许的 Tool 模式 | 允许的 Memory 写入 |
|-------|------|----------------|-----------------|
| `IntelAgent` | "intel" | `search.*`, `hot_monitor.*`, `browser_fallback.*` | `intel/*.md` |
| `ContentAgent` | "content" | `kimi.*`, `content_gen.*` | 无（只读） |
| `AnalystAgent` | "analyst" | `data_analysis.*`, `kimi.summarize` | `content/playbook.md`, `analyst/*.md` |

### Scenario: 跨角色 Tool 调用被拒绝
- **WHEN** ContentAgent 尝试调用 `search.collect_notes`
- **THEN** policy 返回 deny
- **AND** ToolPolicyViolation 抛出

### Scenario: 跨角色 Memory 写入被拒绝
- **WHEN** ContentAgent 尝试写 `memory/content/playbook.md`
- **THEN** Memory 层返回 `WritePermissionDenied`

---

## ADDED Requirement: Master 必须做安全约束

Master SHALL 在每次任务执行前后执行：
1. **任务鉴权**（基于 tenant_id，跨租户拒绝）
2. **预算分配**（task.budget 不得超过 tenant 配额）
3. **Policy 注入**（向 Sub Agent 注入合适的 ToolPolicy）
4. **审计日志**（submit / route / complete / failure 各阶段）
5. **失败处理**（超时/violation/exception 各自 fallback）

### Scenario: 跨租户任务被阻断
- **WHEN** task.tenant_id ≠ 当前 session 的 tenant_id
- **THEN** Master 立即拒绝，写审计日志（reason=`cross_tenant`）

### Scenario: Sub Agent 异常崩溃
- **WHEN** Sub Agent 抛出未捕获异常
- **THEN** Master 捕获，写审计（含 traceback）
- **AND** 返回 `TaskResult.failed(error_id)`，不向上传播
