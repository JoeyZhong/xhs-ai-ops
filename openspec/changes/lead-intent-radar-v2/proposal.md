# Proposal: lead-intent-radar-v2 多源扩展 + 半自动一键发送

> **创建日期**：2026-06-18
> **状态**：**DRAFT · 待评审**（用户选定范围后写，待拍板再实现）
> **前置**：`lead-intent-radar` V1 已交付归档（archive `2026-06-17-lead-intent-radar`）。本 change 在其能力规格上扩展。
> **用户选定范围（2026-06-18）**：① 扩源（猪八戒 + 知乎 collector）② 半自动一键发送（⚠️需先切引擎）。
> **未选**：客群扩散发帖拦截、检测延迟统计——不在本轮。

---

## 1. Problem Statement

V1 跑通了"小红书单源 → 判定 → 草稿 → 人工复制+手动发"的闭环，但有两个明显天花板：

1. **信源单一**：只有小红书。而审计求购意图在**猪八戒（直接发需求单，接单是平台原生动作，转化最高）**和**知乎（高意图提问）**上同样密集，且这两个渠道封号风险低于小红书。雷达只盯一个池子，线索量受限。
2. **触达靠纯手工**：V1 "通过" = 复制草稿 + 打开原帖，运营人再手动粘贴发送。每条要切窗口、粘贴、发送，**省人工不彻底**。在合规引擎到位的前提下，可以做到**逐条人工确认后一键直接发出**，把"最贵的人工"（找/判/写）和"最廉价的人工"（点一下确认）之间那段手工操作也省掉。

## 2. Solution

在 V1 能力规格上扩展两块，**全部沿用 V1 的可替换盒子 + 共享契约模式**：

### 2.1 扩源 · 多信源采集（读端，低风险）
- 新增两个 collector 工具，挂同一 `collect.*` 契约（Signal 归一结构不变）：
  - `collect.zhubajie_demand`：猪八戒"发需求单"信号。
  - `collect.zhihu_question`：知乎高意图提问信号。
- 每个 collector 同样 **fixture（默认离线）/ sidecar（HTTP 真实）** 双实现，env 切换；不可达降级返 `[]`。
- `scan_goal` 泛化为**多信源**：读 `goal.lead_sources`（默认 `["xhs"]`，可配 `["xhs","zhihu","zhubajie"]`），逐源采集后合并去重（`signal_key` 跨源唯一），同一条判定/草稿/入库流程不变。
- `intent.classify` 已是通用判定（画像从 goal 派生），无需改；只是输入来源变多。

### 2.2 半自动一键发送（写端，⚠️需先切引擎 + 人工闸门）
- 新增**写引擎可替换盒子** `outreach.send`：给定 lead，按配置的写引擎发出首触。
  - **写引擎抽象** `XHS_WRITE_ENGINE`：
    - `dryrun`（**默认**）：不真发，仅校验 + 标记，便于本地建测。
    - `reajason`：真实发送，走 **ReaJason/xhs（MIT，免费商用，`comment_note`）**——**仅当用户完成引擎切换 + 配置有效凭证时可用**。
  - **硬闸门**：`dryrun` 是默认；切到 `reajason` 需要用户显式配置（= 完成上线前置门）。免费版 MediaCrawler 无写能力，不在此列。
- **逐条人工确认**：发送由运营人在 `/admin/leads` 对单条 lead 点"一键发送"触发，**非批量、非定时、非无人值守**。
- **发送前置校验**（任一不过则拒发）：
  - `sendable`（引流词 + 雷同度校验通过，复用 V1 校验器）。
  - **速率限制**：单账号 ≤ N 条/天（默认 5）、两次发送间隔 ≥ 随机分钟（默认 5–15min 抖动）。
  - 每次发送写 `audit_log`。
- 发送成功 → lead `lead_status=touched` + 记录 `sent_at` / 平台返回 id。
- UI：写引擎就绪时，详情区主按钮变"一键发送"；未就绪/dryrun 时保留 V1 的"复制草稿 + 打开原帖"作为兜底，并明示当前为 dry-run。

## 3. 明确边界（红线，写进 spec）

**本 change 做的是"人工逐条确认的辅助发送"，不是"无人值守批量自动留言"。**

| 会做 | 不会做 |
|---|---|
| 逐条人工点"发送"才发一条 | 定时/批量/无人值守自动留言 |
| 速率限制 + 随机间隔 + 校验拦截 | 去掉人工闸门 |
| 只走商用合规引擎（ReaJason MIT / Pro） | 用免费版 MediaCrawler 写 / 任何检测规避花招 |
| 默认 dry-run，切真发需显式配置 | 默认就真发 |

## 4. Out of Scope（本轮不做）

