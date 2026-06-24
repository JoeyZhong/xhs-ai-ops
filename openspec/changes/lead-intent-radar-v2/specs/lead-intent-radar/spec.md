# lead-intent-radar 能力规格（change delta · V2）

> 在已交付 V1（`openspec/specs/lead-intent-radar/spec.md`）之上做增量。
> 归档时把下列 MODIFIED/ADDED 合入 canonical 规格。
> 本轮范围 = 多源采集（红书/知乎/猪八戒）+ 小红书半自动一键发送（默认 dryrun）。

## MODIFIED Requirement: 意图信号采集（可替换采集器 · 多源）
系统 SHALL 通过注册到 Tool Registry 的 `collect.*` 工具，从 **goal 配置的多个信源**（`goal.lead_sources`，默认 `["xhs"]`）采集"主动公开发布需求"的信号，并归一为统一 `Signal` 结构（`source`/`source_url`/`signal_key`/`author`/`posted_at`/`post_text`，平台特有字段进 `meta`）。每个信源对应一个 collector 工具（`collect.xhs_intent` / `collect.zhihu_question` / `collect.zhubajie_demand`），各自 `fixture`（默认离线）/ `sidecar`（HTTP 真实）双实现可替换，不可达降级返 `[]`。

### Scenario: 多源采集合并去重
- **WHEN** `scan_goal` 读到 `goal.lead_sources=["xhs","zhihu","zhubajie"]`
- **THEN** 逐源调对应 collector，合并所有 `Signal`，按 `signal_key` 去重（`signal_key` 含源前缀 `xhs:`/`zhihu:`/`zhubajie:`，跨源天然唯一）
- **AND** 任一源采集失败/不可达 → 该源降级返 `[]`，不影响其余源

### Scenario: 平台特有字段隔离
- **WHEN** 猪八戒需求单带预算 / 交付周期 / 是否已接单
- **THEN** 这些字段进 `Signal.meta`（并随 lead 持久化到 `lead.meta`），不污染主流程；红书/知乎无 `meta` 时该结构缺省

### Scenario: 采集层与内容采集隔离（不变）
- **WHEN** 信号落库
- **THEN** 持久化到独立 `leads` 实体，**不得**写入 `collected_notes`

## MODIFIED Requirement: 人工闸门首触（增加一键发送，仅小红书）
系统 SHALL 为合格 lead 生成首触草稿（按 goal 关联 persona 的口吻，**草稿文体随信源**：小红书短回复 / 知乎专业回答 / 猪八戒接单报价；persona 不变）。触达 SHALL 逐条人工确认：
- **知乎 / 猪八戒**：沿用 V1，"通过" = 复制草稿 + 打开原帖/问题/需求单，系统 SHALL NOT 自动发送；
- **小红书**：在引擎就绪时可"一键发送"（逐条人工点击触发，非批量/非定时/非无人值守），默认走 `dryrun`（只校验+预览，不真实发出）。

### Scenario: 知乎/猪八戒只读触达
- **WHEN** 运营人对知乎/猪八戒 lead 点主操作
- **THEN** 复制草稿 + 打开对应原帖/问题/需求单，lead 标 `touched`；系统不触发任何自动发送

### Scenario: 小红书一键发送（演练默认）
- **WHEN** 运营人对小红书 lead 点"一键发送"且 `XHS_WRITE_ENGINE=dryrun`
- **THEN** 仅跑校验与预览、不真实发出、不修改 lead 状态，并向运营人明示"演练 · 不会真实发出"

### Scenario: 小红书一键发送（真发）
- **WHEN** `XHS_WRITE_ENGINE=reajason` 且凭证有效、校验通过、未超速率
- **THEN** 经 ReaJason `comment_note` 发出，lead `lead_status=touched` 并记录 `sent_at` / `send_platform_id` / `send_engine`

### Scenario: 引流词 / 雷同红线（不变）
- **WHEN** 草稿含引流词或与历史首触相似度过高
- **THEN** `sendable=false`，一键发送被拒（`blocked_checks`），不得真实发出

## ADDED Requirement: 写引擎可替换与发送闸门
系统 SHALL 通过 `outreach.send` 工具发出小红书首触，写引擎经 `XHS_WRITE_ENGINE` 抽象（`dryrun` 默认 / `reajason` 真发）。`outreach.send` SHALL 仅对 `source=xhs` 的 lead 生效；对知乎/猪八戒返回拒绝（只读）。引擎未配置有效凭证时 SHALL 返回 `engine_not_ready` 并拒发。每次真发 SHALL 写审计日志。

### Scenario: 非小红书源拒绝自动发
- **WHEN** 对知乎/猪八戒 lead 调 `outreach.send`
- **THEN** 返回拒绝（该源只读，无自动发），不触达

### Scenario: 引擎未就绪拒发
- **WHEN** `XHS_WRITE_ENGINE=reajason` 但凭证缺失/失效
- **THEN** 返回 `engine_not_ready`，回退人工"复制 + 打开原帖"路径

## ADDED Requirement: 发送速率限制
小红书一键发送 SHALL 受单账号速率限制：每日 ≤ N 条（默认 5）、两次发送间隔 ≥ 随机 5–15 分钟抖动。计数 SHALL 按 account/day 持久化（quota sidecar，带锁串行）。

### Scenario: 当日超限拒发
- **WHEN** 某账号当日真发已达上限
- **THEN** 返回 `rate_limited`，给出"今日 N/N"与距下次可发分钟数，拒绝本次发送

### Scenario: 间隔不足拒发
- **WHEN** 距上次真发不足随机抖动间隔
- **THEN** 返回 `rate_limited` 并给出剩余等待分钟数

## ADDED Requirement: goal 信源配置
goal SHALL 支持 `lead_sources` 配置（字符串数组，元素 ∈ `xhs|zhihu|zhubajie`，缺省 `["xhs"]` 向后兼容 V1）。`scan_goal` SHALL 据此决定从哪些信源采集；`goal.lead_radar_enabled` 仍控制是否被 scheduler 周期扫描。

### Scenario: 缺省向后兼容
- **WHEN** goal 未配置 `lead_sources`
- **THEN** `scan_goal` 视为 `["xhs"]`，行为与 V1 一致

## ADDED Requirement: 读写引擎商用合规（上云前置门，沿用 V1 并扩到写端）
投入正式商用获客（= 上云）前，系统 SHALL 使用允许商用的读 / 写引擎；写端 SHALL 为 ReaJason/xhs(MIT) 或已购 MediaCrawlerPro。

### Scenario: 上云前切换闸门
- **WHEN** 准备上云正式商用
- **THEN** 写引擎必须为 ReaJason/xhs(MIT) 或 Pro；免费版 MediaCrawler 不得用于商用写
- **AND** 内部验证阶段 `dryrun` / ReaJason MIT 均无 License 问题
