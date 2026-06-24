# Tasks: lead-intent-radar（V1 · 小红书单源 · 人工首触）

> 实现顺序自上而下；完成一个勾一个 `- [x]`。
> V1 范围 = 小红书单源 + 审计 tenant + 雷达→判定→草稿→**人工**首触闭环。
> 自动写路径 / 扩源 / 扩散发帖在 V1 之后（见末尾）。

## Phase 0 · 审计 goal + persona 落地（决策 C=2 · local 模式 / default 租户）
> 修正：local 模式无独立 PG 租户，审计与售卖机同在 `default` 租户下，按 `goal_id` 隔离。
> **解耦原则（用户提出）**：审计 persona/goal 是**运行时数据**，与本 feature 解耦——radar 代码对任意 goal/persona 通用；真实业务值随时可经「人设管理」「目标对齐」UI 改、或上线时再填。本次应用户要求先用其提供的事实 seed 一份。
- [x] 0.1 复用现有 `default` 租户；审计线索按 `goal_id` 隔离；lead 仍带 `tenant_id`（=default）备将来切 PG
- [x] 0.2 新增审计 persona `audit_kuaichu`（自有 CPA 所 / 全国 / 可加急 / 低至 3000）→ `personas.json`（昵称、首触 system_prompt 为占位，待 Phase 3 / 用户改）
- [x] 0.3 充实审计 goal `goal_173655b8`（objective/描述/受众/品牌定位/keywords=[审计报告]/persona_id=audit_kuaichu）→ `goals.json`
- [x] 0.4 核对：goal 有 persona_id + keywords，JSON 合法，radar 可用

## Phase D · 设计确认闸门（方案乙：提前到建表之前，让 UI 定 schema）✅
- [x] D.1 产出 `/admin/leads` HTML 设计稿（`design/leads-inbox.html`）→ 用户桌面端审阅 → **已确认定稿**（用户改：主题切暖纸 warm-paper + 深色侧栏外壳，对齐首页；字段集/交互未动）
- [x] D.2 **冻结字段集**（Phase 2 建表依据）：
  - signal/lead 核心：`source`(小红书) · `source_url` · `author` · `posted_at` · `excerpt`/`post_text`(原帖全文) · `detected_at`/检测延迟
  - 意图判定：`is_intent`(是否求购) · `match_score`(画像匹配度%) · `trigger_type`(贷款/投标/高新/外资/注销) · `judge_reason`(判定理由)
  - 首触：`draft_text`(草稿) · `check_lure`(引流词校验) · `check_dup`(雷同度校验)
  - 生命周期：`lead_status`(detected→qualified→drafted→**pending/touched/skipped**)
  - 关联维度：`tenant_id`(=default) · `goal_id` · `persona_id`
  - 度量埋点：北极星=沟通机会数；辅以 今日合格线索 / 平均检测延迟 / 本周成交

## Phase 1 · 读 · 小红书采集（XHS 读 sidecar）
- [x] 1.1 采集器可替换契约（`design/sidecar-contract.md`）：FixtureCollector(离线,默认) + SidecarCollector(HTTP)；env `XHS_COLLECTOR` 切换；sidecar 不可达降级返 []（真实 MediaCrawler/ReaJason 进程仍是激活时的盒子，未拉依赖）
- [x] 1.2 `agent_tools/collect_xhs_intent.py`：`collect.xhs_intent(keywords,limit)`，注册 `registry` + `agent_tools/__init__` bootstrap
- [x] 1.3 统一 `Signal` 归一结构（source/source_url/signal_key/author/posted_at/post_text）+ signal_key 稳定去重键
- [x] 1.4 编排中枢 `agents/lead_radar.py::scan_goal`（采集→判定→草稿→入库，逐条容错+幂等）；`scripts/run_radar_scan.py` 手动跑；端到端验证(5扫描/2噪声/3合格/3建/重扫幂等)
- [~] 1.5 ⚠️ **切换闸门（决策 B · 上线前置门，不随本 change 关闭）**：上线真实获客前，把读/写引擎切到 ReaJason/xhs(MIT) 或购买 Pro。**未完成不得对真实潜客触达。** 归档后转为 STATUS 常驻 gate（见 STATUS §4）

