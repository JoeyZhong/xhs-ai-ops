# Spec: data-dimensions（横切数据维度治理）

> 本 spec 是**新 capability**，落盘在 `openspec/specs/data-dimensions/spec.md`。change 归档时直接 copy 覆盖（无 delta，只有 ADDED）。
> 维护原则：每个 conformance 修复（如 goal_id 4 层落地）通过后追加 Scenario 验证；新维度接入按本 spec 模板逐项添加。

---

## 概述

"横切数据维度"指**贯穿多个 capability 的数据归属字段**，例如 `tenant_id`（哪个租户的数据）、`goal_id`（属于哪个运营目标）、`persona_id`（属于哪个人设）。这些维度天然横切采集 / 存储 / 读取 / 消费四个阶段，必须全链路保留才能保证业务隔离正确性。

本 spec 治理框架包含 4 个条款（每个维度必须满足）：

1. **写入入口必须带**：数据进入系统的所有入口（采集 tool / API write endpoint / agent action）必须显式接受该维度
2. **存储 schema 必须列**：所有持久化层（PG / 本地文件）必须以独立列或文件名组成部分记录该维度
3. **读取入口必须暴露**：所有 list / query 端点必须提供按该维度过滤的参数
4. **消费方必须按维度 scope**：所有 prompt 拼装 / evidence 注入 / 决策生成必须按该维度隔离

---

## 已立维度清单

| 维度 | 应用面 | 守护状态 |
|---|---|---|
| `tenant_id` | 全部 | ✅ 历史已守护（RLS / _require_tenant / verify_token），本 spec 追认 |
| `goal_id` | 采集 / 内容生成 / 选题 / 策略 / 草稿 | 🟡 本 change 首批 conformance（仅采集层验证；其他层标 future） |
| `persona_id` | 内容生成 / 人设管理 | 🔴 spec 已立，本 change 不验证（待 future change） |
| `funnel_stage` | 选题 / 日历 / 策略 / 内容生成 prompt | 🟡 P0 已部分接入（prompt_context），本 change 不验证完整性 |

---

## ADDED Requirement: tenant_id 全链路保留

系统 SHALL 保证 `tenant_id` 在所有数据生命周期中显式保留，禁止跨 tenant 数据混读。

**应用面**：所有数据表、所有 API endpoint、所有 prompt 上下文。

### Scenario: 写入入口必须带 tenant_id
- **WHEN** 调用 `backend.save_*(tenant_id, ...)`
- **THEN** `tenant_id` 必须非空，否则抛 `TenantContextRequired`
- **AND** 通过 `_require_tenant()` 强制校验

### Scenario: 存储 schema 必须列 tenant_id
- **WHEN** 数据落到 PG
- **THEN** 表必须有 `tenant_id` 列 + RLS policy
- **AND** LocalJsonBackend 数据按 `config/<tenant>/` 或 `xhs_data/<tenant>/` 目录隔离

### Scenario: 读取入口必须暴露 tenant_id
- **WHEN** 调用任何 backend list 方法
- **THEN** `tenant_id` 是第一个位置参数（必填）
- **AND** API 层通过 `verify_token` 从 JWT 注入，不允许 query 参数覆盖

### Scenario: 消费方必须按 tenant_id scope
- **WHEN** 任何 prompt 拼装 / agent 决策
- **THEN** 数据源必须经过 `tenant_id` 过滤
- **AND** 跨 tenant 数据访问视为安全事故

---

## ADDED Requirement: goal_id 全链路保留

系统 SHALL 保证 `goal_id` 在采集相关数据的生命周期中保留，使多 goal 并存时数据可隔离过滤。

**应用面**：
- 采集层：`collected_notes`（PG）/ `spider_xhs_采集结果_*.xlsx`（LocalJson）
- 内容生成层：`generated_content.goal_id`（已有，本 change 不重复验证）
- 选题层：`topics.goal_id`（已有，本 change 不重复验证）

**本 change 首批 conformance 范围**：仅采集层（`collected_notes`），其他应用面已在 content-lifecycle-v1 中处理。

### Scenario: 写入入口必须带 goal_id（采集层）
- **WHEN** 调用 `agent_tools.search.collect_for_keyword(goal_id, ...)` 或 `collect_batch(goal_id, ...)`
- **THEN** `goal_id` 必须是入参（非可选）
- **AND** 生成的 DataFrame 每行必须含 `goal_id` 列且值与入参一致
- **AND** 调用 `backend.save_collected_data(...)` 时 df 必须含 `goal_id`

