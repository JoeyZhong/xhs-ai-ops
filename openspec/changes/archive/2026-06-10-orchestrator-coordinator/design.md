# Design: orchestrator-coordinator · V1 真·协调 Agent

> 详细设计。配合 `proposal.md` 与总纲 `docs/superpowers/specs/2026-06-04-orchestrator-autonomous-agent-design.md`。

## 1. 核心机制：主 Agent = agent，子 agent = 它的工具

复用 `AgentBase`(`agents/base.py`)的主循环(LLM → tool_calls → ToolPolicy → registry 调用 →
收结果 → 再循环，受 `max_iterations` + token budget 约束 + GOAP scratch_pad 推理)。
Orchestrator 是一个**新 agent**(`agents/orchestrator_agent.py`)，其可调"工具"是一组**元工具**：

| 元工具 | 入参 | 行为 |
|---|---|---|
| `run_subagent` | `archetype`(intel/analyst/content)、`task`(prompt) | 经 `HermesMaster.submit(AgentTask(...))` 跑一个子 worker，**同步**拿 `TaskResult`，结果回灌主回路供观察 |
| `ask_user` | `question` | **暂停**回路，向用户提问(信息不足时) |
| `raise_decision_card` | `kind`、`title`、`detail` | **暂停**回路，出决策卡等用户确认(闸门二预留；V1 仅计划确认场景用) |
| `finish` | `summary` | 结束，输出"人话建议 + 依据" |

主 Agent 在循环里**自主决定**调哪个/调几次/什么顺序——"只 `ask_user`"、"只 `run_subagent(analyst)`"、
"intel→analyst→content 串联" 都是它按意图 + 上一步结果现决的，**不再固定三件套**。

### V1a：run_subagent 的接口缝(不做全量重构)

```python
# agents/orchestrator_agent.py 内
def _tool_run_subagent(archetype: str, task: str) -> dict:
    # V1a：archetype → 现有 AgentTask.type，直接复用 HermesMaster.submit
    # V2 再把这里换成 spec→通用 worker；调用方(主回路)接口不变
    result = self._master.submit(AgentTask(type=archetype, prompt=task,
                                           tenant_id=self.tenant_id, goal_id=self.goal_id))
    return {"ok": result.ok, "agent": archetype,
            "content": result.content, "error": result.error}
```

`archetype` 当前限 {intel, analyst, content}(经 ToolPolicy 白名单校验)。
新角色/技能特化留 V2——但**主回路对 `run_subagent` 的调用形态(archetype+task)V2 不变**，缝铺好即可平滑接。

## 2. 可暂停可恢复的回路(多轮澄清的根)

主回路是同步的，而"暂停问用户、跨 HTTP 轮次再续"不能阻塞服务端协程。采用
**无状态重放(re-entrant replay)**，而非挂起活协程：

- 每一步(用户消息、主 agent 的 tool_calls、子 agent 结果作为 tool 结果、追问、卡片)
  **全部持久化**到 `orchestrator_sessions`(P2 表)。
- `ask_user`/`raise_decision_card` 被调用时 → 抛一个内部 `PauseSignal`，
  主循环捕获 → 把 `pending`(待答问题/待确认卡)写入 session，状态置 `awaiting_user`/`awaiting_decision`，
  SSE 发 `awaiting` 事件后关流。
- 用户答复(走 `/converse` 带 `session_id`)→ **用持久化的对话历史 + 新答复重建 messages**
  → 喂回一个新的循环实例继续。**已跑过的子 agent 结果作为 tool 结果留在历史里，不重跑。**

这与主流"无状态 agent + 持久化对话"一致：恢复 = 重建
`[system, user, assistant(tool_calls), tool(results)…, user(新答复)]` → 继续循环。

### session schema 扩展(在 P2 基础上)

`orchestrator_sessions` 现有 `messages / proposed_plan / decision_cards / dag_id`。本期增/改：
- `status`：枚举扩为 `thinking | awaiting_user | awaiting_decision | done | cancelled`(替代旧 gathering/planned/dispatched)。
- `trace`(新)：协调步骤数组 `[{step, kind, ...}]`，供前端渲染 + 恢复。
- `pending`(新)：当前待用户输入项(question 或 card)。
- `messages`：承载 LLM 对话历史(含 tool_calls / tool results)，供重放恢复。