## Phase 2 · 判 · 意图资格 + 持久化
- [x] 2.1 `agent_tools/intent_classifier.py`：调 LLM(json_mode,低温) 判 `{is_intent, match_score, trigger_type, judge_reason, qualified}`，注册 `intent.classify`；画像从 goal 派生(横向通用)；纯解析函数防御性降级；端到端 + 纯解析单测通过
- [x] 2.2 PG 迁移 `db/migrations/012_leads.sql`：`leads` 表（`tenant_id`/`goal_id`/`persona_id`/`lead_status` + 冻结字段 + RLS + signal_key 唯一去重），**独立于 `collected_notes`**（V1 只存合格 lead，噪声不落库，故暂不单建 signals 表）
- [x] 2.3 storage 方法 `create_lead`（signal_key 幂等）/ `get_lead` / `list_leads`（新鲜度×匹配度排序 + goal/status/trigger 过滤）/ `update_lead`（OCC + 状态流转 + outcome）/ `delete_lead`；写进 `StorageBackend` Protocol；本地后端实测通过（幂等/OCC/隔离/北极星计数）
- [x] 2.4 噪声过滤：classifier `qualified=is_intent and match_score≥阈值`；非求购/低匹配丢弃，合格才 create_lead 入收件箱（端到端验证：同行广告被过滤不入库）

## Phase 3 · 收件箱 + 首触草稿（前端 + 人工发）
- [x] 3.1 `/admin/leads` 收件箱页（队列+详情，按 Phase D 定稿；真实 globals token + lucide 图标无 emoji；tsc+eslint 通过）+ 后端 `server/routers/leads.py`（list/stats/detail/PUT-OCC/touch，TestClient 全绿）+ `leadsApi` + Sidebar「线索雷达」导航 + `scripts/seed_demo_leads.py` 演示数据
- [x] 3.2 首触草稿生成 `outreach.draft`（审计 persona 口吻，短回复 80-130 字，硬规则禁引流词/不写死时效）；persona_id 从 personas.json 解析；mock LLM 端到端通过
- [x] 3.3 引流词 + 雷同度 校验器（`check_lure_words` / `check_similarity` 纯函数；命中→`sendable=False` 禁发）；各类引流词/完全雷同全部拦截，纯函数单测通过
- [x] 3.4 一键 通过(复制草稿+打开原帖→`touched`)/改(PUT 草稿)/跳过(`skipped`)；已触达可标记 outcome=有回复/成交(北极星)；**V1 通过=人工去 app 发，系统不自动发**（页面诚实标注）

## Phase 4 · 持续监控
- [x] 4.1 `agents/scheduler.py::_radar_scan` 周期跑 `scan_goal`（cron 09:30/14:30/20:30 = 3次/天≥2h，符合频控）；只扫 `goal.lead_radar_enabled=true` 的 goal（审计 goal 已 opt-in，售卖机 goal 不扫）；注册进 `register_default_jobs`；验证只扫 opt-in + 幂等 + 真实库无污染

## Phase 5 · 度量（扣住"转化低=没意义"）
- [x] 5.1 `/api/v1/leads/stats`：今日合格线索 / 待处理 / 本周成交；收件箱顶部数据条展示
- [x] 5.2 北极星：本周沟通机会数（outcome=replied/converted），收件箱已触达可一键标记「有回复/成交」回填；stats 实时计数
  - 注：检测延迟(发帖→入库)字段已存(posted_at/detected_at)，精确均值统计 V1 暂缓（posted_at 为平台自然串，解析不稳定，不做假数据）

## V1 之后（不在本 change 范围 · 归档时转入 memory backlog，非未完成项）
> 以下为 V2 候选，**不阻塞本 change 归档**。已记入 backlog：
- 写路径自动化：切 ReaJason/xhs `comment_note` 或 CDP 驱动同 page 发（读写同源）
- 扩源：知乎提问 / 猪八戒需求单 collector
- 客群扩散：ContentAgent 选题对准 Tier-1 扩散词（贷款/投标/高新/外资/注销）发帖拦截
- `cookie_manager` 泛化为多平台会话库
- 检测延迟精确均值统计（需稳定解析 posted_at）
