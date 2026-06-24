# lead-intent-radar 能力规格

> 已交付能力的当前真相（V1，2026-06-17 交付并验收）。
> 范围 = 小红书单源 + 人工首触；可横向复用引擎（换垂直 = 换 goal 关键词/画像 + 信源）。

## Requirement: 意图信号采集（可替换采集器）
系统 SHALL 通过注册到 Tool Registry 的 `collect.xhs_intent` 工具，按关键词采集"主动公开发布需求"的信号，并归一为统一 `Signal` 结构（`source`/`source_url`/`signal_key`/`author`/`posted_at`/`post_text`）。采集器实现 SHALL 可替换（env `XHS_COLLECTOR`：`fixture` 默认离线 / `sidecar` 走 HTTP 真实采集），主代码与 agent 不随采集器变更而改。

### Scenario: 关键词采集
- **WHEN** 调用 `collect.xhs_intent(keywords, limit)`
- **THEN** 返回归一后的 `Signal[]`，每条带稳定去重键 `signal_key`
- **AND** sidecar 模式不可达 / 非 200 时降级返回 `[]`，不崩溃

### Scenario: 采集层与内容采集隔离
- **WHEN** 信号落库
- **THEN** 持久化到独立 `leads` 实体（PG `leads` 表 / 本地 `lifecycle_leads.json`），**不得**写入 `collected_notes`

## Requirement: 意图资格判定与噪声过滤
系统 SHALL 通过 `intent.classify` 对每条 `Signal` 判定 `{is_intent, match_score, trigger_type, judge_reason, qualified}`；仅 `qualified`（求购意图且匹配度 ≥ 阈值）的信号方可成为 lead，其余丢弃。

### Scenario: 噪声过滤
- **WHEN** 判定为非求购（同行 / 广告 / 无关）或匹配度低于阈值
- **THEN** 该信号丢弃，不进入收件箱、不落库

### Scenario: 命中成 lead
- **WHEN** 判定 `qualified=true`
- **THEN** 生成 lead 入库，进入 `/admin/leads` 收件箱

## Requirement: 维度隔离
每条 lead SHALL 携带 `tenant_id` / `goal_id` / `persona_id`。local 模式按 `goal_id` 隔离（不同业务线互不可见）；`tenant_id` 一并保存（=`default`）以便将来切 PG 时启用 RLS。lead 生命周期状态为 lead 专属字段（`detected→qualified→drafted→pending→touched→skipped`），**不复用**内容 `funnel_stage`。

### Scenario: 业务线隔离
- **WHEN** 收件箱按某 goal 列线索
- **THEN** 仅返回该 `goal_id` 的 lead，其它业务线（如售卖机 goal）数据不可见

## Requirement: 人工闸门首触
系统 SHALL 为合格 lead 生成首触草稿（按 goal 关联 persona 的口吻），并要求人工确认后方可触达。V1 中系统 SHALL NOT 自动发送——"通过" = 复制草稿 + 打开原帖，由运营人在平台外手动发送。

### Scenario: 一键通过
- **WHEN** 运营人在 `/admin/leads` 点"复制草稿 + 打开原帖"
- **THEN** 草稿入剪贴板、原帖新窗打开、lead 标记 `touched`
- **AND** 系统不触发任何自动发送

### Scenario: 引流词 / 雷同红线
- **WHEN** 首触草稿含微信 / 电话 / "加我" / "私信"等引流词，或与历史首触相似度过高
- **THEN** 校验器置 `sendable=false`，禁止进入可发送状态

## Requirement: 转化度量
系统 SHALL 暴露 `/api/v1/leads/stats`（今日合格线索 / 待处理 / 本周成交）；北极星为**本周沟通机会数**（lead `outcome ∈ {replied, converted}`），收件箱"已触达"可一键回填 outcome。

### Scenario: 北极星回填
- **WHEN** 运营人对已触达 lead 标记"有回复"或"成交"
- **THEN** lead `outcome` 更新，stats 的本周沟通机会 / 成交实时计数

## Requirement: 反封号约束
首触触达 SHALL 保持低量、人节奏、无引流词；XHS 渠道闸门最严，不做批量自动留言。周期采集频率 SHALL 符合频控（≥2h 间隔、每日 ≤3 次）。

### Scenario: 周期扫描频控
- **WHEN** scheduler 周期触发雷达扫描
- **THEN** 每日不超过 3 次、间隔 ≥2h（cron 09:30/14:30/20:30）
- **AND** 仅扫描 `goal.lead_radar_enabled=true` 的 goal

## Requirement: 读写引擎商用合规（上线前置门）
投入真实商用获客前，系统 SHALL 使用允许商用的读 / 写引擎。

### Scenario: 上线前切换闸门
- **WHEN** 准备对真实潜客触达 / 进入生产获客
- **THEN** 读写引擎必须为 ReaJason/xhs(MIT) 或已购 MediaCrawlerPro
- **AND** 免费版 MediaCrawler 仅限本地技术验证，不得用于生产商用获客
