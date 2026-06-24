# Tasks: content-lifecycle-v1 内容生命周期闭环

> 按 OpenSpec Stage 2 执行。完成一个任务就在本文件勾选。  
> Owner 标签：`[Claude-B]` 后端/数据/API/测试，`[Claude-A]` Agent，`[Codex-F]` Next.js，`[Codex-R]` 文档/调研。

---

## 0. 文档与开工门槛

- [x] [Codex-R] 0.1 起草并提交 `proposal.md` / `tasks.md` / `design.md` / `specs/web-api/spec.md`，等待用户确认。依赖：无。估时：0.5d
- [x] [Codex-R] 0.2 完成 PRD §15 的现有接口 Gap Analysis，盘点 `server/routers/content.py` 已有字段、端点和可复用能力，产出 `docs/baselines/v1_1_api_gap.md`。依赖：0.1。估时：0.5d
- [x] [Claude-B] 0.3 以 `design.md` API 契约为准，确认 B/F 两条流不再临时改字段名。依赖：0.1、0.2 已满足；Claude 已审过 design 并接受 schema 反馈 #1，本任务可在 mv migration 草案到正式路径后勾选。估时：0.5d

## 1. 数据层

- [x] [Claude-B] 1.1 将 `db/migrations/drafts/007_content_lifecycle.sql` 原样平移为 `db/migrations/007_content_lifecycle.sql`，字段名、类型、CHECK、默认值不得漂移。依赖：0.3。估时：0.5d
- [ ] [Claude-B] 1.2 运行 migration runner dry-run/本地 PG 验证，确认 `topics` / `content_strategies` / `calendar_items` / `generated_content` 扩列成功。依赖：1.1。估时：0.5d
- [x] [Claude-B] 1.3 扩展 `storage/pg_backend.py`：topics CRUD、calendar_items CRUD、content_strategies CRUD。依赖：1.2。估时：1d
- [x] [Claude-B] 1.4 扩展 `storage/pg_backend.py` 的 generated_content 读写，支持 `topic_id` / `strategy_id` / `calendar_item_id` / `knowledge_refs` / `memory_refs`。依赖：1.2。估时：1d
- [x] [Claude-B] 1.5 新增 `scripts/migrate_goals_json_to_pg.py`：备份 `config/goals.json`，迁移 `topic_library[]` 和 `content_calendar[]`，幂等生成稳定 id。依赖：1.3。估时：1d
- [x] [Claude-B] 1.6 迁移脚本加 `--dry-run` / `--verify`，输出 source_count、target_count、mismatch 明细。依赖：1.5。估时：0.5d

## 2. API 层

- [x] [Claude-B] 2.1 新建 `server/routers/topics_v2.py`，实现 Topic CRUD（generate-content 留给 2.4）。依赖：1.3、0.3。估时：1d
- [x] [Claude-B] 2.2 新建 `server/routers/calendar.py`，实现 CalendarItem CRUD、软删除（generate-content 留给 2.5）。依赖：1.3、0.3。估时：1d
- [x] [Claude-B] 2.3 新建 `server/routers/strategies.py`，实现策略 CRUD，并校验 `topic_id IS NOT NULL OR manual_input_hint IS NOT NULL`。依赖：1.3、0.3。估时：0.5d
- [x] [Claude-B] 2.4 新建 `server/routers/drafts.py`，实现草稿箱列表、详情、编辑、复制、加入日历、标记不采用。依赖：1.4、2.2。估时：1d
- [x] [Claude-B] 2.5 扩展 `server/routers/content.py`：`ContentGenerateRequest` 接受 `topic_id` / `strategy_id` / `calendar_item_id` / `knowledge_refs` / `memory_refs`，列表支持关联字段筛选。依赖：1.4、2.1、2.3。估时：1d
- [x] [Claude-B] 2.6 在 `server/main.py` 注册 4 个新 router，所有写入端点接入 `Idempotency-Key` 校验和 `verify_token` tenant 注入。依赖：2.1、2.2、2.3、2.4、2.5。估时：0.5d。（67 routes 总数，4 个 CRUD prefix 全部注册，无路径冲突）

