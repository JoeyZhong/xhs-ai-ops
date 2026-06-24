# Proposal: lead-intent-radar 需求意图雷达（主动获客新子系统）

> **创建日期**：2026-06-17
> **状态**：**已评审 · 范围锁定 · 待启动实现**（决策 A/B/C 已拍板，见 §9；`tasks.md` + `specs/lead-intent-radar/spec.md` 已补）
> **触发**：审计报告业务上线后发现——除了「发帖被动获客」，小红书上存在**主动公开发布需求的客户**（求审计报告、要贷款/投标/高新等），希望在他们发声后**第一时间主动触达**，把"被动等"变成"主动截"。
> **关联调研**：本提案是 2026-06-16~17 六轮讨论的收口（Scrapling → MediaCrawler-CDP → Sidecar → 需求层 → 项目归属 → 客群扩散/流量拦截）。
> **本质定位**：这是一套**可横向复用的「在线意图雷达 + 人工闸门首触」引擎**，审计是它的第一个垂直验证案例；换垂直 = 换「需求图谱 + 信源 + 触达钩子」，底座不变。

---

## 0. 总结评审（六轮讨论收敛结论）

| # | 议题 | 结论 | 关键理由 |
|---|---|---|---|
| 1 | 抓取层是否换 Scrapling | **部分采纳**：传输层指纹值得升级，但签名层它解决不了 | Scrapling 优化"传输+外壳"，给不了 XHS 私有签名 |
| 2 | 签名层怎么根治 | **浏览器上下文自签**（让真实已登录 Chrome 自签） | 三条路线里唯一对"签名易碎"的结构性解法 |
| 3 | 怎么接入 | **Sidecar 即工具**：爬虫关进独立进程，主代码只认 `collect_*` 契约 | 解耦 + 共享契约；XHS 改签名只换盒子，主平台无感 |
| 4 | 自动评论能否直接用 | **不能**：MediaCrawler 严格只读，无发评论接口 | 写路径要自建；最佳是**驱动同一个 Chrome** 发，读写同源同指纹 |
| 5 | 需求源头评估 | XHS 批量自动留言对"审计获客"性价比最低；真护城河是**检测延迟**不是留言数量 | 需求低频+商品化+价格敏感→谁先发现谁先触达者赢 |
| 6 | 客群扩散 / 流量拦截 | **对角线策略**：精准词→主动首触；扩散词→发帖拦截（复用 ContentAgent） | 扩散用评论=高成本高封号低转化；用内容卡位安全可规模化 |
| 7 | 新开项目 vs 现有项目 | **留在现有项目**，作为边界清晰的新子系统 | 平台本就是多租户通用底座；新增的只有"精准雷达+首触"，落在 IntelAgent 本职 |
| 8 | 首触自动化程度 | **半自动 + 一键人工闸门**，渠道风险分级 | 砍掉最贵的人工（找/查/写），保留最廉价的一键确认（保安全+转化） |
| 9 | 读引擎选型（License） | **V1 免费版本地 spike；上线前切 ReaJason/xhs(MIT) 或 Pro** | 免费版 MediaCrawler 禁止商用，不靠措辞规避，靠"上线前必切"硬闸门兜底 |

---

## 1. Problem Statement

审计报告业务（中小企业、要快、要便宜，多因贷款/投标/高新/外资/注销触发）当前只有**发帖被动获客**一条路。但观察到两个事实：

1. **存在主动求购信号**：小红书等平台上有客户公开发帖「急求一份审计报告」「公司贷款要审计报告找谁」——这类人**已自我筛选**为在场、高意图、画像匹配的线索。
2. **现有平台抓不到、也不会主动触达**：`IntelAgent` 现有工具（`search.*`/`hot_monitor.*`）面向"采集爆款样本喂内容创作"，没有"持续监控主动求购信号 → 资格判定 → 人工闸门首触"的链路。

后果：这些**最高转化的线索每天稍纵即逝**，靠人工刷平台既慢又漏，且竞争是"谁先回复谁赢"的速度游戏，人工根本跑不赢。

## 2. Solution

在现有平台内**新增一个边界清晰的子系统 `lead-intent-radar`**，复用 `agents` 编排内核 / `registry` / sidecar / scheduler / draft 闸门 / 多租户存储，**不与售卖机内容逻辑耦合**。三块能力：

### 2.1 读 · 多源采集（Sidecar，业务无关）
- **XHS 读 sidecar**（独立进程，`safe_run.py` 沙箱兜底）负责抓帖 + 抓评论。
  - **License 约束（决策 B）**：MediaCrawler 免费版**禁止商用**，V1 仅作**本地技术验证（feasibility spike）**；**上线真实商用获客前必须切换到 ReaJason/xhs（MIT，免费商用，读写齐全）或购买 Pro**——见 §9 与 `tasks.md` 1.5 切换闸门。