- 客群扩散发帖拦截（ContentAgent 选题对准 Tier-1 扩散词）——用户未选，留 V2.1。
- 检测延迟精确统计——用户未选。
- `cookie_manager` 泛化多平台：本轮写端仅 XHS（ReaJason）；知乎/猪八戒为**只读**，不涉及多平台写会话，故暂不泛化（真要多平台写时再做）。
- 知乎/猪八戒的**写端**（自动回答/接单）——本轮这两源只读、只采集，触达仍走各自平台原生人工动作。

## 5. Impact

**新增**：
- `agent_tools/collect_zhubajie.py`、`agent_tools/collect_zhihu.py`（+注册 + bootstrap）。
- `agent_tools/outreach_send.py`：`outreach.send` 工具 + 写引擎抽象（dryrun/reajason）+ 速率限制器。
- `agents/lead_radar.py::scan_goal`：泛化多信源（读 `goal.lead_sources`）。
- `server/routers/leads.py`：新增 `POST /api/v1/leads/{id}/send`。
- `frontend`：详情区"一键发送"按钮 + 写引擎状态提示；`leadsApi.send`。
- `db/migrations/013_leads_send.sql`：`leads` 加 `sent_at` / `send_platform_id` / `send_engine` 列。
- storage：`update_lead` 允许新列；本地后端同步。
- 速率限制状态：`config/lifecycle_send_quota.json`（按 account/day 计数）。

**修改**：
- V1 spec `人工闸门首触` 的"通过=复制+手动发" → MODIFIED 为"通过=复制+手动发 **或** 一键发送（引擎就绪时）"。
- `collect.*` 能力 MODIFIED：从单源 → 多源（lead_sources）。

**Strangler / 边界**：
- 写引擎默认 dryrun，不改变 V1 现状（不会因为部署本 change 就真发）。
- 知乎/猪八戒 collector 与 XHS 同契约，scan 流程不变。

## 6. 横切维度影响审查（AGENTS.md 必填）

- [x] **tenant_id**：已保留（lead 维度不变，沿用 V1）。
- [x] **goal_id**：已保留 + 新增 `goal.lead_sources` 配置（哪些信源）。
- [x] **persona_id**：已保留（首触草稿/发送口吻按 goal 关联 persona）。
- [x] **funnel_stage**：不涉及（lead 用自有生命周期，沿用 V1）。

## 7. Risk

| 风险 | 缓解 |
|---|---|
| **自动发送触 XHS 风控封号** | 逐条人工确认（非批量）+ 速率限制（≤5/天、随机间隔）+ 引流词/雷同校验拦截 + 仅 ReaJason 单引擎单指纹；默认 dryrun |
| **未切引擎就误真发** | `XHS_WRITE_ENGINE=dryrun` 默认；reajason 需显式配置 + 有效凭证，否则 `outreach.send` 返回"引擎未就绪"拒发 |
| **ReaJason 写凭证 / 商用合规** | ReaJason/xhs 为 MIT 免费商用；凭证走现有 `cookie_manager`（XHS 已支持）；本 change 不引入免费版 MediaCrawler 写 |
| **猪八戒/知乎 collector 真实抓取** | 同 V1：fixture 默认离线，sidecar 真实可替换；不可达降级 `[]` |
| **多源 signal_key 冲突** | `signal_key` 含 source 前缀（`zhihu:` / `zhubajie:` / `xhs:`），跨源天然唯一 |
| **速率计数并发** | quota sidecar 走 `_SIDECAR_LOCK` 串行（对齐现有 OCC 写路径） |

## 8. 决策锁定（A/B/C 已拍板 2026-06-18）

**A. 商用闸门绑定到「上云」** —— 闸门往后推：**上云 = 正式商用**那一刻才需满足商用引擎要求；现在是内部验证。
- 本轮：写引擎默认 `dryrun`，`reajason` 适配位留好；要真发时显式配置即可（内部验证用 ReaJason MIT 也无 License 问题）。
- 商用 License 义务（读+写引擎须商用合规、免费版 MediaCrawler 不得商用）在**上云**时生效。STATUS gate 措辞从"真实获客前"改为"上云正式商用前"。

**B. 发送速率** —— 锁定 ≤5 条/天、两次间隔 5–15min 随机抖动（单账号）。

**C. 扩源** —— 猪八戒 + 知乎**一起做**。但有两个前置（用户要求）：
- **C1 产品方案先说清**：猪八戒/知乎信息如何与 goal 等现有实体关联 → 见 `design/multi-source-product-model.md`（本 change 交付）。
- **C2 设计稿用户自做**：UI 设计稿由用户在桌面端执行，**AI 不产出 UI mockup、不碰 design-sync**。本 change 只给产品/数据层方案作为设计输入。

> 下一步：用户审 `design/multi-source-product-model.md` + 自行出 UI 设计稿。两者就绪后，再补 `tasks.md` + `specs/` delta 并实现。