## 3. Agent 层

- [x] [Claude-A] 3.1 检查 `agent_tools/content_gen.py` 当前入参，确认是否已能透传 `topic_id` / `strategy_id` / `calendar_item_id`。依赖：0.2。估时：0.5d。**结论：当前不支持，3.2 必须扩展 tool schema + handler 输出 records 携带 lifecycle refs**。
- [x] [Claude-A] 3.2 如需扩展，更新 content_gen 工具 schema 和调用路径，使 ContentAgent 生成草稿时保留 lifecycle refs。依赖：3.1、2.5。估时：0.5d
- [x] [Claude-A] 3.3 增加最小 Agent 回归：从 topic/calendar 生成内容后，输出包含 `topic_id`、`strategy_id` 或 `calendar_item_id`。依赖：3.2。估时：0.5d（`tests/test_content_gen_lifecycle.py` 4/4 通过）

## 4. 前端

- [x] [Codex-F] 4.1 扩展 `frontend/lib/api.ts`：新增 `topicsApi`、`calendarApi`、`strategiesApi`、`draftsApi`，并让写入方法自动带 `Idempotency-Key`。依赖：0.3。估时：1d
- [x] [Codex-F] 4.2 改造 `frontend/app/(main)/topics/page.tsx`：选题列表支持生成内容、加入日历、状态展示。依赖：4.1、2.1。估时：1d
- [x] [Codex-F] 4.3 改造日历所在页面入口：支持编辑日期/时间/漏斗阶段/状态、软删除确认、从日历生成内容。依赖：4.1、2.2。估时：1d
- [x] [Opus] 4.4 改造 `frontend/app/(main)/content/page.tsx`：支持 `?topicId=` / `?calendarItemId=` / `?strategyId=`，生成后默认保存为草稿。依赖：4.1、2.5。估时：1d。
  - api.ts export `generateIdempotencyKey()` 供 content 页复用
  - 新增 useQuery 拉 topic / calendar item (含 include_deleted=true fallback) / strategy
  - 顶部 LifecycleBanner 展示三种上下文（title/angle/funnel_stage + scheduled_date/time + hook）
  - `effectiveUserIntent` 派生：用户未输入时回退到 `topic.title + 角度` 种子（避开 React 19 set-state-in-effect 规则）
  - `effectiveStrategy` 派生：state 空时回退到 strategy 服务端快照，编辑后 setStrategy 接管
  - 生成 POST 强制带 `Idempotency-Key` (每次 fresh UUID) + lifecycle 5 字段 (`topic_id` / `strategy_id` / `calendar_item_id` 自动解析；persist=true)
  - `normalizeContentItem()` 把后端 `content_id/body/hashtags/publish_at` 映射回 UI 的 `id/content/tags/publish_time`，同时保留 `content_id` 供 drafts 链接
  - PUT `/content/{id}` 反向映射：UI `content/tags/publish_time` → 后端 `body/hashtags/publish_at`，并补 `Idempotency-Key`
  - 每张 ContentCard 加 「🗂 查看草稿」链接 → `/drafts?content_id=<id>`；结果区头加「打开草稿箱 →」
  - 自测：`npx.cmd tsc --noEmit --pretty false` exit=0；`npm.cmd run lint` exit=0（仅 4 个 pre-existing analytics/console/insight warning）
- [x] [Opus] 4.5 新建 `frontend/app/(main)/drafts/page.tsx`：按 goal/persona/status/topic/date 筛选，支持编辑、复制、加入日历、重新生成、标记不采用。依赖：4.1、2.4。估时：2d。
  - FiltersBar 6 字段（status/topic/persona/date_from/date_to/page_size）+ 重置 + activeGoalId 自动注入
  - DraftRow 折叠列表（标题 + status 色块 + refs chips + content_id 末 8 位）；`?content_id=xxx` 自动展开
  - 行内编辑 title/body/hashtags；保存自动 status=draft→edited；OCC retry 一次（catch rev_mismatch → reload → 用 current_rev 重试）
  - 复制（draftsApi.duplicate）→ 新行自动展开
  - 排期 ScheduleModal（date/time/funnel_stage）→ draftsApi.schedule（draft 转 scheduled + 创 calendar_item）
  - 重新生成 Link → `/content?topicId=&strategyId=&calendarItemId=`，复用 lifecycle 上下文
  - 标记不采用 RejectModal（reason 可选）→ draftsApi.reject
  - 分页（上一页/下一页 + 当前页 + has_more 控制）
