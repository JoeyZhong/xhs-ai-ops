# Spec Delta: web-api（content-lifecycle-v2）

> 本 delta 在 v1 已有 lifecycle 端点基础上，新增 packaging / intel evidence / analytics 三组端点，并修改 `/content/strategy`、`/content/generate` 的 prompt 拼装契约。
> 依据：PRD §R3、R4、R7、R10；本 change 的 `proposal.md` 和 `design.md`。

---

## ADDED Requirement: 包装规则可视化编辑 API

系统 SHALL 提供 `/api/v1/packaging/rules` GET/PUT，使运营人可在 UI 编辑 `memory/_universal/packaging_rules.md`，无需直接编辑文件。

### Scenario: GET 读取当前规则
- **WHEN** 客户端请求 `GET /api/v1/packaging/rules`
- **THEN** 响应 `{ rules: string, updated_at: iso8601 }`
- **AND** `rules` 字段是文件原始 markdown 内容
- **AND** 响应 HTTP 200

### Scenario: PUT 保存新规则
- **WHEN** 客户端请求 `PUT /api/v1/packaging/rules` body `{ rules: string }`，带合法 `Idempotency-Key`
- **THEN** 系统原子写入（写 `.tmp` → rename）
- **AND** mtime 变化触发 `agent_tools.packaging_rules.load_packaging_rules()` 缓存失效
- **AND** 响应 HTTP 200 + `{ rules, updated_at }`

### Scenario: PUT 缺必填字段被拒
- **WHEN** body `rules` 不含 "五大爆文标题公式" 或不含 "CES"
- **THEN** 响应 HTTP 422 + `ErrorCode.PACKAGING_INVALID`
- **AND** 文件不被修改

### Scenario: PUT 缺 Idempotency-Key 被拒
- **WHEN** PUT 请求未带 `Idempotency-Key` header
- **THEN** 响应 HTTP 428 + `ErrorCode.MISSING_IDEMPOTENCY_KEY`

---

## ADDED Requirement: 爆款样本提取 API

系统 SHALL 提供 `/api/v1/intel/evidence/extract` POST，从高 CES 的 `collected_notes` 中调用 LLM 抽取 `{angle, hook, key_insight}`，并写入 `content_evidence` 表。

### Scenario: 批量提取
- **WHEN** 客户端请求 `POST /api/v1/intel/evidence/extract` body `{ ces_threshold: 250, batch_size: 10 }`
- **THEN** 系统查询满足 `ces_score > 250 AND note_id NOT IN content_evidence` 的 notes
- **AND** 按 batch_size 分批调用 Kimi
- **AND** 每条 Kimi 结果通过枚举校验（angle ∈ 5 公式）后 upsert 到 `content_evidence`
- **AND** 响应 `{ extracted_count, skipped_count, errors: list[string] }`

### Scenario: 部分失败容错
- **WHEN** 某批 Kimi 返回解析失败
- **THEN** 该批降级为逐条调用
- **AND** 单条失败不阻断后续 batch
- **AND** 失败 note_id 记入 `errors[]`，不写入 evidence

### Scenario: 幂等重跑跳过已提取
- **WHEN** 客户端重复调用 extract
- **THEN** 已存在 `(tenant_id, source_note_id)` 的 evidence 不会被重新调用 Kimi
- **AND** `skipped_count` 反映跳过的数量

### Scenario: list 查询
- **WHEN** 客户端请求 `GET /api/v1/intel/evidence?angle=反直觉型&page=1&page_size=20`
- **THEN** 响应分页 envelope `{ items, total, page, page_size, has_more }`
- **AND** items 按 `ces_score DESC` 排序

---

## ADDED Requirement: 发布数据回填 API

系统 SHALL 提供 `/api/v1/analytics/performance` POST，让运营人录入发布后的互动数据，自动计算 CES 并写回 `generated_content.meta.ces_score` 和 `goals.used_angles[].last_ces`。

### Scenario: 录入数据
- **WHEN** 客户端 `POST /api/v1/analytics/performance` body `{ content_id, likes, comments_count, shares, collects, follows }`，带 `Idempotency-Key`
- **THEN** 系统计算 CES = likes×1 + collects×1 + comments_count×4 + shares×4 + follows×8
- **AND** 更新 `generated_content.meta.ces_score`
- **AND** 更新对应 `goal.used_angles[angle].last_ces` 和 `evidence_count += 1`
- **AND** 响应 `{ content_id, ces_score, angle_status }`

### Scenario: content_id 不存在
- **WHEN** 录入 content_id 在 `generated_content` 中不存在
- **THEN** 响应 HTTP 404 + `ErrorCode.NOT_FOUND`

### Scenario: 同 content_id 二次录入
- **WHEN** 同 content_id 第二次录入
- **THEN** CES 是最新值（覆盖），不是累加
- **AND** `used_angles[angle].last_ces` 反映最新 CES

---

