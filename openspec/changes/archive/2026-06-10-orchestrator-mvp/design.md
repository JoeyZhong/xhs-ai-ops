# Design: orchestrator-mvp

> 配合 `proposal.md`。本文件定架构决策、数据模型、API 契约、组件边界。
> 设计原则：**包装而非重写**——Orchestrator 是 `plan_from_intent` + `submit_dag` 之上的薄编排层。

---

## 1 · 组件边界（谁负责什么）

```
用户 ──对话──> [主助手面板 (Next.js)]
                      │  POST /orchestrator/converse|plan/confirm|decision
                      ▼
              [server/routers/orchestrator.py]   ← 薄 HTTP 层：鉴权/幂等/参数
                      │  调用
                      ▼
              [agents/orchestrator.py  (service)]  ← 本 change 核心，纯包装
                ├─ understand_intent()   一轮必填信息检查 → 需澄清就回问题
                ├─ build_plan()          复用 planner.plan_from_intent()
                ├─ explain_plan()        LLM 把 plan 转人话 + 生成决策卡片
                ├─ confirm_and_dispatch() 复用 HermesMaster.submit_dag()  ← 唯一执行入口
                └─ summarize_results()   把 Sub Agent records 转可读建议
                      │  读写
                      ▼
              [storage: orchestrator_sessions]    ← 会话/plan/卡片/状态/dag_id
```

**边界铁律**：
- `agents/orchestrator.py` **不直接调 Tool、不直接跑 Agent**；执行一律经 `HermesMaster.submit_dag`（保留 ToolPolicy + AuditLogger + master_token）。
- 不新增 `OrchestratorAgent(AgentBase)`。它是无状态 service 函数集 + 一个 session 存储，不进 Agent 注册表、不受 ToolPolicy 白名单约束（因为它不持有工具，只编排）。
- `planner.py` / `master.py` / `task_ledger.py` **零改动**。

## 2 · 数据模型

### orchestrator_sessions（PG migration 010 + local sidecar）

| 列 | 类型 | 说明 |
|----|------|------|
| `session_id` | text PK | `os-<uuid8>` |
| `tenant_id` | uuid NOT NULL | RLS 隔离 |
| `goal_id` | text | 计划上下文锚点（可空，但 converse 强烈建议带） |
| `status` | text | `gathering`（待补信息）/ `planned`（已出 plan 待确认）/ `dispatched`（已执行）/ `done` / `cancelled` |
| `messages` | jsonb | `[{role: user|orchestrator, text, ts}]`，只存摘要不存全量工具输出 |
| `proposed_plan` | jsonb | plan_from_intent 产出的 `[{id,type,prompt,blocked_by}]`（待确认态） |
| `decision_cards` | jsonb | `[{card_id, kind, title, detail, options, status}]` |
| `dag_id` | text | confirm 后 submit_dag 返回，关联执行进度 |
| `rev` | int | OCC 乐观锁（沿用项目模式） |
| `created_at`/`updated_at` | timestamptz | |

- RLS policy 参考 `collected_notes`（tenant 隔离）。
- local sidecar：`config/<tenant>/orchestrator_sessions.json`，沿用 clv2 evidence sidecar 的 RLock 串行写。

### decision_card 结构（MVP）

```json
{
  "card_id": "dc-1",
  "kind": "plan_approval | high_risk_step",
  "title": "采纳这份 3 步计划？",
  "detail": "1. 采集深圳工厂物业相关笔记 → 2. 分析高 CES 共性 → 3. 生成 3 篇草稿",
  "options": ["approve", "reject"],
  "status": "pending | approved | rejected"
}
```

MVP 只产两类卡片：
- `plan_approval`：整份 plan 一张卡（满足 PRD "≥1 决策卡片"）。
- `high_risk_step`：plan 里命中高风险规则的步骤各一张（MVP 高风险规则=节点 prompt 命中发布/对外/删除类关键词；可空）。