- 新增 collector 工具注册到 `registry`，挂到 `IntelAgent` 工具带：V1 仅 `collect.xhs_intent`；后续 `collect.zhubajie_demand`、`collect.zhihu_question`。读端扩源**零摩擦**——契约统一为 `collect_signals(source, keywords) -> Signal[]`。

### 2.2 判 · 意图资格 + 线索队列
- 新增 `agent_tools/intent_classifier.py`：对采集到的原文判定 `{是否真实求购意图, 画像匹配度, 触发事件类型}`，过滤噪声。
- 新增 **lead/signal 独立持久化**（新表，**不复用 `collected_notes`**，规避已知中英列错配 / goal 隔离断裂的坑）。
- 命中线索进**线索收件箱**（复用 `/admin/drafts` 的 review UX 范式）。

### 2.3 触 · 半自动首触（人工闸门）
- 系统**全自动做重活**：监控 → 检测 → 资格判定 → **按线索定制首触草稿**（复用 ContentAgent 生成能力）→ 排序进收件箱。
- 人**只做一键动作**：扫一眼 → 通过/改/跳过。
- **V1 触达=人工发**：通过=运营人在 app 里自己发；写路径自动化（CDP 同源发 / ReaJason `comment_note`）在 V1 之后、且与 License 切换同步。
- **渠道风险分级**：猪八戒/知乎（平台原生互动、低封号）可放更自动；XHS 评论（封号雷区）闸门收紧、人节奏、无引流词校验。

### 2.4 扩 · 客群扩散用「发帖」不用「评论」
「意图精度 × 触达机制」走对角线：

| | 精准词（求审计报告）= 主动求购者 | 扩散词（贷款/投标/高新）= 上游客群 |
|---|---|---|
| **主动触达（评论/私信/接单）** | ✅ 高意图低量，一对一首触，安全 | ⚠️ 量大意图散，逐条评论=高成本+高封号+低转化，**不做** |
| **发帖拦截（内容卡位）** | 可做（沉淀案例页） | ✅ **正解**，复用 ContentAgent 发笔记，安全可规模化 |

→ **扩散这一大块几乎是白送的**（ContentAgent 已会发笔记，只需把选题对准 Tier-1 扩散词）；本子系统真正新增的只有**精准词雷达 + 首触**。例外：Tier-1 高流量帖/高赞回答下，**人工+高质+低量**的专家评论可纳入，但不是机器刷量。

## 3. 涵盖需求项 / 范围

| 能力 | 优先级 | 落点 | V1? |
|---|---|---|---|
| XHS signal 采集 | P0 | `agent_tools/collect_xhs_intent.py` + XHS 读 sidecar | ✅ |
| 意图资格判定 | P0 | `agent_tools/intent_classifier.py`（调 LLMProvider） | ✅ |
| lead/signal 持久化 + 收件箱 | P0 | 新 PG 表 + storage 方法 + `/admin/leads` 页 | ✅ |
| 首触草稿生成 | P0 | 复用 ContentAgent 生成 + 首触 prompt | ✅ |
| 审计 tenant + persona + goal | P0 | `personas.json` / `goals.json` / PG tenant | ✅ |
| scheduler 持续监控 | P0 | 复用 `agents/scheduler.py` | ✅ |
| XHS 触达自动化（同源发） | P1 | sidecar 写路径（切 ReaJason/xhs 后） | ✖ |
| 知乎/猪八戒 采集 + 触达 | P2 | 各自 collector + touch adapter | ✖ |
| 扩散发帖（Tier-1 选题对准） | P1 | 复用现有 ContentAgent 链路，仅扩选题词 | ✖ |

## 4. Out of Scope（明确不做）

- **冷名单外呼**（企查查维度筛选 → 电话/EDM）：低转化 + PIPL/骚扰电话合规风险，按用户铁律"转化低=没意义"，不做。
- **大规模自动留言 / 反检测规避封号**：明确排除；本子系统靠"低量+人工闸门+高质首触"，不靠机器刷量躲检测。
- **SEO/SEM 投放系统**：流量拦截的"慢钟"（内容卡位地基）本提案只覆盖"发帖对准扩散词"，不含百度竞价/落地页系统。
- **线下渠道转介**（代账/银行/招投标 BD）：用户只做线上，不在范围。
- **V1 不含**：自动发送、扩源（知乎/猪八戒）、扩散发帖——均在 V1 之后。
- 售卖机业务的任何改动。

## 5. Impact

**新增**：
- 1 个 XHS 读 sidecar 服务（独立进程/独立依赖树；V1=MediaCrawler 免费版 spike）
- collector 工具：V1 `collect.xhs_intent`（后续 zhihu/zhubajie）
- `agent_tools/intent_classifier.py` 新工具
- 新 PG 迁移：`leads`/`signals` 表（**独立于 `collected_notes`**）+ storage 方法 `upsert_signal`/`list_leads`/`record_touch`
- 前端新页 `/admin/leads`（线索收件箱 + 一键首触，复用深色外壳 + draft review 范式，**无 emoji，lucide 线性图标**）
- 审计 tenant + persona（`personas.json`）+ goal（`goals.json`）
- 新 capability spec：`openspec/specs/lead-intent-radar/spec.md`（归档时合入）