## MODIFIED Requirement: 内容生命周期对象 API（v1 → v2 扩展）

> v1 已建立 Topic / Strategy / Draft / Calendar CRUD 和 `topic_id`/`strategy_id`/`calendar_item_id`/`knowledge_refs`/`memory_refs` 追踪；v2 扩 prompt 拼装契约。

系统 SHALL 在 `/api/v1/content/strategy` 和 `/api/v1/content/generate` 的 LLM prompt 中**强制注入** 5 个上下文段，按下列顺序拼装：

1. 基础信息（brand_position / target_audience / 关键词 / 用户意图）— v0 已有
2. `core_block`（来自 `goal.overall_strategy.core_message`）— P0 已加
3. `funnel_block`（来自 `goal.overall_strategy.content_funnel[stage]`）— P0 已加
4. `packaging_rules`（来自 `memory/_universal/packaging_rules.md`）— P0 已加
5. **`evidence_block`（来自 `content_evidence` top-3，按 funnel 优先 + angle 次之 + ces_score DESC）** — v2 新增
6. **`playbook_summary`（来自 `memory/<tenant>/content/playbook.md` 的 `<!-- analyst-auto: v2 -->` 块前 500 字符）** — v2 新增

### Scenario: prompt 含 evidence 段
- **WHEN** 客户端 `POST /api/v1/content/strategy` body 含合法 `topic_id`，且 `content_evidence` 表有 ≥ 1 条匹配 `funnel_stage` 或 `angle` 的记录
- **THEN** LLM prompt 包含 `── 同 funnel/同 angle 爆款样本 ──` 段
- **AND** 该段含 1-3 条 evidence 的 `angle`、`hook`、`key_insight`

### Scenario: prompt 含 playbook 段
- **WHEN** `memory/<tenant>/content/playbook.md` 含 `<!-- analyst-auto: v2 -->` 块
- **THEN** LLM prompt 包含 `── 已验证爆款规律（playbook）──` 段
- **AND** 该段引用 playbook 前 500 字符（超长截断加 "..."）

### Scenario: evidence 缺失时不放置空段
- **WHEN** `content_evidence` 无任何匹配记录
- **THEN** prompt 不含 `── 同 funnel/同 angle 爆款样本 ──` 段
- **AND** 其他 5 段（v0/P0 既有）仍按契约拼装

### Scenario: playbook 缺失时不放置空段
- **WHEN** `memory/<tenant>/content/playbook.md` 不存在或无 `<!-- analyst-auto: v2 -->` 块
- **THEN** prompt 不含 `── 已验证爆款规律 ──` 段

---

## MODIFIED Requirement: goals.used_angles schema（v1 → v2 三态升级）

> v1 中 `used_angles: list[str]`；v2 升级为 `list[dict]` 结构，承载三态学习信号。

系统 SHALL 在 `goals.data->'used_angles'` 中存储以下结构：

```json
[
  { "angle": "反直觉型",
    "status": "validated_hit",   // 或 "sunk" / "unknown"
    "evidence_count": 5,
    "last_ces": 320 }
]
```

### Scenario: 老 goals 自动迁移
- **WHEN** PG migration 009 执行
- **THEN** 所有现有 goal 的 `used_angles: ["X", "Y"]` 自动 wrap 成 `[{angle: "X", status: "unknown", evidence_count: 0, last_ces: null}, {...}]`
- **AND** 原字符串数组保留在 `used_angles_legacy` 字段 1 个 minor 版本，便于 rollback

### Scenario: AnalystEvaluator 写入三态
- **WHEN** AnalystEvaluator 周报跑完
- **THEN** 满足 `min_samples ≥ 3 AND 平均 CES > 200` 的 angle 标 `validated_hit`
- **AND** 满足 `min_samples ≥ 3 AND 平均 CES < 80` 的 angle 标 `sunk`
- **AND** 其余保持 `unknown`

### Scenario: 前端不展示 unknown
- **WHEN** 前端 `/content` 内容卡片渲染 angle
- **THEN** `validated_hit` 显示 "✅ 已验证爆款（CES {n}）"
- **AND** `sunk` 显示 "❌ 沉底（CES {n}）"
- **AND** `unknown` 不显示标签（避免噪音）

---

## ADDED Requirement: prompt 拼装契约的对外可观测性

系统 SHOULD（非强制）支持 `?debug_prompt=true` 查询参数，使开发者可在 `/content/strategy` 和 `/content/generate` 响应里看到拼装后的完整 prompt，便于运营人调试为什么"包装公式没生效"。

### Scenario: debug 模式
- **WHEN** 请求 `POST /api/v1/content/strategy?debug_prompt=true`
- **THEN** 响应除常规字段外，包含 `debug.prompt: string` 字段
- **AND** 该字段包含完整拼装后的 LLM 输入

### Scenario: 非 debug 模式不暴露
- **WHEN** 请求未带 `debug_prompt=true`
- **THEN** 响应不含 `debug` 字段
