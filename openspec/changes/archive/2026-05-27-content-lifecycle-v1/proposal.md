# Proposal: content-lifecycle-v1 内容生命周期闭环

> **创建日期**：2026-05-26  
> **触发**：PRD V1.1 "内容生产闭环"落地  
> **依据**：`docs/PRD_V1_1_SELF_LEARNING_XHS_AGENT_PLATFORM.md` §1、§3.3、§7、§8.A、§8.1、§8.2、§8.3、§9 V1.1、§12 用例 1-5、§14 第 1 项；计划文档 §2.3、§4

---

## Problem Statement

Spider_XHS V1 已有市场采集、选题、内容生成、数据追踪和 Agent Console，但 PRD §1 明确指出当前体验仍是多个能力页拼接，核心断点集中在两类问题：

1. **内容生命周期断点**：日历内容无法删除，选题加入日历后无法生成内容，生成文章无法暂存。本质是缺少统一的 `Topic -> Draft -> Calendar -> Publish -> Performance` 状态机。
2. **策略链路割裂**：选题和内容创作割裂，内容策略与选题策划关系不清。本质是缺少"本次内容任务"的中心对象，策略、选题、文章没有可追踪关系。

现有实现里，`config/goals.json` 的 `topic_library[]` 和 `content_calendar[]` 只是轻量数组，缺少独立 id、状态、来源、版本与跨对象关联。`server/routers/content.py` 已有草稿雏形，但未把 `topic_id`、`strategy_id`、`calendar_item_id`、`knowledge_refs`、`memory_refs` 串起来，无法支撑 PRD §8.1-§8.3 的闭环。

## Solution

以 `content-lifecycle-v1` 为单独 OpenSpec change，先定 API 契约，再并行推进后端和 Next.js 前端。

本 change 使用 `db/migrations/drafts/007_content_lifecycle.sql` 作为唯一 schema 来源：

- 新增 `topics`
- 新增 `content_strategies`
- 新增 `calendar_items`
- 扩展 `generated_content`

在 API 层新增内容生命周期路由组，并扩展现有内容生成端点，使选题、策略、草稿、日历项之间有可追踪 id 关系。前端只改 Next.js 工作台，不对 Streamlit 做补丁。

## 涵盖需求项

| PRD 需求 | 范围 | 优先级 | 本 change 落点 |
|---|---|---|---|
| R1 内容日历条目删除/编辑/状态管理 | 日历 CRUD、软删除、状态编辑 | P0 | `calendar_items` + `/api/v1/calendar/*` |
| R2 从选题库/日历一键生成内容 | 从 `topic_id` 或 `calendar_item_id` 进入内容生成 | P0 | `/topics/{id}/generate-content`、`/calendar/{id}/generate-content`、`content.py` 扩参 |
| R3 内容草稿箱：暂存、编辑、复制、加入日历 | 草稿箱页面与草稿动作 | P0 | `generated_content` 扩列 + `/api/v1/drafts/*` + content action endpoints |
| R4 统一内容生命周期模型 | Topic/Strategy/Draft/Calendar 显式状态机 | P0 | 007 migration + Pydantic 状态校验 |
| R5 内容策略与选题关联 | 策略必须关联 topic 或 manual input | P1 | `content_strategies` + `/api/v1/strategies/*` |

## 目标与成功指标

对齐 PRD §3.3 V1.1 阶段指标：

- 从日历选题一键生成内容成功率 ≥ 95%。
- 生成内容可暂存、可编辑、可再次排期，100% 支持。

支撑 PRD §3.1 的追溯目标：

- 每篇内容能追溯到 `topic_id`、`strategy_id`、`calendar_item_id`，并保留 `knowledge_refs` / `memory_refs` 字段。
- V1.1 收尾时可通过 `verify_content_lifecycle.py` 覆盖 PRD §12 用例 1-5。

## 范围

### In Scope

- 落地 `db/migrations/007_content_lifecycle.sql`，内容必须从 `db/migrations/drafts/007_content_lifecycle.sql` 平移，字段名、类型、CHECK、默认值不漂移。
- `storage/pg_backend.py` 增加 topics/calendar/strategies CRUD 和 generated_content 新字段读写。
- 新增或重构 FastAPI 路由：
  - `server/routers/topics_v2.py`：Topic CRUD + 从选题生成内容。
  - `server/routers/calendar.py`：CalendarItem CRUD + 从日历项生成内容。
  - `server/routers/strategies.py`：ContentStrategy CRUD。
  - `server/routers/drafts.py`：草稿箱查询、详情、编辑入口。
  - `server/routers/content.py`：扩展生成参数、筛选参数、复制、排期、拒绝等动作。
