# Spec Delta: web-api

> 本 delta 为 `content-lifecycle-v1` 新增内容生命周期业务端点，并修改早期 "v1 仅 /health" 红线的适用边界。  
> 依据：PRD §7 R1-R5、§8.1-§8.3、§12 用例 1-5。

---

## MODIFIED Requirement: v1 仅含 /health（红线）

系统 SHALL 继续要求所有新增业务端点必须先经过独立 OpenSpec change 提案；`content-lifecycle-v1` 被批准后，内容生命周期相关 `/api/v1/*` 业务端点 MAY 加入 FastAPI app。

### Scenario: 业务端点必须有 change
- **WHEN** 新增 `/api/v1/topics`、`/api/v1/calendar`、`/api/v1/strategies`、`/api/v1/drafts` 或扩展 `/api/v1/content`
- **THEN** 这些端点 MUST 属于 `content-lifecycle-v1` 的 proposal / design / tasks 范围
- **AND** 不得夹带 Streamlit、知识库连接器、Orchestrator 或自动发布端点

---

## ADDED Requirement: 内容生命周期对象 API

系统 SHALL 提供 Topic、ContentStrategy、CalendarItem、ContentDraft 的 HTTP API，使每篇生成内容可追踪 `topic_id`、`strategy_id`、`calendar_item_id`、`knowledge_refs` 和 `memory_refs`。

### Scenario: Topic CRUD
- **WHEN** 客户端创建、读取、更新或归档选题
- **THEN** 系统通过 `/api/v1/topics` 完成操作
- **AND** 响应对象包含 `topic_id`、`status`、`source`、`source_refs`、`rev`

### Scenario: Strategy 关联选题
- **WHEN** 客户端创建内容策略
- **THEN** 请求 MUST 提供 `topic_id` 或 `manual_input_hint`
- **AND** 响应对象包含 `strategy_id`、`topic_id`、`evidence_refs`、`memory_refs`、`knowledge_refs`

### Scenario: Calendar soft delete
- **WHEN** 客户端删除未发布日历项
- **THEN** 默认执行软删除
- **AND** `calendar_items.status` 变为 `cancelled`
- **AND** `deleted_at` 被写入
- **AND** 默认列表不再展示该条目

### Scenario: Draft box
- **WHEN** 客户端查询草稿箱
- **THEN** `/api/v1/drafts` SHALL 支持按 goal、persona、status、topic、日期筛选
- **AND** 草稿可编辑、复制、加入日历、标记不采用

---

## ADDED Requirement: 从选题或日历生成内容

系统 SHALL 支持从 Topic 或 CalendarItem 一键生成内容，生成结果默认进入草稿箱，并保留生命周期关联字段。

### Scenario: 从选题生成内容
- **GIVEN** 当前 tenant 下存在一个 `topics.topic_id`
- **WHEN** 客户端请求 `POST /api/v1/topics/{topic_id}/generate-content`
- **THEN** 系统生成 `generated_content` 行
- **AND** 每条草稿包含相同 `topic_id`
- **AND** 若生成或选择了策略，草稿包含 `strategy_id`

### Scenario: 从日历项生成内容
- **GIVEN** 当前 tenant 下存在未生成内容的 `calendar_items.calendar_item_id`
- **WHEN** 客户端请求 `POST /api/v1/calendar/{calendar_item_id}/generate-content`
- **THEN** 系统生成草稿
- **AND** 草稿包含 `calendar_item_id`
- **AND** 日历项状态更新为 `drafted`

### Scenario: 生成内容暂存
- **WHEN** 客户端请求 `POST /api/v1/content/generate` 且 `persist=true`
- **THEN** 生成结果进入草稿箱
- **AND** 不自动加入日历
- **AND** 刷新页面后仍可通过 `/api/v1/drafts` 查询和编辑

---

## ADDED Requirement: tenant 注入与幂等写入

系统 SHALL 对内容生命周期所有业务写入端点执行 tenant 注入、RLS 隔离和幂等控制。

### Scenario: tenant_id 不允许由客户端传入
- **WHEN** 请求 body 或 query 包含 `tenant_id`
- **THEN** 返回 422
- **AND** 实际 tenant 只能来自 `verify_token` 返回的 `AuthContext.tenant_id`

### Scenario: 写入端点缺少 Idempotency-Key
- **WHEN** 客户端请求 POST、PUT 或 DELETE 写入端点但没有 `Idempotency-Key`
- **THEN** 返回 428

### Scenario: OCC rev 冲突
- **WHEN** 客户端用过期 `rev` 更新 Topic、ContentStrategy 或 CalendarItem
- **THEN** 返回 409
- **AND** 响应说明需要重新拉取最新对象

---

## ADDED Requirement: V1.1 验收脚本

系统 SHALL 提供 `verify_content_lifecycle.py`，覆盖 PRD §12 用例 1-5。

### Scenario: 验收通过
- **WHEN** 运行 `python verify_content_lifecycle.py`
- **THEN** 日历基础管理、选题进入内容创作、日历项继续生成内容、生成内容暂存、知识引用字段回显全部通过
- **AND** 退出码为 0
