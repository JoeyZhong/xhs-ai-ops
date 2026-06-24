# Spec Delta: orchestrator（orchestrator-mvp）

> 新建 capability `orchestrator`。所有条款 `## ADDED`。
> Orchestrator 是 `Planner.plan_from_intent` + `HermesMaster.submit_dag` 之上的对话化包装层（L2 计划代理），不重写调度内核。
> 依据：PRD §8.0；本 change 的 proposal.md / design.md。

---

## ADDED Requirement: 对话式意图理解与计划生成

系统 SHALL 提供 `POST /api/v1/orchestrator/converse`，接收自然语言意图，理解后生成可执行 DAG 计划或主动追问缺失信息。

### Scenario: 信息充分则生成计划
- **WHEN** 客户端 `POST /api/v1/orchestrator/converse` body `{ message, goal_id }`，且 goal_id 可定位、意图非空
- **THEN** 系统调用 `Planner.plan_from_intent`（注入该 goal 的 overall_strategy / funnel / playbook 摘要作上下文）
- **AND** 响应 `{ session_id, status: "planned", reply, proposed_plan, decision_cards }`
- **AND** `proposed_plan` 是已 topo_sort 校验的 `[{id, type, prompt, blocked_by}]`
- **AND** `reply` 是对计划的人话解释（非裸 JSON）

### Scenario: 信息不足则追问
- **WHEN** converse 的 message 未带可定位的 goal_id，或意图为空/不可拆
- **THEN** 响应 `{ session_id, status: "gathering", reply, missing }`
- **AND** `missing` 列出缺失项（如 `["goal_id"]`）
- **AND** 不生成 plan、不执行任何任务

### Scenario: LLM 不可达时降级
- **WHEN** 计划解释的 LLM 调用失败
- **THEN** 系统降级为规则化解释（按 task type 模板），仍返回 plan + 卡片
- **AND** 不抛 500

---

## ADDED Requirement: 计划确认网关（人工确认才执行）

系统 SHALL 要求所有实际执行经 `POST /api/v1/orchestrator/plan/confirm` 人工确认，且执行一律复用 `HermesMaster.submit_dag`。

### Scenario: 批准后执行
- **WHEN** 客户端 `POST /api/v1/orchestrator/plan/confirm` body `{ session_id, plan_card_decision: "approve" }`，带 Idempotency-Key
- **THEN** 系统用 session 的 `proposed_plan` 调用 `HermesMaster.submit_dag`（经 ToolPolicy + AuditLogger）
- **AND** 响应 `{ session_id, status: "dispatched", dag_id }`
- **AND** 执行进度通过既有 `GET /api/v1/dag/{dag_id}` 查询（不新增进度端点）

### Scenario: 拒绝则不执行
- **WHEN** `plan_card_decision: "reject"`
- **THEN** 响应 `{ status: "cancelled" }`
- **AND** 不调用 submit_dag

### Scenario: 无确认不得执行
- **WHEN** 仅调用过 converse（status=planned）但未 confirm
- **THEN** 系统 MUST NOT 执行任何 Sub Agent 任务
- **AND** MVP 不存在任何自动执行 / 绕过确认的路径

---

## ADDED Requirement: 决策卡片

系统 SHALL 在生成计划时产出至少 1 个可操作决策卡片；卡片必须 load-bearing（采纳/拒绝真的改变系统行为）。

### Scenario: 计划级卡片
- **WHEN** 生成了 proposed_plan
- **THEN** `decision_cards` 至少含 1 张 `kind: "plan_approval"` 卡
- **AND** 卡片含 `card_id / title / detail / options:["approve","reject"] / status:"pending"`

### Scenario: 高风险步骤卡片
- **WHEN** plan 中某节点 prompt 命中高风险关键词（发布 / 对外 / 删除类）
- **THEN** 为该步骤生成 `kind: "high_risk_step"` 卡片
- **AND** 该步骤在用户批准前不会执行

### Scenario: 决策更新状态
- **WHEN** 客户端 `POST /api/v1/orchestrator/decision` body `{ session_id, card_id, decision }`
- **THEN** 对应卡片 `status` 更新为 `approved` / `rejected`
- **AND** 响应返回更新后的 `decision_cards`

---

## ADDED Requirement: 会话持久化与恢复

系统 SHALL 持久化 orchestrator 会话，使刷新/重进可恢复对话、计划、卡片和执行关联。

### Scenario: 续接会话
- **WHEN** converse 带已存在的 `session_id`
- **THEN** 在该 session 上追加 message 并续接状态机
- **AND** 不新建 session

### Scenario: 刷新恢复
- **WHEN** 客户端 `GET /api/v1/orchestrator/session/{session_id}`
- **THEN** 返回 `{ status, goal_id, messages, proposed_plan, decision_cards, dag_id }`
- **AND** 前端可据此恢复四区面板

### Scenario: 租户隔离
- **WHEN** 用 tenant A 的 JWT 访问 tenant B 的 session_id
- **THEN** 不返回该 session（RLS / tenant 过滤）

### Scenario: tenant_id 不可由客户端传入
- **WHEN** 请求 body 含 tenant_id
- **THEN** 返回 422
- **AND** 实际 tenant 取自 `verify_token` 的 AuthContext

---

## ADDED Requirement: 执行结果转可读建议

系统 SHALL 把 Sub Agent 的执行结果（records）转成可读建议，而非返回裸工具输出。

### Scenario: 结果汇总
- **WHEN** dispatched 的 DAG 执行完毕
- **THEN** Orchestrator 把各节点 `TaskResult.records` 归并为按 task type 的可读摘要（如"生成了 N 篇草稿，建议去内容创作查看"）
- **AND** 不向用户暴露 tool_call 入参日志
