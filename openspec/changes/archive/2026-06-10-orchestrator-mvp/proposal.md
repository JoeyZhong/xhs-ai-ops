# Proposal: orchestrator-mvp · V1.3 Orchestrator 主助手 MVP

> **创建日期**：2026-06-03
> **触发**：`content-lifecycle-v2` 已归档（2026-06-03），它沉淀的 evidence / playbook / 三态 used_angles 已就位——正是 Orchestrator "主动建议"的数据源。PRD 把 Orchestrator 列为产品主入口（R12, P1），但一直只有蓝图、从未立 change（2026-06-03 项目进度盘点发现的遗漏）。
> **依据**：`docs/PRD_V1_1_SELF_LEARNING_XHS_AGENT_PLATFORM.md` §8.0（Orchestrator 主助手）+ R12；`content-lifecycle-v2` proposal「与 V1.3 Orchestrator 的契约」节。
> **版本链**：content-lifecycle-v1 → v2（数据基础，已归档）→ **orchestrator-mvp（本 change）** → V1.4 自主进化。

---

## Problem Statement

平台现在是"一堆功能页的拼接"：目标对齐、市场洞察、选题、内容创作、数据追踪、Agent Console 各自为政。运营人要自己决定先做什么、用哪个页、把上一页结果搬到下一页。底层其实已经具备"理解意图→拆任务→调度"的全部零件，但**没有面向用户的入口把它们串起来**：

1. **`Planner.plan_from_intent`（意图→DAG，LLM 驱动）已实现+已测**，但只接进了**老 Streamlit `dashboard.py:1514`**；新栈（FastAPI + Next.js）里没有任何地方调用它。
2. **新栈 `/api/v1/dag` 只接收前端传进来的现成 plan**，不会把自然语言意图转成 plan——也就是说"问答式调度"在新栈是断的。
3. **没有对话入口、没有计划解释、没有决策卡片**：用户无法用一句话（"这周帮我规划 3 篇面向深圳工厂物业的内容"）让系统拆解任务并请求确认。
4. content-lifecycle-v2 刚把 evidence / playbook / 三态 used_angles 备齐，但**没有消费者主动用它们提建议**——数据躺着没人读（[[feedback_artifacts_must_be_load_bearing]] 的隐患）。

后果：产品停留在 L0/L1（被动工具），无法到达 PRD V1.3 目标的 **L2（计划代理：系统拆任务并请求确认）**。

## Solution

按 PRD §8.0 MVP 分层，新增一个**轻量 Orchestrator service**（不是新 Agent 类），把已有 `plan_from_intent` + `HermesMaster.submit_dag` 包装成"对话→计划→确认→执行→汇总"的用户主入口。**不重写调度器、不重写 Planner**，只做包装层 + 会话状态 + 决策卡片。

- **对话→计划**：`POST /api/v1/orchestrator/converse` 收自然语言意图（带 goal_id 上下文）→ 若信息够则调 `plan_from_intent` 生成 DAG plan + 一段人话解释 + 决策卡片；若信息不足则回追问。
- **确认→执行**：`POST /api/v1/orchestrator/plan/confirm` 用户批准 plan → 复用 `HermesMaster.submit_dag` 后台执行（与现有 `/api/v1/dag` 同一执行网关），返回 `dag_id`。
- **执行汇总**：复用现有 `GET /api/v1/dag/{dag_id}` 轮询进度；Orchestrator 把 Sub Agent 的 `records` 结果转成可读建议（不是裸工具输出）。
- **决策卡片**：需要用户判断的内容（整份 plan 采纳与否、被标高风险的步骤）以结构化卡片返回，`POST /api/v1/orchestrator/decision` 采纳/拒绝。
- **会话持久化**：`orchestrator_sessions` 表（PG migration + local sidecar）存对话/plan/决策卡片/状态/dag_id，解决刷新即丢（backlog #8）。
- **前端主助手面板**：新增"主助手"页（sidebar 入口），含对话区 / 任务计划区 / 决策卡片区 / 执行结果区。

达成 PRD L2 验收：用户能用自然语言创建运营任务计划；系统能说明拆解、调了哪些 Sub Agent、要确认哪些决策；结果转成可读建议。

## 涵盖需求项

| PRD 需求 | 范围 | 优先级 | 本 change 落点 |
|---|---|---|---|
| R12 Orchestrator 主助手 MVP | 对话化包装层 + 计划展示 + 决策卡片 + 结果汇总 | P1 | `agents/orchestrator.py` service + `/api/v1/orchestrator/*` + 主助手页 |
| §8.0 调度 Sub Agent | 复用 submit_dag 执行 plan_from_intent 产出的 DAG | P1 | 不重写，包装 |
| §8.0 决策升级 | 高风险动作必须人工确认 | P1 | plan/confirm 网关 + 决策卡片 |

## Out of Scope（明确不做）