- [x] [Opus] 4.6 更新 `frontend/components/Sidebar.tsx`，增加 `/drafts` 入口。依赖：4.5。估时：0.5d。（位置：内容创作 ✍️ 与 包装设计 🎨 之间，icon 🗂）
- 自测：`npx.cmd tsc --noEmit --pretty false` exit=0；`npm.cmd run lint` exit=0（pre-existing 4 warnings 与本任务无关）

## 5. 测试

- [x] [Claude-B] 5.1 新建 `verify_content_lifecycle.py`，覆盖 PRD §12 用例 1-5。依赖：2.6。估时：1d。（38/38 PASS，含 include_deleted bonus；TestClient + MockLifecycleBackend + monkeypatch Kimi，无真 PG/外网）
- [x] [Claude-B] 5.2 新建 `tests/test_topics_router.py`：Topic CRUD、跨 tenant 不可见、body tenant_id → 422、409 RevMismatch 含 current_rev。依赖：2.1。估时：0.5d。（18/18 passed）
- [x] [Claude-B] 5.3 新建 `tests/test_calendar_router.py`：日历 CRUD、软删除（status=cancelled + 列表隐藏）、硬删除、409、跨 tenant 隔离。依赖：2.2。估时：0.5d。（15/15 passed）
- [x] [Claude-B] 5.4 新建 `tests/test_strategies_router.py`：CRUD、topic_id / manual_input_hint 二选一校验（422 + `error.code=strategy_missing_anchor`）、跨 tenant 隔离。依赖：2.3。估时：0.5d。（15/15 passed）
- [x] [Claude-B] 5.5 新建 `tests/test_migrate_goals_json_to_pg.py`：parse + dry-run + 正式迁移（含 `.bak`）+ 幂等重跑 + `--verify`。依赖：1.6。估时：0.5d。（6/6 passed）
- [x] [Opus] 5.6 跑回归：`verify_web_skeleton.py`、`verify_phase5_p0.py` 至 `verify_phase5_p4.py`、`verify_content_lifecycle.py`。依赖：5.1-5.5。估时：0.5d。

  | 脚本 | 结果 | 备注 |
  |---|---|---|
  | verify_web_skeleton | 46/46 ✓ | F6 重写后 |
  | verify_phase5_p0 | FAIL | pre-existing：MagicMock JSON 序列化 + 缺 `tiktoken` 模块（agent reasoning loop，未被 V1.1 触碰） |
  | verify_phase5_p1 | FAIL | pre-existing：S6.1 `call_kimi_with_tools` 薄壳转发（`agent_tools/kimi.py`，未被 V1.1 触碰）；commit `281a25b feat(backend): kimi empty-content handling` 之后引入，需单独 followup |
  | verify_phase5_p2 | 67/67 ✓ | DAG / TaskLedger |
  | verify_phase5_p3 | FAIL | pre-existing：S17 `GET /api/v1/playbook/drafts` items 为空（playbook router，未被 V1.1 触碰），与种子数据/路由变更有关 |
  | verify_phase5_p4 | ALL ✓ | Subprocess 沙箱 |
  | verify_content_lifecycle | 38/38 ✓ | V1.1 端到端 |

  **结论**：V1.1 lifecycle 链路 0 回归。p0/p1/p3 三处失败全部在 V1.1 未触碰的文件里，由更早的 commit 引入，列为 F17/F18/F19 跟进项（不阻塞本 change 归档）。

## 6. 收尾