## 3. 流式呈现(SSE)

新增 `POST /api/v1/orchestrator/converse/stream`(`EventSourceResponse`，对齐 `collect_stream` 写法)：
- 在线程池跑主回路(`loop.run_in_executor`)，通过 `asyncio.Queue` 把每步 emit 给 SSE 消费者；
  `stop_event` 处理断连(对齐 `server/stream_utils.py`)。
- 事件类型：`thinking`(scratch_pad 摘要)、`subagent_start`、`subagent_result`、
  `decision_card`、`awaiting_user`、`final`(收尾建议)、`error`、`done`。
- 每个事件**同时落 session.trace**，保证刷新/断连后 `GET /session/{id}` 能恢复全过程。
- 旧的非流式 `/converse`(P1)保留作回退/测试入口；前端默认走 stream。

> 阻塞调用(LLM、子 agent 执行)全在 worker 线程内，不阻塞事件循环(沿用既有纪律)。

## 4. 结果解读

`finish(summary)` 由主 agent 综合全过程产出人话建议(PRD §355)。
单个子 agent 结果也在回灌时被主 agent 在 scratch_pad 里消化，下一步决策基于"读懂的结论"而非原始文本。

## 5. 安全与防失控

- 子 agent 执行**全程经 HermesMaster/master_token/ToolPolicy/AuditLogger/沙箱**——主 agent 再动态也越不过。
- 主回路受 `AgentBase` 的 `max_iterations` + token budget 约束(防 spawn 风暴 / 死循环)。
- `run_subagent` 的 `archetype` 经白名单校验(非法 → 拒绝)。
- 闸门二(不可逆对外动作执行前确认)：V1 仅预留 `raise_decision_card` 通道，真实对外写工具随 F1。

## 6. 前端(assistant 面板改造)

- 执行结果区从"轮询静态 DAG"改为**消费 SSE 协调 trace**：气泡列表渲染
  思考/调用子 agent/结果/最终建议；**增量逐字 + 打字指示 + 自动滚动**(参考 Ant Design X 体感，
  用现有 base-ui/tailwind/shadcn 实现，**不引入 antd**)。
- `ask_user`/决策卡 → 在流里渲染为可交互气泡；用户答复 → 再发起一轮 stream(带 session_id)续跑。
- 进页 localStorage 续接 session_id，`GET /session/{id}` 恢复 trace(沿用 P3)。

## 7. 复用 vs 替换

| 资产 | 处置 |
|---|---|
| `agents/base.py` AgentBase 主循环 | **复用**作主 Agent 的回路底座 |
| `HermesMaster.submit` / ToolPolicy / Audit | **复用**作子 agent 执行闸 |
| P2 `orchestrator_sessions` + 双 backend | **复用**，扩 `trace`/`pending`/status |
| P3 面板壳 + `orchestratorApi` + sidebar | **复用**，执行区改 SSE 流式 |
| 决策卡片 | **复用**作 `raise_decision_card` 渲染 |
| `agents/orchestrator.py` 一次性状态机 | **替换**为协调回路(逻辑迁到 `orchestrator_agent.py`；旧 `/converse` 薄封装新内核) |
| `planner.plan_from_intent` / `submit_dag` | **保留为构件**，不再是唯一路径(主 agent 可按需调，也可不调) |

## 8. 开放设计点(实现时可微调，供 review)

1. **重放成本**：每轮恢复都重建对话历史喂 LLM；子 agent 结果已缓存为 tool 结果不重跑，但历史会变长 →
   复用 `agents/compression.py` 免疫压缩控制上下文膨胀。
2. **trace 与 messages 的关系**：messages 是 LLM 上下文(给恢复用)，trace 是给前端的人类可读步骤；
   两者从同一循环产出，分别落库。
3. **archetype 命名**：V1 沿用 intel/analyst/content(= 现有 type)，避免引入新词造成 V1↔V2 迁移负担。