- 更新 Pydantic 模型，负责状态枚举、`rev` OCC、`tenant_id` 禁止由 body/query 覆盖、`Idempotency-Key` 头校验。
- Next.js 前端改造：
  - `frontend/app/(main)/topics/page.tsx`
  - `frontend/app/(main)/content/page.tsx`
  - `frontend/app/(main)/goals/[id]/page.tsx` 或现有日历展示入口
  - 新建 `frontend/app/(main)/drafts/page.tsx`
  - 扩展 `frontend/lib/api.ts`
- 一次性迁移脚本 `scripts/migrate_goals_json_to_pg.py`，把 legacy `goal.topic_library[]` 和 `goal.content_calendar[]` 落到 PG。
- 测试与验收：`verify_content_lifecycle.py`、topics/calendar/strategies router 单测、legacy 迁移测试。

### Out of Scope

- 不改 `dashboard.py`，不做 Streamlit 端补丁。
- 不做 Next.js 之外的前端工作，包括 `_legacy_prototype`、独立 Vite/Astro 页面或 Streamlit 迁移。
- 不实现完整知识库 CRUD、外部知识连接器或 NotebookLM/Obsidian 集成；本 change 只承载 `knowledge_refs` 字段和草稿引用展示接口。
- 不实现 memory governance、Orchestrator 主助手、关键词自主拓展或自动发布。
- 不修改 `db/migrations/drafts/007_content_lifecycle.sql` 的字段命名；如有 schema 建议只记录在 `design.md` 的 "Schema 反馈"。

## Impact

### 路由

- 新增 `GET/POST/PUT/DELETE /api/v1/topics`
- 新增 `POST /api/v1/topics/{topic_id}/generate-content`
- 新增 `GET/POST/PUT/DELETE /api/v1/calendar`
- 新增 `POST /api/v1/calendar/{calendar_item_id}/generate-content`
- 新增 `GET/POST/PUT /api/v1/strategies`
- 新增 `GET/PUT/POST /api/v1/drafts/*`
- 扩展 `GET/POST /api/v1/content/*`

所有业务端点继续通过 `server/auth.py::verify_token` 注入 `AuthContext(tenant_id, is_admin)`；`tenant_id` 不允许出现在请求体中。

### 表

- 新增：`topics`
- 新增：`content_strategies`
- 新增：`calendar_items`
- 扩展：`generated_content`

### Pydantic 模型

- `TopicCreateRequest` / `TopicUpdateRequest` / `TopicResponse`
- `CalendarItemCreateRequest` / `CalendarItemUpdateRequest` / `CalendarItemResponse`
- `ContentStrategyCreateRequest` / `ContentStrategyUpdateRequest` / `ContentStrategyResponse`
- `ContentGenerateRequest` 扩展 `topic_id` / `strategy_id` / `calendar_item_id` / `knowledge_refs` / `memory_refs`
- `ContentItem` 扩展 `content_id`、关联字段和 `scheduled` 状态
- `DraftListQuery` / `DraftUpdateRequest` / `DraftActionResponse`

## Risk

| 风险 | 缓解 |
|---|---|
| legacy `goals.json` 与新 PG 表双源不一致 | 迁移脚本先 `.bak`，迁移后数组置空保留字段，PG 成为新对象源真相 |
| 草稿/日历状态混乱 | 状态机写入 design.md；Pydantic 层做状态值和状态跃迁校验 |
| 前后端并行时契约漂移 | design.md 先固定 HTTP API JSON Schema，Codex-F 和 Claude-B 各自按契约实现 |
| 重复点击生成造成重复草稿 | 所有写入端点要求 `Idempotency-Key`，复用 idempotency 中间件 |
| 多租户串数据 | `tenant_id` 只从 JWT 注入，storage 层继续 `WHERE tenant_id = %s` + RLS |

## Next Step

用户审阅本 proposal / tasks / design / spec delta 后，再进入 Stage 2 实现。实现阶段按 `tasks.md` 勾选，不在本提案阶段修改 `.py` / `.sql` / `.json` / `.tsx`。
