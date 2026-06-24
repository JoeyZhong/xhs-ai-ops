# Proposal: cross-cutting-dimension-governance

> **创建日期**：2026-05-28
> **触发**：2026-05-28 用户发现采集数据未按 `goal_id` 隔离，全链路 4 层断裂；Opus 反思识别为架构治理空缺（横切数据维度无 spec 守护）
> **依据**：用户反馈 + `memory/project_collected_data_goal_isolation_broken.md`
> **优先级**：插队 — 必须在 `content-lifecycle-v2 P2 evidence pool` 启动前完成（否则 evidence 会继承同样污染）

---

## Problem Statement

项目已有完整的 capability-level spec（multi-tenant-storage / web-api / agent-architecture 等），但**没有"横切数据维度治理"的 spec**。`tenant_id` 是历史上唯一被全链路守护的维度（RLS policy / `_require_tenant()` / `verify_token` 三道关），其他横切维度（`goal_id` / `persona_id` / `knowledge_refs` / `memory_refs` / `funnel_stage`）没有任何同级保护。

2026-05-28 暴露的具体后果——`goal_id` **4 层断裂**：

| 层 | 文件 | 现状 |
|---|---|---|
| API | `server/routers/notes.py:50,56` | 接 `goal_id` query 参数但完全丢弃，未传给 backend |
| PgBackend 读 | `storage/pg_backend.py:97-110` | SELECT 出 `goal_id` 列，WHERE 不过滤 |
| LocalJsonBackend 存 | `storage/local_json.py:117-124` | 文件名 `spider_xhs_采集结果_{ts}.xlsx` 不带 goal_id，源头就丢归属 |
| 采集源 | `agent_tools/search.py` | 未确认 row 里是否塞了 goal_id（即便 PG 列在，可能全 NULL） |

更深层根因：

1. **现有 spec 按 capability 切，没有按"数据维度"切** —— spec 回答"这个能力长什么样"，没回答"这个数据维度如何流转"。
2. **架构对称性失衡** —— tenant_id 守护严，导致团队产生"隔离已解决"的心智惯性；实际上 RLS 守不了同 tenant 内的多 goal 污染。
3. **缺"多 goal 并存"first-class scenario** —— 当前用户只有 1 个 active goal，bug 在生产路径被掩盖。Orchestrator V1.3 启动后多 goal 是常态。
4. **缺架构级守护测试** —— `verify_web_skeleton.py` 守护 6 节单点 invariant，没有守护"链路完整性"。
5. **变更管控偏 capability，没横切审查** —— openspec proposal 模板没要求作者列出"本变更影响的横切维度"。

## Solution

新建本 change，建立"横切数据维度治理"框架，**首批落地 goal_id 隔离**作为参考实现。

3 件事，按顺序串行：

### A · 立 spec：`openspec/specs/data-dimensions/spec.md`（新 capability）

明确列出**所有横切数据维度**及其全链路契约：
- 当前覆盖：`tenant_id`、`goal_id`、`persona_id`、`funnel_stage`
- 未来扩展锚点：`knowledge_id`（V1.2）、`session_id`（V1.3）

每个维度回答 4 个问题：
- 写入入口必须带（哪些 endpoint / function 必须接受这个维度）
- 存储层必须列（哪些表 / 文件 schema 必须有这个字段）
- 读取入口必须暴露（哪些 list endpoint 必须提供过滤参数）
- prompt / 消费方必须按维度 scope（哪些 prompt 拼装必须按维度隔离）

### B · 修复 goal_id 全链路断裂（作为 spec 的首个 conformance 验证）

按 spec 顺序修 4 层：
1. `agent_tools/search.py` — 确认/补全采集时 row 含 `goal_id`
2. `storage/local_json.py` — 文件名改为 `spider_xhs_采集结果_{goal_id}_{ts}.xlsx`；`list_collected_data` 加 `goal_id` 过滤
3. `storage/pg_backend.py` — `list_collected_data` 签名加 `goal_id`，WHERE 追加条件
4. `server/routers/notes.py` — 把 query `goal_id` 真正透传给 backend

