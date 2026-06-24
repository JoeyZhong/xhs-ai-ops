# Tasks: orchestrator-mvp · V1.3 Orchestrator 主助手 MVP

3 个 phase，每个独立可合并 PR，phase 末用户验收。
> **前置**：content-lifecycle-v2 已归档（evidence / playbook / 三态 used_angles 就位）；`plan_from_intent` + `submit_dag` + `/api/v1/dag` 已就绪。
> **预计周期**：P1 1.5-2 天 / P2 1 天 / P3 1.5-2 天，总计 4-6 天。
> **启动门槛**：用户确认 proposal 的 4 个开放决策后再开 P1。

---

## P0 · 开放决策确认（proposal §"需用户拍板"）

- [x] D1 会话持久化：PG 表 `orchestrator_sessions`（**用户 2026-06-03「直接开干」取推荐**；P1 先进程内、P2 落 PG+sidecar）
- [x] D2 澄清深度：一轮必填检查（取推荐）
- [x] D3 决策卡片范围：plan_approval + high_risk_step（取推荐）
- [x] D4 按 P1/P2/P3 分阶段（取推荐）

---

## P1 · Orchestrator service + 对话/确认 API（1.5-2 天）

> **目标**：一句话意图 → 出 plan + 解释 + 决策卡片 → 确认 → 复用 submit_dag 执行。会话先用内存/最小 sidecar，PG 持久化留 P2。

### P1.1 service 层

- [x] P1.1.1 新建 `agents/orchestrator.py`，纯 service 函数，不调 Tool / 不跑 Agent
- [x] P1.1.2 `understand_intent` → 必填检查（goal 可定位 + 意图非空）；缺 → gathering+missing
- [x] P1.1.3 `build_plan` → 复用 `planner.plan_from_intent`，注入 goal overall_strategy/funnel/三态 used_angles 摘要作 methodology
- [x] P1.1.4 `explain_plan` → 1 次 LLM 调用生成人话 reply；LLM 不可达降级规则化。决策卡片由 `make_decision_cards` 产
- [x] P1.1.5 dispatch 逻辑在 router（`plan_nodes` + `mark_dispatched`）复用 `HermesMaster.submit_dag`
- [x] P1.1.6 高风险规则 `_is_high_risk`：命中发布/上线/对外/删除类 → high_risk_step 卡

### P1.2 API 路由

- [x] P1.2.1 新建 `server/routers/orchestrator.py`，注册到 `server/main.py`，挂 `IdempotencyRoute` + `verify_token`
- [x] P1.2.2 `POST /converse`（无/有 session_id；返回 gathering 或 planned+plan+cards）
- [x] P1.2.3 `POST /plan/confirm`（approve→submit_dag→dag_id；reject→cancelled）+ `POST /decision` + `GET /session/{id}`（提前做了，本属 P2）
- [x] P1.2.4 tenant_id 取自 AuthContext（session 按 tenant 隔离）
- [x] P1.2.5 阻塞调用（LLM / submit_dag）全部 `run_in_threadpool`

### P1.3 测试

- [x] P1.3.1 单测/集成：缺 goal→gathering；converse→planned（≥3步+≥1卡片）；confirm 调 submit_dag(stub master)；reject→cancelled；跨租户隔离 → **7/7 通过** `tests/test_orchestrator.py`
- [x] P1.3.2 集成 converse→planned→confirm→dispatched+dag_id（含在上）
- [x] P1.3.3 `verify_web_skeleton.py` 加 orchestrator 到 IdempotencyRoute 覆盖 → **54/54 通过**

### P1.4 Commit
- [x] P1.4.1 `feat(orchestrator): intent→plan→confirm service + API (reuse planner/submit_dag)`

---

## P2 · 会话持久化 + 决策卡片闭环（1 天）

> **目标**：会话落库（刷新可恢复，解 backlog #8）；决策卡片采纳/拒绝真的改状态。

### P2.1 存储

