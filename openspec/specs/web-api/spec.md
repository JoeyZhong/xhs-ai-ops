# Spec Delta: web-api

> 新建 capability。所有条款 `## ADDED`。
> 这是 Strangler Fig 迁移的第一份 spec — 立基线 + 立红线。

---

## ADDED Requirement: HTTP API 进程独立

系统 SHALL 提供一个独立的 FastAPI 进程作为 Web/HTTP 接入层，与 Streamlit
（`dashboard.py`）共存。两个进程通过共享文件系统（`config/` / `xhs_data/` /
`memory/` / `config/cookies.db`）协调，互不阻塞。

### Scenario: 双进程共存（Phase 4a 更新）
- **GIVEN** 用户运行 `python -m streamlit run dashboard.py`（端口 8501）
- **AND** 用户运行 `python -m uvicorn server.main:app --port 8000`
- **THEN** 两个进程同时正常运行
- **AND** `STORAGE_BACKEND=local` 时：读 `config/personas.json` / `config/cookies.db`
- **AND** `STORAGE_BACKEND=postgres` 时：共用 PG `cookies` 表（pgcrypto 列加密 + RLS 隔离）

### Scenario: server/ 目录命名约束
- **WHEN** 在仓库根目录扫描后端代码目录
- **THEN** FastAPI 后端代码 MUST 位于 `server/`
- **AND** 不得使用 `api/`（与现有 `apis/` 即 XHS API 封装目录混淆）
- **AND** 不得使用 `web/`（保留给未来 SPA 前端工程）

---

## ADDED Requirement: /health 健康检查端点

系统 SHALL 提供 `GET /api/v1/health` 端点用于服务可用性探测。

### Scenario: 健康检查返回标准结构
- **WHEN** 客户端发起 `GET /api/v1/health`
- **THEN** 返回 HTTP 200
- **AND** 响应体为 JSON 对象 `{"status": "ok", "version": "..."}`
- **AND** 端点无副作用、不读 DB、不调外部 API

### Scenario: URL 含版本号
- **WHEN** 接口 URL 被规划
- **THEN** 路径 SHALL 以 `/api/v1/` 开头
- **AND** 后续 v2 版本可 `/api/v2/...` 平滑共存
- **AND** 反向代理（Nginx / Cloudflare）可按 `/api/*` 一条规则路由

---

## ADDED Requirement: CORS 严格白名单

系统 SHALL 配置 CORS 中间件，**不允许** `allow_origins=["*"]`。
v1 白名单：

```
http://localhost:3000   # Next.js 默认
http://localhost:5173   # Vite 默认
http://localhost:4321   # Astro 默认
```

### Scenario: 白名单内的 Origin 被允许
- **WHEN** 浏览器从 `http://localhost:5173` 发起预检请求
- **THEN** 响应 `Access-Control-Allow-Origin: http://localhost:5173`
- **AND** 业务请求被允许

### Scenario: 白名单外的 Origin 被拒绝
- **WHEN** 浏览器从 `https://evil.example.com` 发起请求
- **THEN** 响应不包含 `Access-Control-Allow-Origin: https://evil.example.com`
- **AND** 浏览器同源策略阻断业务请求

### Scenario: 禁止使用通配符
- **WHEN** grep 仓库 `allow_origins\s*=\s*\[\s*"\*"`
- **THEN** 0 个匹配
- **AND** 即使带 `allow_credentials=False`，仍禁止使用 `*`

---

## ADDED Requirement: 阻塞调用必须 run_in_threadpool（红线）

业务代码（含未来添加的端点 handler）调用任何**同步阻塞**操作时
SHALL 使用 `fastapi.concurrency.run_in_threadpool` 包装。

裸调以下阻塞 API 在 async handler 中是**严重错误**：
- `subprocess.Popen` / `subprocess.run`
- `requests.get` / `requests.post`
- `sqlite3.connect`（含 `cookie_manager.get_cookie` 等）
- `pandas.read_excel` / `pd.DataFrame.to_excel`
- `playwright.sync_api.sync_playwright`
- `PyExecJS` (XHS 签名)

### Scenario: 正确做法
- **GIVEN** 一个 async handler 需要调用 `cookie_manager.get_cookie`
- **WHEN** 编写代码
- **THEN** MUST 写为：
  ```python
  from fastapi.concurrency import run_in_threadpool
  cookie = await run_in_threadpool(get_cookie, account_id)
  ```

### Scenario: 错误做法（禁止）
- **GIVEN** 一个 async handler
- **WHEN** 直接 `cookie = get_cookie(account_id)` 或裸调任何阻塞函数
- **THEN** 这是 spec 违规
- **AND** code review MUST 拒绝

