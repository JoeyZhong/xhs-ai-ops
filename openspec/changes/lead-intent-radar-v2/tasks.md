# Tasks: lead-intent-radar-v2

> 范围（用户锁定 2026-06-18）：① 扩源（猪八戒 + 知乎 collector）② 半自动一键发送（默认 dryrun，逐条人工确认，≤5/天 + 5–15min 抖动）。
> 设计输入：`design/multi-source-product-model.md`（产品/数据模型）+ `design/leads-inbox-v2.html`（UI 定稿，用户出）。
> 红线：人工逐条确认、默认演练、知乎/猪八戒只读、商用引擎闸门绑定上云（proposal §8-A）。

## 1. 规格与计划（本目录）
- [x] 1.1 proposal.md（A/B/C 已拍板）
- [x] 1.2 design/multi-source-product-model.md（C1 产品模型）
- [x] 1.3 design/ui-design-brief.md + design/leads-inbox-v2.html（C2 UI，用户定稿 commit 9bf1852）
- [x] 1.4 tasks.md + specs/ delta（本文件）

## 2. 数据层（leads 加发送字段 + goal 加信源）
- [x] 2.1 `storage/local_json.py`：`_LEAD_FIELDS` + create_lead item + update_lead `allowed` 加 `sent_at` / `send_platform_id` / `send_engine`
- [x] 2.2 `db/migrations/013_leads_send.sql`：leads 加三列（PG 平价，local 不依赖）
- [x] 2.3 `config/goals.json`：审计 goal `goal_173655b8` 加 `lead_sources: ["xhs","zhihu","zhubajie"]`（其余 goal 缺省视为 `["xhs"]`）

## 3. 采集层（多源 collector，读端低风险）
- [x] 3.1 `agent_tools/collect_zhihu.py`：`collect.zhihu_question`（fixture 默认 / sidecar；source=zhihu）
- [x] 3.2 `agent_tools/collect_zhubajie.py`：`collect.zhubajie_demand`（fixture/sidecar；source=zhubajie；meta=预算/交付周期/接单状态）
- [x] 3.3 `agent_tools/__init__.py`：两个新模块入 `_TOOL_MODULES` bootstrap
- [x] 3.4 不可达降级返 `[]`（沿用 V1 契约）

## 4. 草稿文体随源（persona 不变）
- [x] 4.1 `agent_tools/lead_outreach.py`：`outreach.draft` 加 `source` 参数 + 三套模板（xhs 短回复 / zhihu 专业回答 / zhubajie 接单报价）

## 5. 写端：一键发送（仅 xhs · 默认 dryrun · 人工逐条）
- [x] 5.1 `agent_tools/outreach_send.py`：`outreach.send` 工具
  - [x] 写引擎抽象 `XHS_WRITE_ENGINE`：`dryrun`（默认，只校验+预览不真发）/ `reajason`（真发，需有效凭证，否则 engine_not_ready）
  - [x] 发前置校验：source==xhs；sendable（引流词+雷同复用 V1 字段）
  - [x] 速率限制：account ≤5/天 + 两次间隔 5–15min 随机；quota sidecar `config/<tenant>/lifecycle_send_quota.json`（带锁）
  - [x] 真发成功：持久化 lead `lead_status=touched` + `sent_at` / `send_platform_id` / `send_engine`，并 +1 计数
  - [x] 返回 status ∈ {sent, dryrun, blocked_checks, engine_not_ready, rate_limited, source_unsupported} + 计数/下次可发
- [x] 5.2 `agent_tools/__init__.py`：outreach_send 入 bootstrap

## 6. 编排：scan_goal 多源
- [x] 6.1 `agents/lead_radar.py::scan_goal`：读 `goal.lead_sources`（默认 `["xhs"]`），按源映射 collect 工具，合并 + signal_key 跨源去重
- [x] 6.2 判定/草稿/入库不变；草稿传 `source`；猪八戒 `meta` 透传 create_lead
- [x] 6.3 stats 增 `by_source`（供前端分布）

## 7. API
- [x] 7.1 `server/routers/leads.py`：`POST /api/v1/leads/{id}/send`（SendRequest{account_id?}）→ invoke `outreach.send` → 映射状态 + 返回 lead
- [x] 7.2 错误态映射：业务态（blocked_checks/engine_not_ready/rate_limited/source_unsupported/dryrun/sent）走 200 带 status；lead 不存在 404；其它 invoke 失败 422

## 8. 前端（按定稿 leads-inbox-v2.html）
- [x] 8.1 `frontend/lib/api.ts`：Lead 加 `sent_at`/`send_platform_id`/`send_engine`；`LeadSource`/`SendResult` 类型；`leadsApi.send`
- [x] 8.2 Delta1 队列卡信源字符徽标（红/知/猪）+ 队列顶源筛选分段
- [x] 8.3 Delta2 猪八戒 meta（卡片 chip + 详情结构化小卡；缺省不渲染）
- [x] 8.4 Delta3 详情主按钮按 source 切换（xhs 一键发送；zhihu/zhubajie 复制+打开问题/需求单）+ 来源行/链接/文体随源
- [x] 8.5 Delta4 一键发送全状态（可发送/演练/发送中/已发/校验未过/引擎未就绪/速率超限）+ sendbar 计数
- [x] 8.6 Delta5 stats 今日合格按源小分段

## 9. 验收
- [x] 9.1 后端离线 E2E 脚本：多源 scan（fixture，mock LLM）+ send 全状态 + 速率超限；28/28 通过，零真实 config 污染
- [x] 9.2 `npx tsc --noEmit` + eslint 前端零报错；HTTP TestClient 验证 /send（dryrun 200 / source_unsupported / 缺 Idempotency-Key 428 / lead 不变）
- [x] 9.3 `docs/STATUS.md` V2 行更新；memory 更新
- [ ] 9.4 人工验收（红/知/猪三源各一条；xhs 演练发送链路；切真发仍 gated）→ 通过后归档

## 10. 上云前置门（不在本轮，记账）
- [ ] 10.1 切读写引擎到 ReaJason/xhs(MIT) 或 Pro（免费版 MediaCrawler 禁商用）——proposal §8-A，上云=正式商用时执行