- [x] P2.1.1 PG migration `db/migrations/010_orchestrator_sessions.sql`（表 + RLS + FORCE RLS + OCC rev；reviewed-only，PG 不可达随 runner 执行）
- [x] P2.1.2 `storage/base.py` Protocol 增 `create_session / get_session / update_session / list_sessions`
- [x] P2.1.3 `storage/pg_backend.py` 实现（get_rls_cursor + WHERE tenant_id 双保险 + `rev=rev+1` OCC）
- [x] P2.1.4 `storage/local_json.py` 实现（sidecar `lifecycle_orchestrator_sessions.json`，`_SIDECAR_LOCK` 串行读改写）
- [x] P2.1.5 测试：跨租户隔离 / OCC rev 冲突 / 续接 session → **7/7** `tests/test_orchestrator_sessions.py`

### P2.2 接入 + 决策端点

- [x] P2.2.1 converse / confirm / decision 全改为经 backend 读写 session（gathering→planned→dispatched/cancelled），OCC rev 透传
- [x] P2.2.2 `POST /decision`（card_id + approve/reject → 落库更新卡片 status）
- [x] P2.2.3 `GET /session/{id}`（刷新恢复，dispatched+dag_id 可恢复）
- [x] P2.2.4 测试：刷新恢复 session（gathering/planned/dispatched）；decision 改卡片状态 → 含在 `tests/test_orchestrator.py` **8/8**

### P2.3 Commit
- [x] P2.3.1 `feat(orchestrator): persist sessions + decision cards`

---

## P3 · 主助手前端面板（1.5-2 天）

> **目标**：sidebar 新增「主助手」入口，四区面板，复用 DAG 进度轮询。

### P3.1 页面

- [x] P3.1.1 新建 `frontend/app/(main)/assistant/page.tsx`，sidebar 加入口（菜单名「主助手」🧭）
- [x] P3.1.2 对话区：输入意图 + goal 选择器（显式选优先，回退全局 active goal）→ 调 /converse；展示 reply + missing 追问
- [x] P3.1.3 任务计划区：展示 proposed_plan（序号 + type 图标 + id + 依赖 + prompt）
- [x] P3.1.4 决策卡片区：渲染 cards（plan_approval / high_risk_step），approve/reject 调 confirm；卡片状态徽章
- [x] P3.1.5 执行结果区：confirm 后用 dag_id 轮询既有 `/api/v1/dag/{id}`，复用 console 的 records 渲染 + 终态下一步 CTA
- [x] P3.1.6 进页用 session_id 恢复（localStorage `spider-xhs-orch-session`，会话不存在自动清理）；`orchestratorApi` 加进 `lib/api.ts`

### P3.2 验收

- [x] P3.2.1 `verify_orchestrator.py`：端到端对话→计划→确认→(stub 执行)→刷新恢复→决策→拒绝→跨租户 → **20/20 通过**
- [x] P3.2.1b `npx tsc --noEmit` + `eslint` 全绿；`next build` 成功（`/assistant` 路由已生成）
- [ ] P3.2.2 浏览器手工验收（用户自点）：一句话意图 → 看到 ≥3 步计划 + ≥1 决策卡片 → 确认 → 看执行结果（PRD L2 验收标准）— **待用户验收**

### P3.3 Commit
- [x] P3.3.1 `feat(assistant): orchestrator main-assistant panel`

---

## 总验收（Phase Gate）

- [x] G.1 pytest 全绿（不含 PG 专用）→ **366 passed, 55 skipped(PG)**
- [x] G.2 `verify_web_skeleton.py` 路由数 ≥ 基线 + orchestrator 新增端点 → **54/54**
- [x] G.3 端到端：自然语言意图 → 计划（≥3 步）+ 决策卡片（≥1）→ 确认 → submit_dag 执行 → 结果转可读建议 → `verify_orchestrator.py` **20/20**（API 层全覆盖 PRD §351-356）
- [x] G.4 刷新恢复：dispatched 后刷新，session + dag_id + 进度可恢复（verify S4 + test_dispatched_state_recovers_after_refresh）
- [x] G.5 文档：`docs/USER_GUIDE.md` 增「§10 主助手」章节 + 能力地图；`docs/STATUS.md` V1.3 行更新为「P1-P3 全绿，待验收+归档」
- [ ] G.6 归档（**待 P3.2.2 浏览器验收后执行**）：spec deltas 合入 `openspec/specs/orchestrator/spec.md`（新建 capability）；目录移 `archive/<date>-orchestrator-mvp/`
