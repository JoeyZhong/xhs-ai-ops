# Proposal: orchestrator-coordinator · V1 真·协调 Agent

## 背景

`orchestrator-mvp`(已实现 P1-P3，未归档)按 PRD §313 保守 MVP 做成了**薄 service**：
每收到一句话就固定 ①查 goal_id → ②调一次 `plan_from_intent` → ③`submit_dag` 一把梭。
用户 2026-06-04 反馈：**这跟现有 Agent Console DAG 多步没本质区别**——流程僵化、
不按意图选子 agent、不解读结果、不真懂意图，**不是一个"主 Agent"**。

总纲见 `docs/superpowers/specs/2026-06-04-orchestrator-autonomous-agent-design.md`
(北极星受控 L3、五条架构原则、三层目录安全模型、分版路线图)。本 change = 该路线图的 **V1**。

## 目标(本 change 范围)

把 orchestrator 的"脑"从"一次性规划 + 发完不管"换成**真协调回路**，达到 PRD L2(计划代理)：

1. **动态协调**：主 Agent 自己决定这次只答 / 只采 / 只写 / 还是多步串联，**按上一步结果再定下一步**。
2. **多轮澄清**：信息不足时暂停回路、主动追问，用户答了再续(可恢复)。
3. **结果解读**：把子 agent 原始产出收敛成"人话建议 + 依据"。
4. **流式呈现**：SSE 实时推送协调过程(参考 Ant Design X 流式体感，用现有 base-ui/tailwind 实现)。

## 核心设计(一句话)

**主 Agent 就是一个 agent，它的"工具"是子 agent。** 复用 `AgentBase` 的
LLM→tool_calls→策略→调用→收结果→再循环 主循环 + token/迭代预算，把"调用子 agent / 追问用户 /
出决策卡 / 收尾解读"注册成元工具 —— 迭代协调回路即白捡。

## 关键取舍(已与用户拍板 2026-06-04)

- **V1a(轻)**：只铺 `run_subagent(archetype, task)` 接口缝，内部映射到现有 intel/analyst/content 类；
  **不做** agent=spec/通用 worker 全量重构(→ V2)。
- **SSE 流式**：每步落 session 以便刷新恢复。
- **参考 antd X 体感但不引入 antd**(现栈 base-ui+shadcn+tailwind，避免设计系统冲突)。

## 不在本 change 范围(留后续)

- 主 agent 自拟新角色 / 通用 worker 全量重构(V2)
- 完整三层目录分层 + F1 评论区获客的对外写工具(随 F1)
- 自学习建 skill/tool(V2)
- 前端"聊天升主入口、页面降后台"的正式重组(V3，本期只把聊天体验做扎实)

## 与现有资产的关系

- **复用**：`orchestrator-mvp` 的 P2 会话持久化(migration 010 + 双 backend + OCC)、
  P3 面板壳 + `orchestratorApi` + sidebar 入口、决策卡片、`HermesMaster.submit` / `AgentBase`。
- **替换**：`agents/orchestrator.py` 的"一次性规划 + 发完不管"状态机 → 真协调回路。
- `orchestrator-mvp` 不再单独归档/验收(浏览器验收作废)，其代码作为 V1 的脚手架被本 change 收编。

## 影响维度(cross-cutting)

- `tenant_id`：协调回路与 session 全程按 tenant 隔离(沿用 P2)。
- `goal_id`：意图理解 + 子 agent 任务都带 goal 上下文(沿用现有 methodology 注入)。
- 安全：所有子 agent 执行仍经 HermesMaster/master_token/ToolPolicy/AuditLogger；主回路受迭代+token 预算约束(防失控)。