**修改**：
- `IntelAgent` 工具白名单 + `ToolPolicy` 增 `collect.*`/`intent.*`
- `registry` 注册新工具

**Strangler / 边界原则**：
- 不动 Streamlit、不动售卖机内容链路、不动 v1 已交付模型
- lead 实体独立建表，不污染 `collected_notes`
- sidecar 独立进程，崩溃不拖垮主平台

## 6. 横切维度影响审查（AGENTS.md 必填）

- [x] **tenant_id**：已保留 —— local 模式复用现有 `default` 租户，**不新建租户**；lead 仍带 `tenant_id`（=default）以便将来切 PG
- [x] **goal_id**：新增依赖 —— 每条 lead 关联 `goal_id`（审计获客目标），触发事件类型挂目标
- [x] **persona_id**：新增依赖 —— 首触草稿用审计 persona 的口吻；触达账号按 persona 绑定
- [x] **funnel_stage**：不涉及（内容漏斗阶段） —— **但 lead 有自己的生命周期** `detected→qualified→drafted→touched→replied→converted`，作为 lead 专属状态字段，**不复用** content `funnel_stage`；非跨切维度，故 `data-dimensions/spec.md` 不新增条款

## 7. Risk

| 风险 | 缓解 |
|---|---|
| **XHS 触达封号**（评论/私信触风控） | 读写同源单指纹；低量、人节奏随机间隔、无引流词校验；人工闸门;XHS 仅作信源之一不主力 |
| **MediaCrawler 免费版禁止商用** | V1 仅本地 spike 验证；`tasks.md` 1.5 设硬切换闸门：上线真实获客前切 ReaJason/xhs（MIT）或买 Pro。"暂时自己用"不改变获客=商用的性质，故以"上线前必切"兜底 |
| **合规（PIPL / 平台 ToS）** | 只触达"主动公开发帖者"（非抓取私人联系方式群发）；首触走平台原生动作；不做冷名单 |
| **意图判定误报**（把同行/广告当客户） | classifier 加画像匹配度阈值 + 人工闸门兜底；误报样本回流优化 |
| **数据层异构**（多源 schema 不齐） | lead 独立表 + 统一 `Signal` 归一结构；绕开 `collected_notes` 已知坑 |
| **签名易碎仍在**（CDP 也依赖真实页面） | 维护点收敛进 sidecar 一个盒子，可独立替换/回退到现有 `requests` 链路 |

## 8. 转化度量（V1 成功标准）

用户铁律是"转化低=没意义"，故 V1 必须可量化：
- 雷达侧：每日检测到的合格 lead 数、检测延迟（发帖→入收件箱）
- 触达侧：首触发出数、**沟通机会数（回复/加联系）= 核心北极星**、最终成交数
- 对照：人工刷平台的基线 vs 雷达后的 lead/天 与沟通机会/周

---

## 9. 决策锁定（A/B/C 已拍板 2026-06-17）

**A. V1 信源 —— 锁定：小红书单源先行**
- 理由：已验证审计求购帖存在；平台 XHS 原生，集成成本最低。
- 安全约束：V1 首触**先纯人工发**（系统出草稿 → 人工在 app 发），闭环+转化跑通后再自动化写路径。

**B. 读引擎 —— 锁定：V1 用 MediaCrawler 免费版做本地技术验证；上线前硬切换**
- V1（spike）：MediaCrawler-CDP 免费版，本地验证"采集→判定→草稿"可行性，不对外真实获客。
- **硬闸门**：在投入真实商用获客（对真实潜客发首触 / 产生收入）**之前**，必须切换到 **ReaJason/xhs（MIT，免费商用，且自带 `comment_note` 写路径）** 或购买 MediaCrawlerPro。
- 诚实记录："暂时自己用"不改变"为付费审计业务获客=商业用途"的性质；故不以措辞规避，而以"上线前必切到合规引擎"兜底。切换同时一并解决写路径。

**C. 审计 goal/persona —— 锁定：选项 2，V1 第一步即配置审计 goal + 人设（修正：非独立 tenant）**
- 实证修正：`STORAGE_BACKEND=local`，无独立 PG 租户；审计与售卖机同在 `default` 租户下，**按 `goal_id` 隔离**（平台真正生效的隔离维度）。
- V1 Phase 0 = 充实已存在的审计 goal `goal_173655b8` + 新增审计 persona，**由用户经「目标对齐」「人设管理」前端自助配置**（非手改 JSON）。
- 模型：租户 → 多 goal（各自 `persona_id` 指向租户级 persona 池）。

> 范围已锁。`tasks.md` 按实现顺序、`specs/lead-intent-radar/spec.md` 的 Requirement/Scenario delta 已补。下一步等你说"启动实现"。