## 3 · API 契约（全部 JWT + IdempotencyRoute）

### POST /api/v1/orchestrator/converse
```
body: { message: str, goal_id?: str, session_id?: str }
→ 200:
  // 信息不足
  { session_id, status: "gathering", reply: "你想针对哪个目标？还需要…", missing: ["goal_id"] }
  // 已出计划
  { session_id, status: "planned", reply: "<人话解释>", proposed_plan: [...], decision_cards: [...] }
```
- 无 session_id → 新建 session；有 → 续接。
- `understand_intent`：MVP 必填检查 = goal_id 是否可定位 + 意图是否非空可拆；缺则回 `gathering` + missing。
- 够 → `plan_from_intent(message, methodology=goal 上下文摘要)` → `explain_plan` 生成 reply + cards → 存 session（status=planned）。

### POST /api/v1/orchestrator/plan/confirm
```
body: { session_id: str, plan_card_decision: "approve" | "reject" }
→ approve: { session_id, status: "dispatched", dag_id }   // 复用 submit_dag
→ reject:  { session_id, status: "cancelled" }
```
- approve → `HermesMaster(tenant).submit_dag(proposed_plan, ...)` 后台跑 → 写 dag_id + status=dispatched。
- 执行进度：前端轮询既有 `GET /api/v1/dag/{dag_id}`（不新增端点）。

### POST /api/v1/orchestrator/decision
```
body: { session_id, card_id, decision: "approve" | "reject" }
→ { session_id, decision_cards: [...更新后...] }
```
- MVP：更新对应卡片 status；`plan_approval` 卡的 approve 等价于走 confirm（二选一入口，避免重复，前端用 confirm）。

### GET /api/v1/orchestrator/session/{session_id}
```
→ { session_id, status, goal_id, messages, proposed_plan, decision_cards, dag_id }
```
- 解决刷新丢失（backlog #8）：前端进页用 session_id 恢复。

## 4 · 结果汇总（summarize_results）

执行完后前端轮询 `/dag/{id}` 已能拿到各节点 `TaskResult`。`agent-architecture-refactor` 已把 content 类任务的 result 替换为真实 `records`（非 tool_call 日志，见 ARCHITECTURE）。Orchestrator 的 `summarize_results` 在此之上：把 records 归并成"这次产出了 N 篇草稿/1 份分析，建议下一步去 X"。MVP 可先做规则化汇总（按 task type 模板），不强制 LLM。

## 5 · 关键决策记录

| 决策 | 选择 | 理由 |
|------|------|------|
| Orchestrator 形态 | service 模块，非 AgentBase 子类 | PRD §313；MVP 不持有工具，只编排，避免引入新 Agent 生命周期 |
| 执行路径 | 复用 submit_dag，不另起 | 保留安全网关/审计/权限；不重复造执行器 |
| 状态查询 | 复用 GET /dag/{id} | DAG 进度基建已完备，避免重复端点 |
| 会话存储 | PG 表 + local sidecar 双实现 | 与项目存储抽象一致；解决刷新丢失 |
| 澄清深度 | 一轮必填检查 | MVP 控范围；多轮澄清留 V1.4 |
| 决策卡片 | plan_approval + high_risk_step 两类 | 满足 PRD "≥1 卡片"且 load-bearing；细粒度留后续 |

## 6 · 测试策略

- 单测：`understand_intent` 缺 goal → gathering；`build_plan` mock planner provider → 校验 plan 结构；`confirm` → 校验调 submit_dag（mock master）；session OCC 冲突。
- 集成（TestClient + mock call_kimi + tmp backend，沿用 clv2 verify_g3 模式）：converse(够信息) → planned + cards → confirm → dispatched + dag_id → GET session 恢复。
- 验收脚本 `verify_orchestrator.py`：端到端走一遍对话→计划→确认→（mock 执行）→汇总。