- **独立 Agent 类**：MVP 是 service 模块，不新增 `OrchestratorAgent(AgentBase)`；若 MVP 证明有效再评估（PRD §313-314）。
- **自主优化/主动追问的高级形态**：MVP 的"追问"只做一轮必填信息检查，不做多轮深度澄清（深度自主留 V1.4）。
- **反思面板**（PRD §292，"近 2 周采集过度集中…"建议）→ V1.6。
- **周度自动运营 / 定时主动发起**（L3）→ V1.6。
- **自动发布**（R13，风控原因）。
- **Streamlit 端改造**：老 dashboard 继续保留它自己的 plan_from_intent 调用，新链路只在 Next.js。
- **重写 submit_dag / plan_from_intent**：只读不改。
- **DAG 同层并行**（既有架构债，单独处理，不夹带）。

## Impact

**新增**：
- 1 个 service 模块：`agents/orchestrator.py`（意图理解 / 计划解释 / 决策卡片生成，纯包装，不碰调度内核）
- 1 组新 API：`/api/v1/orchestrator/{converse,plan/confirm,decision}`（+ 复用现有 `/api/v1/dag/{id}` 查状态）
- 1 个 PG 迁移：`db/migrations/010_orchestrator_sessions.sql`（表 `orchestrator_sessions`，RLS）
- storage 新方法：`create_session / get_session / update_session / list_sessions`（PG + local sidecar 双实现）
- 前端 1 个新页 + sidebar 入口：主助手面板（对话/计划/决策卡片/结果）
- 验收脚本：`verify_orchestrator.py`

**修改**：
- `server/main.py`：注册 orchestrator router
- `agents/policy.py`：如需，确认 Orchestrator service 调用 submit_dag 的权限路径（仍走 master_token）
- 前端 sidebar 导航 + api 客户端

**Strangler 原则**：
- 不动 Streamlit、不动 submit_dag/plan_from_intent 内核
- 复用现有 DAG 执行 + 进度轮询，不另起执行器

## 横切维度影响审查

- [x] **tenant_id**：已保留——`orchestrator_sessions` 按 tenant RLS 隔离；submit_dag 已 tenant-aware。
- [x] **goal_id**：**新增依赖**——converse 接收 goal_id，把当前目标的 overall_strategy / funnel / playbook / 三态 used_angles 作为计划上下文（这是 Orchestrator 价值核心）。session 表带 goal_id 列。
- [x] **persona_id**：不涉及——MVP 不做人设切换；下游 content 任务已自行处理 persona。
- [x] **funnel_stage**：不涉及——Orchestrator 不新增 funnel 依赖；plan 内的 content 任务沿用既有 funnel 锚点。

## Risk

| 风险 | 缓解 |
|---|---|
| plan_from_intent 输出不稳定（LLM 拆解跑偏） | 已有 schema 校验 + max_retries=2 + topo_sort 验环（planner.py 内建）；MVP plan 节点 ≤6（既有约束） |
| 用户绕过确认直接执行高风险动作 | 执行**只能**经 plan/confirm 网关；submit_dag 仍过 HermesMaster/ToolPolicy/AuditLogger；MVP 无任何自动执行路径 |
| 会话状态膨胀 / 并发写冲突 | session 用 OCC rev（沿用项目 OCC 模式）；local sidecar 用既有 RLock；messages 只存摘要不存全量工具输出 |
| LLM 成本（每轮对话 1-2 次调用：澄清 + 计划解释） | MVP 限制：converse 每次最多 1 次 plan_from_intent + 1 次解释/卡片调用；复用 idempotency |
| 决策卡片做成展示性摆设（不 load-bearing） | MVP 决策卡片必须可操作：plan/confirm 真的触发执行，decision 采纳/拒绝真的改 session 状态并影响后续 |
| 范围蔓延（Orchestrator 容易越做越大） | 严格按 Out of Scope；MVP 只到 L2（拆任务+请求确认），不碰 L3 自主 |

## 需用户拍板的开放决策（审阅时确认）

1. **会话持久化**：MVP 就上 PG 表 `orchestrator_sessions`（推荐，和项目一致、解决刷新丢失），还是先纯内存/sidecar 最小化？
2. **"追问"深度**：MVP 只做一轮必填信息检查（goal 是否选、意图是否可拆），还是要多轮澄清对话？（推荐一轮，深度留 V1.4）
3. **决策卡片范围**：MVP 只做"整份 plan 采纳/拒绝 + 高风险步骤标记"，还是要做 PRD §290 那种细粒度卡片（"是否采纳这 5 个关键词""是否更新人设口吻"）？（推荐前者，细粒度留后续）
4. **是否分阶段**：本 change 估 4-6 天，建议拆 P1 后端 service+API / P2 会话持久化+决策卡片 / P3 前端面板三个可独立合并的 phase（同 clv2 模式）。

---

## 与下游版本的契约

- Orchestrator service 的接口（converse/confirm/decision）是 V1.4「自主进化」「主动追问」、V1.6「周度自动运营」的复用基座——它们将以"定时/事件触发 converse + 自动确认低风险 plan"的形态扩展，**不另起入口**。
- 决策卡片数据结构（本 change 定义）是后续"反思面板"卡片的同构扩展。