### Scenario: V1.1+ 多端点压测
- **GIVEN** V1.1 已注册 lifecycle 4 个 prefix（topics/calendar/strategies/drafts）+ content + legacy 端点
- **WHEN** 接口被压测
- **THEN** 事件循环不阻塞
- **AND** 业务代码内任何同步阻塞调用 MUST 经 `run_in_threadpool` 包装

---

## MODIFIED Requirement: 新业务端点必须先经 OpenSpec change

> 历史：本 requirement 早期写作 "v1 仅含 /health（红线）"。
> Phase 4a + content-lifecycle-v1（2026-05-27）后，业务端点已正式入驻，红线
> 改为流程约束：所有新业务端点 MUST 先经独立 change 提案。

系统 SHALL 要求所有新增业务端点（含未来的 Goals / Personas / Notes / Skills 等）
先在 `openspec/changes/<name>/` 下完成 proposal / design / tasks 才能合入。已落地
端点（`/api/v1/health`、`/api/v1/topics`、`/api/v1/calendar`、`/api/v1/strategies`、
`/api/v1/drafts`、`/api/v1/content`、`/api/v1/goals`、`/api/v1/personas`、
`/api/v1/notes`、`/api/v1/playbook`、`/api/v1/scheduler`、`/api/v1/dag`、
`/api/v1/agent`、`/api/v1/collect/*`）均有对应 spec / change 支撑。

### Scenario: 业务端点必须有 change
- **WHEN** 新增 `/api/v1/<resource>` 端点
- **THEN** 该端点 MUST 属于某个已批准 change 的 proposal / design / tasks 范围
- **AND** 不得夹带 Streamlit、Orchestrator 或自动发布等未提案能力