### Scenario: 存储 schema 必须列 goal_id（PG）
- **WHEN** PgBackend 写入 collected_notes
- **THEN** SQL INSERT 必须 SET goal_id 列
- **AND** goal_id 可为 NULL（兼容老数据）但新写入应非 NULL

### Scenario: 存储 schema 必须含 goal_id（LocalJson）
- **WHEN** LocalJsonBackend 写入采集 xlsx
- **THEN** 文件名格式为 `spider_xhs_采集结果_{goal_id_sanitized}_{ts}.xlsx`
- **AND** 老格式 `spider_xhs_采集结果_{ts}.xlsx` 保留兼容读取但视为 "unassigned"

### Scenario: 读取入口必须暴露 goal_id 过滤参数（backend）
- **WHEN** 调用 `backend.list_collected_data(tenant_id, ..., goal_id=...)`
- **THEN** `goal_id` 是命名参数（可选，默认 None）
- **AND** None 时返回所有 goal 数据（含 NULL）
- **AND** 非 None 时严格过滤（老 NULL 数据不返回）

### Scenario: 读取入口必须暴露 goal_id 过滤参数（API）
- **WHEN** GET `/api/v1/notes`
- **THEN** query 参数必须接受 `goal_id`
- **AND** `goal_id=default` 或空串视为不传（兼容老前端）
- **AND** 传具体值时透传给 backend

### Scenario: 消费方必须按 goal_id scope（future · 不在本 change 验证）
- **WHEN** 任何使用 collected_notes 的 prompt 拼装（如 P2 evidence pool 提取）
- **THEN** 数据查询必须带具体 goal_id
- **AND** 不允许 fallback 到全 tenant 数据

---

## ADDED Requirement: persona_id 全链路保留（spec 立但本 change 不验证）

> Future conformance — 留待后续 change 验证全链路。

系统 SHALL 保证 `persona_id` 在内容生成相关数据生命周期中保留。

**应用面**：`generated_content.persona_id` / `topics.persona_id` / personas 配置 / ContentAgent prompt 上下文

### Scenario: 写入入口必须带 persona_id（future）
（待后续 change 详细约束）

### Scenario: 消费方必须按 persona_id scope（future）
（待后续 change 详细约束）

---

## ADDED Requirement: funnel_stage 全链路保留（spec 立但本 change 不验证）

> Future conformance — P0/P1 已部分接入 prompt_context，留待 P2/P3 全面验证。

系统 SHALL 保证 `funnel_stage` 在选题 / 日历 / 策略 / 内容生成的生命周期中保留。

**应用面**：`topics.funnel_stage` / `calendar_items.funnel_stage` / `content_strategies.funnel_stage` / prompt_context.funnel_strategy_text

### Scenario: 消费方必须按 funnel_stage scope（部分已接，P2 完善）
- **WHEN** prompt_context.build_strategy_prompt_context 拼装
- **THEN** funnel_stage 必须从 topic 推导
- **AND** 注入对应的 funnel_strategy_text 段
- （已有 P0 实施；本 change 不重复验证）

---

## Future Dimensions（锚点）

后续接入新维度时，按本 spec 4 条款模板新增 `## ADDED Requirement: <dim_name> 全链路保留` 段。已规划：

| 维度 | 预计接入 change | 应用面 |
|---|---|---|
| `knowledge_id` | V1.2 知识库 | 知识检索 / RAG prompt |
| `session_id` | V1.3 Orchestrator | 对话会话 / 决策历史 |
| `strategy_id` / `topic_id` / `calendar_item_id` | content-lifecycle-v1（已实施） | 内容生命周期追踪（已 spec 在 web-api） |
| `evidence_id` | content-lifecycle-v2 P2 | evidence pool（必须带 goal_id 派生隔离） |

---

## 守护机制

`verify_web_skeleton.py` S7 节通过解析本 spec 的 ADDED Requirement 段自动构造守护断言：

- "写入入口必须带" → ast 扫 `agent_tools.search` 等 module 的函数签名
- "存储 schema 必须列" → ast 扫 backend Protocol / 文件名约定 docstring
- "读取入口必须暴露" → ast 扫 backend Protocol 签名 + FastAPI route 反射
- "消费方必须按 scope" → 暂不自动守护（成本高），人工 review

新增维度时同步更新 S7 节的"已接入维度清单"（不需要改 verify 脚本逻辑）。