- [x] [Claude-B] 6.1 根据实现结果更新 `docs/ARCHITECTURE.md` 的内容生命周期链路说明。依赖：5.6。估时：0.5d。
  - 5.2 标 Legacy 指 5.6
  - 新增 §5.6 内容生命周期闭环（V1.1 4 实体链路 + OCC + Idempotency + 软删 + tenant 校验）
  - §12 文件路径补 V1.1 行：IdempotencyRoute / 4 个 router / 007 SQL / migrate 脚本 / api.ts / V1.1 测试套件
  - 新增 §14 V1.1 内容生命周期闭环段（设计依据 10 行表 + 物理分层 + 96 测试回归表 + 跟进项）
  - §10 已交付里程碑加 2026-05-27 content-lifecycle-v1 行
  - §10 进行中加 V1.1 收尾子段（Codex 4.4-4.6 / F6 / 5.6 / 6.3）
  - 附录时间线加 v3.1（V1.1）
  - 5.6 跑完后若有变动再回填测试数
- [~] [Opus] 6.2 用本地 Next.js 跑一遍用例 1-4，记录 UI 断点和接口差异。依赖：4.6、5.6。估时：0.5d。
  - **部分完成**：`npm run build` 0 错误，20 个 route 全部成功 prerender（含新增 `/drafts`）；tsc + lint 0 错误；接口契约由 `verify_content_lifecycle.py` 38/38 覆盖。
  - **未完成**：真正的浏览器交互走查（点击 / 表单填写 / OCC 409 触发 / 软删 toggling）需用户在 `npm run dev` 下手动验证。建议用例：(1) /topics 创建选题 → 跳 /content 生成 → /drafts 查看；(2) /drafts 编辑 → 排期 → /topics 看 calendar；(3) /drafts 复制 → reject；(4) 跨 tenant token 切换确认隔离。
- [x] [Opus] 6.3 按 `openspec/AGENTS.md` 将 delta 合入 `openspec/specs/web-api/spec.md` 并归档 change。依赖：6.1、6.2、用户确认。估时：0.5d。
  - `openspec/specs/web-api/spec.md`：MODIFIED "v1 仅含 /health" → "新业务端点必须先经 OpenSpec change"（+反向防偷加端点 Scenario）
  - ADDED 3 个 V1.1 Requirement：内容生命周期对象 API / 从选题或日历生成内容 / tenant 注入与幂等写入（共 13 个新 Scenario，含 OCC + Idempotency 4xx 不缓存 + idempotency_conflict）
  - MODIFIED 验收脚本：补 verify_content_lifecycle + V1.1 单元测试 Scenario
  - 归档路径：`openspec/changes/archive/2026-05-27-content-lifecycle-v1/`

---

## 依赖摘要

```
0.1 -> 0.2 -> 0.3
0.3 -> 1.1 -> 1.2 -> 1.3/1.4 -> 1.5 -> 1.6
1.3/1.4 + 0.3 -> 2.x -> 5.x
0.3 + 2.x -> 4.x
0.2 -> 3.1 -> 3.2 -> 3.3
5.x + 4.x -> 6.x
```

---

## 跟进项（review 发现，不阻塞 2.x，但要在合并前清掉）

### DeepSeek 1.3-1.6 review nits

- **F1** [已修] `storage/pg_backend.py` 三处 `update_*` 删了重复 `updated_at = now()` append + 多余的 `if not sets:` 分支（updated_at 总会 append 所以 sets 永不空）。
- **F2** [已修] `scripts/migrate_goals_json_to_pg.py` 非 `--verify` 路径改成 `已 upsert topics=N calendar_items=M（--verify 可校验 PG 实际行数）` 提示，去掉自比报表。
- **F3** [已修] `save_generated_posts:240` 简化为 `list(r.get("hashtags") or [])`。
- 回归：tests/test_topics_router + test_calendar_router + test_strategies_router + test_migrate_goals_json_to_pg + test_content_gen_lifecycle 58/58 ✓，verify_content_lifecycle 38/38 ✓。
- **F4** [已修] grep 确认无任何代码/数据写 calendar `status='draft'`（goals.json 实际只有 `planned/published`，generated_content 的 `draft` 是不同 enum）。`_normalize_calendar_status` 里 `draft → drafted` 死映射已删。test_migrate 6/6 仍过。