### Scenario: 反向防偷加端点
- **WHEN** review 一个 server/main.py 或 server/routers/*.py 的 diff
- **THEN** 新出现的 path/method MUST 能追溯到一个具名 change
- **AND** 未提案的端点 review 应拒绝

---

## ADDED Requirement: 不引入认证（v1 暂缺）→ 已由 Phase 4a 升级为 JWT

~~v1 SHALL 不实现任何认证 / 鉴权机制。/health 是无认证公开端点（监控用）。~~

> **Phase 4a 更新（2026-05-22）**：本 requirement 已被以下 JWT Auth 条款取代。v1 静态 Bearer 已升级为 JWT HS256。

### REMOVED Scenario: v1 端点免认证（已删除）
- ~~**WHEN** 客户端不带任何凭证 `curl http://localhost:8000/api/v1/health`~~
- ~~**THEN** 返回 200~~

### REMOVED Scenario: 业务端点引入认证前不得 ship（已删除）
- ~~**WHEN** 任何 PR 添加非 /health 端点~~
- ~~**THEN** SHALL 同时引入认证机制~~
- ~~**AND** 否则 review 不通过~~

---

## ADDED Requirement: JWT HS256 鉴权（Phase 4a）

系统 SHALL 用 JWT HS256 替代静态 Bearer token，所有 `/api/v1/*` 业务端点（除 `/health`）MUST 通过 JWT 验证。鉴权收口于 `server/auth.py::verify_token`（FastAPI dependency），返回 `AuthContext(tenant_id, is_admin)`。

### Scenario: 缺 Authorization header
- **WHEN** 请求 `/api/v1/goals` 不带 `Authorization` header
- **THEN** 返回 401

### Scenario: JWT 签名伪造
- **WHEN** 请求带 `Authorization: Bearer <fake>` 但签名无法用 `JWT_SECRET` 验证
- **THEN** 返回 401（Invalid token）

### Scenario: JWT 过期
- **WHEN** 请求带 `Authorization: Bearer <token>` 但 `exp` claim 已过
- **THEN** 返回 401（Token expired）

### Scenario: Legacy token 兼容过渡
- **WHEN** `config/settings.json` 中 `auth.allow_legacy_token = true`
- **AND** token 匹配 `api_secret_token`
- **THEN** 返回 `AuthContext(tenant_id="default", is_admin=False)`
- **AND** 过渡期结束后关闭此开关

### Scenario: 合法 JWT 透传 tenant_id
- **WHEN** 请求带合法 JWT，claims `{sub: "<tid>", exp: <future>, ...}`
- **THEN** dependency `verify_token` 返回 `AuthContext(tenant_id=tid, is_admin=...)`
- **AND** router 可用 `auth: AuthContext = Depends(verify_token)` 拿到

---

## ADDED Requirement: JWT 签发 CLI（Phase 4a）

系统 SHALL 提供 `scripts/issue_token.py` CLI，超管可通过命令行为指定租户签发 JWT。

### Scenario: 为新租户签发 token
- **WHEN** 超管运行 `python scripts/issue_token.py --tenant-name shenzhen-pj`
- **THEN** 在 tenants 表插入新行（如不存在）
- **AND** stdout 输出 JWT（HS256 签名，sub = tenant_id, ttl = 24h）

### Scenario: 为已存在租户重签发
- **WHEN** 超管运行 `python scripts/issue_token.py --tenant-id <existing-uuid> --ttl 168`
- **THEN** 不新建 tenant 行，仅签发新 token，ttl=168h（7 天）

---

## ADDED Requirement: SSE 端点接受 query string token（Phase 4a）

SSE 流式端点 SHALL 支持通过 `?token=<jwt>` 传 token，与 Authorization header 二选一。

### Scenario: SSE 用 query string
- **WHEN** 前端 EventSource 连接 `/api/v1/collect/stream?token=<jwt>`
- **THEN** server 解析 query string 取 token 并验证（`verify_token` 内 `request.query_params.get("token")` 优先）
- **AND** 验证逻辑与 header 路径完全一致

> **理由**：浏览器 EventSource API 不支持自定义 header，必须走 query string。

---

## ADDED Requirement: Streamlit 完整保留

本 change SHALL 不修改 `dashboard.py` 任何一行。
Streamlit 路径作为对照基准与回退方案保留。

### Scenario: dashboard.py 零改动
- **WHEN** 本 change 完成
- **THEN** `git diff dashboard.py` 输出为空
- **AND** Streamlit 启动 `python -m streamlit run dashboard.py` 完整可用

---

## ADDED Requirement: 内容生命周期对象 API（V1.1, 2026-05-27）

系统 SHALL 提供 Topic、ContentStrategy、CalendarItem、ContentDraft 的 HTTP API，
使每篇生成内容可追踪 `topic_id`、`strategy_id`、`calendar_item_id`、`knowledge_refs`
和 `memory_refs`。

### Scenario: Topic CRUD
- **WHEN** 客户端创建、读取、更新或归档选题
- **THEN** 系统通过 `/api/v1/topics` 完成操作
- **AND** 响应对象包含 `topic_id`、`status`、`source`、`source_refs`、`rev`

### Scenario: Strategy 关联选题
- **WHEN** 客户端创建内容策略
- **THEN** 请求 MUST 提供 `topic_id` 或 `manual_input_hint`
- **AND** 响应对象包含 `strategy_id`、`topic_id`、`evidence_refs`、`memory_refs`、`knowledge_refs`
- **AND** 两个 anchor 都缺时返回 422 + `error.code = strategy_missing_anchor`

### Scenario: Calendar soft delete
- **WHEN** 客户端删除未发布日历项
- **THEN** 默认执行软删除
- **AND** `calendar_items.status` 变为 `cancelled`
- **AND** `deleted_at` 被写入
- **AND** 默认列表不再展示该条目
- **AND** `?include_deleted=true` 时可见，详情端点始终可见

### Scenario: Draft box
- **WHEN** 客户端查询草稿箱
- **THEN** `/api/v1/drafts` SHALL 支持按 `goal_id` / `persona_id` / `status` / `topic_id` / `date_from` / `date_to` 筛选
- **AND** 草稿可编辑（PUT）、复制（POST /{id}/duplicate）、排期到日历（POST /{id}/schedule）、标记不采用（POST /{id}/reject）

---

## ADDED Requirement: 从选题或日历生成内容（V1.1）

系统 SHALL 支持从 Topic 或 CalendarItem 一键生成内容，生成结果默认进入草稿箱，
并保留生命周期关联字段。

### Scenario: 从选题生成内容
- **GIVEN** 当前 tenant 下存在一个 `topics.topic_id`
- **WHEN** 客户端请求 `POST /api/v1/topics/{topic_id}/generate-content`（或 `POST /api/v1/content/generate` 携带 `topic_id`）
- **THEN** 系统生成 `generated_content` 行
- **AND** 每条草稿包含相同 `topic_id`
- **AND** 若生成或选择了策略，草稿包含 `strategy_id`

### Scenario: 从日历项生成内容
- **GIVEN** 当前 tenant 下存在未生成内容的 `calendar_items.calendar_item_id`
- **WHEN** 客户端请求 `POST /api/v1/calendar/{calendar_item_id}/generate-content`（或 `POST /api/v1/content/generate` 携带 `calendar_item_id`）
- **THEN** 系统生成草稿
- **AND** 草稿包含 `calendar_item_id`
- **AND** 日历项状态更新为 `drafted`

### Scenario: 生成内容暂存
- **WHEN** 客户端请求 `POST /api/v1/content/generate` 且 `persist=true`
- **THEN** 生成结果进入草稿箱
- **AND** 不自动加入日历
- **AND** 刷新页面后仍可通过 `/api/v1/drafts` 查询和编辑

---

## ADDED Requirement: tenant 注入与幂等写入（V1.1）

系统 SHALL 对内容生命周期所有业务写入端点执行 tenant 注入、RLS 隔离和幂等控制。

### Scenario: tenant_id 不允许由客户端传入
- **WHEN** 请求 body 或 query 包含 `tenant_id`
- **THEN** 返回 422
- **AND** 实际 tenant 只能来自 `verify_token` 返回的 `AuthContext.tenant_id`

### Scenario: 写入端点缺少 Idempotency-Key
- **WHEN** 客户端请求 POST、PUT 或 DELETE 写入端点但没有 `Idempotency-Key` header
- **THEN** 返回 428
- **AND** `error.code = missing_idempotency_key`

### Scenario: Idempotency-Key 重放
- **WHEN** 同一 `Idempotency-Key` + 同 payload 重复请求一个 V1.1 写入端点
- **THEN** 中间件返回首次的 2xx 响应（24h cache）
- **AND** 不重复执行 handler（不重复消耗 LLM token / 不重复写库）

### Scenario: Idempotency-Key 与不同 payload 冲突
- **WHEN** 同一 `Idempotency-Key` 重复请求但 payload 不同
- **THEN** 返回 409
- **AND** `error.code = idempotency_conflict`

### Scenario: 4xx / 5xx 不缓存
- **WHEN** 一次写入返回 4xx 或 5xx
- **THEN** 中间件 SHALL NOT 缓存响应
- **AND** 同 key 再次请求时真重跑（让客户端可 fix payload 后重提）

### Scenario: OCC rev 冲突
- **WHEN** 客户端用过期 `rev` 更新 Topic、ContentStrategy、CalendarItem 或 ContentDraft
- **THEN** 返回 409
- **AND** `error.code = rev_mismatch`
- **AND** 响应携带 `current_rev` 字段供客户端重试

---

## MODIFIED Requirement: 验收脚本

系统 SHALL 提供 `verify_web_skeleton.py` + `verify_content_lifecycle.py` 验收脚本，
使用 `fastapi.testclient.TestClient` 验证：

- /health 端点正常响应
- CORS 白名单生效
- 红线规则被遵守（grep 检查 + 阻塞 import 检查）
- V1.1 lifecycle routers 全部注册（topics / calendar / strategies / drafts）
- 所有 V1.1 写入端点挂 `IdempotencyRoute`（含 legacy 例外白名单）
- 端到端用例 1-5 覆盖（PRD §12）

### Scenario: verify_web_skeleton 通过
- **WHEN** 运行 `python verify_web_skeleton.py`
- **THEN** 全部 case 通过（V1.1 后 ≥ 46 项）
- **AND** 退出码为 0

### Scenario: verify_content_lifecycle 通过
- **WHEN** 运行 `python verify_content_lifecycle.py`
- **THEN** 5 个端到端用例全部通过
- **AND** 退出码为 0

### Scenario: V1.1 单元测试
- **WHEN** 运行 `pytest tests/test_topics_router.py tests/test_calendar_router.py tests/test_strategies_router.py tests/test_migrate_goals_json_to_pg.py tests/test_content_gen_lifecycle.py`
- **THEN** 58 个测试全部通过
- **AND** 0 回归

---

## ADDED Requirement: 包装规则可视化编辑 API（content-lifecycle-v2）

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

## ADDED Requirement: 爆款样本提取 API（content-lifecycle-v2）

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

## ADDED Requirement: 发布数据回填 API（content-lifecycle-v2）

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

## ADDED Requirement: 内容生成 prompt 拼装契约（content-lifecycle-v2）

系统 SHALL 在 `/api/v1/content/strategy` 和 `/api/v1/content/generate` 的 LLM prompt 中**强制注入** 6 个上下文段，按下列顺序拼装：

1. 基础信息（brand_position / target_audience / 关键词 / 用户意图）— v0 已有
2. `core_block`（来自 `goal.overall_strategy.core_message`）— content-lifecycle-v1 P0 已加
3. `funnel_block`（来自 `goal.overall_strategy.content_funnel[stage]`）— v1 P0 已加
4. `packaging_rules`（来自 `memory/_universal/packaging_rules.md`）— v1 P0 已加
5. `evidence_block`（来自 `content_evidence` top-3，按 funnel 优先 + angle 次之 + ces_score DESC）— v2 新增
6. `playbook_summary`（来自 `memory/<tenant>/content/playbook.md` 的 `<!-- analyst-auto: v2 -->` 块前 500 字符）— v2 新增

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

## ADDED Requirement: goals.used_angles 三态 schema（content-lifecycle-v2）

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