### C · 加架构守护：`verify_web_skeleton.py` S7 节 + AGENTS.md 模板

- `verify_web_skeleton.py` 新增 S7 节，扫描 backend 方法签名 + router 参数，断言横切维度的全链路完整性
- `openspec/AGENTS.md` 的 proposal 模板新增"横切维度影响审查"必填段

## Out of Scope（明确不做）

- `persona_id` / `knowledge_refs` / `memory_refs` 等其他维度的全链路修复——本 change 只立 spec，**首批 conformance 仅覆盖 goal_id**；其他维度按 spec 排查后单独 change 修复
- 历史采集数据（已存的 xlsx 文件）的回填——只对新采集生效；老数据接受 goal_id=NULL 兼容（list 时不过滤掉，但前端展示加 warning）
- RLS 改造为按 goal_id 切——`goal_id` 不是租户维度，不需要 PG 行级安全，应用层 WHERE 过滤即可
- LocalJsonBackend 其他数据源（generated_content / hot_keywords）的同类修复——本 change 只修 collected_notes，其他作为同类隐患在 spec 中列出待后续 conformance
- 删除老格式文件名（`spider_xhs_采集结果_{ts}.xlsx`）的兼容代码——保留 ≥ 2 个 minor 版本

## Impact

**新增**：
- `openspec/specs/data-dimensions/spec.md`（新 capability spec）
- `verify_web_skeleton.py` S7 节（架构守护断言）
- `tests/test_goal_id_isolation.py`（端到端测试，验证 4 层修复）
- `openspec/AGENTS.md` 新增"横切维度影响审查"段

**修改**：
- `agent_tools/search.py`（采集 row 加 goal_id）
- `storage/local_json.py`（文件名 + list 过滤）
- `storage/pg_backend.py`（list_collected_data 加 goal_id 参数 + WHERE）
- `server/routers/notes.py`（query goal_id 透传）

**Strangler 原则**：
- 老 xlsx 文件名格式（`spider_xhs_采集结果_{ts}.xlsx`）保留兼容读取（goal_id=NULL 视为"未分配"）
- API 层兼容：`goal_id=default` 或不传时，列表返回全 tenant 数据（带 deprecation header）

## Risk

| 风险 | 缓解 |
|---|---|
| 修改 `list_collected_data` 签名是 breaking change，已有调用者炸 | grep 全仓所有调用点（已知：notes router / stream_utils / agents/context.py），DeepSeek 必须把每个调用点的 goal_id 来源都补齐才能 commit |
| 老 xlsx 数据 goal_id=NULL，按 goal 过滤会过滤掉所有老数据 | 兼容策略：API 传 `goal_id=` 空串或不传 → 返回全部（含 NULL）；传具体 goal_id → 严格过滤。前端展示加 "X 条历史数据未分配 goal" 提示 |
| S7 守护断言过于严格，未来其他维度无法接入 | S7 用 spec 驱动（读 `data-dimensions/spec.md` 中的维度清单），不 hardcode；新增维度只需更新 spec + 给 backend 加方法签名 |
| AGENTS.md 加必填段后历史 change 不合规 | 仅对新 change 强制；老 change（archive）不追溯 |

## 与后续 change 的关系

- **本 change 完成是 `content-lifecycle-v2 P2 evidence pool` 的硬前置**：P2 提取的 evidence 会写入 `content_evidence` 表，必须带 goal_id 才能 scoped 注入到 prompt
- **本 change 给 V1.2 knowledge connector 立先例**：`knowledge_id` 接入时直接读 `data-dimensions/spec.md` 的模板，避免重蹈 goal_id 覆辙
- **本 change 给 V1.3 Orchestrator 立先例**：`session_id` 接入同理