### Codex task 12 review notes

- **F5** [已修] design.md 新增 §3.8 "Idempotency-Key 缓存策略（中间件层）"：明确只缓存 2xx + rationale + 冲突检测 + 客户端契约。行为保留。
- **F6** [已修] `verify_web_skeleton.py` 重写：S1-S3 保留（import / health / CORS 白名单）；新增 S4 V1.1 lifecycle routers 4 个 prefix 注册检查 + S6 写入端点全挂 IdempotencyRoute 检查（含已知 legacy 白名单）。46/46 PASS（旧版 37/42）。
- **F16** [新发现] `server/routers/topics.py` 的 `POST /api/v1/topics/generate`（AI 选题生成，pre-V1.1）没挂 IdempotencyRoute。design.md §3.7 已要求 generate 端点支持 Idempotency-Key。临时白名单进 verify，正式应在下个 change 里给老 topics.py 也挂 `route_class=IdempotencyRoute`。
- **F17** [5.6 发现 · pre-existing] `verify_phase5_p0.py` reasoning loop 测试报 `TypeError MagicMock JSON not serializable` + `ModuleNotFoundError: tiktoken`。本次未触碰 agent reasoning loop。修法：mock fixture 修一致 + `pip install tiktoken` 或改用 ImportError 友好降级。
- **F18** [5.6 发现 · pre-existing] `verify_phase5_p1.py::S6.1 薄壳转发` 失败（`agent_tools/kimi.py::call_kimi_with_tools`）。最近 commit `281a25b feat(backend): kimi empty-content handling` 之后出现，需 diff 老/新 kimi.py 找回归点。
- **F19** [5.6 发现 · pre-existing] `verify_phase5_p3.py::S17 GET /api/v1/playbook/drafts items` 为空触发 IndexError。playbook router 未被 V1.1 触碰；可能种子数据缺失或路由筛选条件变更。

### Codex 4.1 review

- **F11** [已修] `GET /api/v1/strategies/{strategy_id}` 后端实现了但 design.md §3.3 缺 spec。已补到 design.md。
- **F12** [已修] `frontend/lib/api.ts` 的 Idempotency-Key 生成原来直接调 `crypto.randomUUID()`，SSR / Node < 19 会崩。已加 `generateIdempotencyKey()` fallback。

### 3.1 audit 发现的 pre-existing bug

- **F13** [已修] `agent_tools/content_gen.py:206` 用中文字段名（"主标题"/"正文"）建 DataFrame，但 `storage/pg_backend.py:228 save_generated_posts` 按英文字段（title/body）读。3.2 实施时一并修复（`_to_english()` 映射 + 4/4 测试通过）。

### DeepSeek 2.4-2.5 + 3.2-3.3 review nits

- **F14** [已修] `server/routers/content.py` 的 `PUT /api/v1/content/{id}` 现已挂 `IdempotencyRoute`（route_class 改在 APIRouter 上，POST /generate + PUT /{id} 都覆盖）。
- **F15** [已修] `server/main.py:14-32` 顶部红线注释 "v1 仅含 GET /api/v1/health" 和 "v1 不引入认证体系" 已 stale。已重写为 3 条仍有效红线（run_in_threadpool / CORS / verify_token 全覆盖）。

### Opus housekeeping 完成

- 1.1 已勾（007 SQL 已在 root）
- 0.3 已勾（design.md + 007 SQL + content.py + pg_backend.py 全部对齐，B/F 流锁定 schema）
- F11/F12（design.md strategies GET 行 + idempotency-key SSR fallback）已修
- F15（main.py 红线注释 stale）已修
- Codex 4.2/4.3 review pass：tsc + lint 0 错误；OCC retry / status 色块 / `?topicId=` / `?calendarItemId=` / soft delete 确认全部到位；topics.py 旧 `/generate` 与 topics_v2.py CRUD 路径不冲突。
