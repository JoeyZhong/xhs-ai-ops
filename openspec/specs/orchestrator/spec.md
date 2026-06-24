# orchestrator 能力规格

> 已交付能力的当前真相（契约/红线）。来源 change：`orchestrator-coordinator`（2026-06-10 归档，
> 取代 `orchestrator-mvp` 对本 capability 的薄壳定义）。
> 事件/会话契约细节见 `docs/handoff/orchestrator-coordinator-contracts.md`
> （§A 会话 schema / §B SSE 事件 / §C 内核接口 / §D session view）。

## Requirement: 动态协调（非固定流水线）
主 Agent SHALL 按用户意图与每一步结果**动态决定**调用哪些子 agent、调用顺序与次数，
而非对每条消息固定执行同一组子 agent。

### Scenario: 纯问答意图不调子 agent
- **WHEN** 用户消息只是询问/咨询（无需采集/分析/生成）
- **THEN** 主 Agent 直接给出答复并收尾，不调用任何子 agent

### Scenario: 单一意图只调对应子 agent
- **WHEN** 用户要"看看上周哪篇数据最好"（仅需分析）
- **THEN** 主 Agent 只调用 analyst 子 agent，不调用 intel/content

### Scenario: 复合意图编排多步
- **WHEN** 用户要"规划并写一批面向工厂物业的内容"
- **THEN** 主 Agent 依次调用 intel → analyst → content，后一步基于前一步结果

## Requirement: 多轮澄清与可恢复
主 Agent SHALL 在信息不足时暂停并向用户追问；会话状态 SHALL 持久化，
使刷新或跨 HTTP 轮次后能从暂停点恢复继续。

### Scenario: 信息不足主动追问
- **WHEN** 意图缺关键信息（如未指定目标）
- **THEN** 主 Agent 暂停回路、`status=awaiting_user`，向用户提出具体问题

### Scenario: 答复后无重跑恢复
- **WHEN** 用户对追问作出答复（带同一 session_id）
- **THEN** 主 Agent 用持久化历史 + 新答复继续；**已执行的子 agent 结果不重跑**

### Scenario: 切目标即开新对话
- **WHEN** 续接同一 session 却传入与会话现有 goal_id 不同的 goal_id
- **THEN** 主 Agent 丢弃旧 messages/trace，以新目标从零开始，避免上一目标上下文渗入

## Requirement: 结果解读为可读建议
主 Agent SHALL 把子 agent 的原始产出收敛成面向运营人的可读建议，而非直接返回原始工具输出。

### Scenario: 收尾给建议而非原始输出
- **WHEN** 子 agent 全部执行完
- **THEN** 主 Agent 输出综合建议（含依据），而不是把原始结果直接抛给用户

## Requirement: 流式呈现协调过程与最终答复
系统 SHALL 通过 SSE 实时推送协调过程（思考、调用子 agent、结果、追问、最终建议）；
协调步骤事件 SHALL 持久化进 `session.trace` 以支持断连/刷新恢复；
最终答复 SHALL 以 token 级增量（`final_delta`）实时流出，不再用 `finish` 工具兜底。

### Scenario: 协调步骤边做边推且可恢复
- **WHEN** 主 Agent 在协调回路中推进
- **THEN** 每步以 SSE 事件推送（thinking / subagent_start / subagent_result / awaiting_user / decision_card / final 等）并落 `session.trace`
- **AND** 客户端断连或刷新后 `GET /session/{id}` 能返回已发生的 trace 与当前 pending，恢复全过程

### Scenario: 最终答复 token 级流式
- **WHEN** 主 Agent 产出纯文本最终建议
- **THEN** 内容以 `final_delta` 事件逐 token 推送、前端累积渲染，随后由一个 `final` 事件给出权威全文定稿
- **AND** `final_delta` 不入 trace（传输层信号）；刷新/恢复由 trace 里的 `final` 整段还原

### Scenario: done 唯一终止符
- **WHEN** 一轮协调结束（终态/暂停/错误/迭代或预算上限）
- **THEN** 流以**恰一个 done 事件**收尾，`done.status` 反映本轮落点（done/cancelled/awaiting_user/awaiting_decision）；前端只在 done 收流

## Requirement: 协调执行的安全与防失控
所有子 agent 执行 SHALL 经 HermesMaster / master_token / ToolPolicy / AuditLogger；
主协调回路 SHALL 受迭代上限与 token 预算约束。

### Scenario: 子 agent 不绕过安全闸
- **WHEN** 主 Agent 调用任一子 agent
- **THEN** 该执行经 HermesMaster 实例化（校验 master_token）并受其 ToolPolicy 约束、写审计

### Scenario: 防失控
- **WHEN** 协调回路达到迭代上限或耗尽 token 预算
- **THEN** 主 Agent 优雅收尾（给出已有结论），不无限循环或无限 spawn 子 agent

### Scenario: archetype 白名单
- **WHEN** 主 Agent 请求 `run_subagent` 的 archetype 不在 {intel, analyst, content}
- **THEN** 该调用被拒绝（V1 不支持自拟新角色）
