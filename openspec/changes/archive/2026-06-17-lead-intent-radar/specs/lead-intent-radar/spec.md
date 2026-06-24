# lead-intent-radar 能力规格（change delta）

> 新 capability。归档时整体合入 `openspec/specs/lead-intent-radar/spec.md`。
> 本轮 V1 范围 = 小红书单源 + 人工首触。

## ADDED Requirement: 多源意图信号采集
系统 SHALL 通过注册到 Tool Registry 的 `collect.*` 工具，从指定信源持续采集"主动公开发布需求"的信号，并归一为统一 `Signal` 结构。V1 仅实现 `collect.xhs_intent`（小红书）。

### Scenario: 小红书求购帖采集
- **WHEN** scheduler 周期触发 `collect.xhs_intent(keywords=审计精准词)`
- **THEN** sidecar 经已登录 Chrome 抓取匹配帖，返回 `Signal[]`（含 source/url/author/posted_at/text）
- **AND** 采集频控沿用现有冷却（间隔 ≥2h、每日 ≤3 次）

### Scenario: 采集层与内容采集隔离
- **WHEN** 写入采集结果
- **THEN** 信号持久化到独立 `signals`/`leads` 表，**不得**写入 `collected_notes`

## ADDED Requirement: 意图资格判定
系统 SHALL 对每条 `Signal` 判定真实求购意图、画像匹配度与触发事件类型，过滤噪声后方可成为 lead。

### Scenario: 噪声过滤
- **WHEN** `intent.classify` 判定画像匹配度低于阈值，或识别为同行 / 广告
- **THEN** 该信号丢弃，不进入线索收件箱

### Scenario: 命中成 lead
- **WHEN** 判定为真实求购意图且画像匹配
- **THEN** 生成 lead，`lead_status=qualified`，进入收件箱

## ADDED Requirement: 维度隔离
每条 lead SHALL 携带 `tenant_id` / `goal_id` / `persona_id`，并按 `tenant_id` RLS 隔离。lead 生命周期状态为 lead 专属字段，不复用内容 `funnel_stage`。

### Scenario: 跨租户不可见
- **WHEN** 审计 tenant 查询 leads
- **THEN** 仅返回审计 `tenant_id` 的 lead，售卖机 tenant 数据不可见

## ADDED Requirement: 人工闸门首触
系统 SHALL 为每条 lead 生成首触草稿，并要求人工确认后方可触达。V1 中系统 SHALL NOT 自动发送——人工确认 = 由运营人在平台外发出。

### Scenario: 一键闸门
- **WHEN** 运营人在 `/admin/leads` 对某 lead 点"通过"
- **THEN** lead 标记 `touched`，记录 `record_touch`
- **AND** V1 不触发任何自动发送动作

### Scenario: 引流词红线
- **WHEN** 首触草稿含微信 / 电话 / "加我"等引流词，或与历史触达高度雷同
- **THEN** 校验器拦截，禁止进入可发送草稿

## ADDED Requirement: 反封号约束
首触触达 SHALL 保持低量、人节奏、无引流词；XHS 渠道闸门最严。

### Scenario: XHS 渠道触达
- **WHEN** 通过 XHS 触达
- **THEN** 走人工闸门、随机间隔、单账号低量；不做批量自动留言

## ADDED Requirement: 读引擎商用合规
投入真实商用获客前，系统 SHALL 使用允许商用的读 / 写引擎。

### Scenario: 上线前切换闸门
- **WHEN** 准备对真实潜客触达 / 进入生产获客
- **THEN** 读写引擎必须为 ReaJason/xhs(MIT) 或已购 MediaCrawlerPro
- **AND** 免费版 MediaCrawler 仅限本地技术验证，不得用于生产商用获客
