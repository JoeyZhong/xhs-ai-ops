# Tasks: content-lifecycle-v2 内容闭环深化

3 个 phase（P1/P2/P3），每个 phase 独立可合并 PR，每个 phase 完成后由用户验收。

> **前置**：content-lifecycle-v1 已归档；P0 hotfix（commit `226a052`）已合到 main，`prompt_context` aggregator + `packaging_rules` loader 已就位
> **预计周期**：P1 半天 / P2 1-1.5 天 / P3 1-2 天，总计 3-4 天

---

## P1 · Packaging Rules Editor（半天）

> **目标**：`/packaging` 页变成 `memory/_universal/packaging_rules.md` 的可视化编辑器，运营人不再需要改文件

### P1.1 后端 API

- [x] P1.1.1 新建 `server/routers/packaging.py`，注册到 `server/main.py`
- [x] P1.1.2 `GET /api/v1/packaging/rules` → 返回 `{ rules: string, updated_at: iso }`（直接读 `memory/_universal/packaging_rules.md`）
- [x] P1.1.3 `PUT /api/v1/packaging/rules` body `{ rules: string }` → 原子写入文件（写到 `.tmp` 然后 rename），bumps mtime 让 loader 缓存失效
- [x] P1.1.4 PUT 路径加 `IdempotencyRoute` route_class
- [x] P1.1.5 PUT 路径前端校验：必须含 "五大爆文标题公式" 与 "CES" 字符串；否则返回 422 + `ErrorCode.PACKAGING_INVALID`
- [x] P1.1.6 单元测试：GET 返回当前文件内容；PUT 改文件 + loader 立即返回新内容；PUT 缺校验字段 → 422
- [x] P1.1.7 添加 IdempotencyRoute 覆盖到 `verify_web_skeleton.py` S6 的 `covered_prefixes`

### P1.2 前端编辑器页

- [x] P1.2.1 改写 `frontend/app/(main)/packaging/page.tsx`：删除硬编码常量，改为 `useQuery` 读 `/api/v1/packaging/rules`
- [x] P1.2.2 加 markdown 编辑器（`@uiw/react-md-editor` 或自研 textarea + preview，从 `lib/api.ts` 沿用现有模式）
- [x] P1.2.3 加「保存」按钮 → `useMutation` PUT `/api/v1/packaging/rules`，含 `Idempotency-Key` header
- [x] P1.2.4 加「恢复默认」按钮 → 重新 PUT 仓库默认值（前端硬编码 fallback 作为种子，不是 source of truth）
- [x] P1.2.5 422 错误展示提示，告诉运营人缺哪个必填字段
- [x] P1.2.6 浏览器手工验收：改 hook 公式 → 保存 → 内容创作页生成策略 → prompt 应反映新公式

### P1.3 集成验证

- [x] P1.3.1 端到端测试：改 packaging_rules.md → /content/strategy POST → 断言 captured prompt 含新内容
- [x] P1.3.2 `verify_web_skeleton.py` 通过（路由数 ≥ 42，新增 packaging 端点）

### P1.4 Commit

- [x] P1.4.1 P1 提交：`feat(packaging): editable rules API + Next.js editor page`

---

## P2 · Insight Evidence Pool（1-1.5 天）

> **目标**：高 CES 笔记自动提取 `{angle, hook, key_insight}` → 写入 `content_evidence` 表 → /content prompt 注入同 funnel/同 angle 的 top-3 evidence

### P2.1 PG migration

- [x] P2.1.1 新建 `db/migrations/008_content_evidence.sql`：
  ```sql
  CREATE TABLE content_evidence (
    evidence_id    text PRIMARY KEY,
    tenant_id      uuid NOT NULL,
    source_note_id text,
    angle          text,
    funnel_stage   text,
    hook           text,
    key_insight    text,
    ces_score      numeric,
    extracted_at   timestamptz DEFAULT now(),
    raw            jsonb
  );
  CREATE INDEX idx_evidence_tenant_angle ON content_evidence(tenant_id, angle);
  CREATE INDEX idx_evidence_tenant_funnel ON content_evidence(tenant_id, funnel_stage);
  -- RLS 启用，参考 collected_notes 的 policy
  ```
- [x] P2.1.2 跑 `python -m db.migration_runner up`，验证表创建成功

### P2.2 Storage 方法

- [x] P2.2.1 `storage/base.py` Protocol 增 `list_evidence(tenant_id, *, angle=None, funnel_stage=None, limit=3) -> list[dict]`、`upsert_evidence(tenant_id, evidence: dict) -> dict`
- [x] P2.2.2 `storage/pg_backend.py` 实现两方法
- [x] P2.2.3 `storage/local_json.py` 实现两方法（sidecar `config/<tenant>/lifecycle_evidence.json`，沿用 v1 P0 同 pattern）
- [x] P2.2.4 测试：list 按 angle 过滤 / upsert idempotent / RLS 跨租户隔离

### P2.3 IntelAgent 新 tool

- [x] P2.3.1 新建 `agent_tools/intel_evidence.py`：函数 `extract_evidence(note_id, raw_note: dict) -> dict`
  - 调 Kimi 抽取 `{angle, hook, key_insight}`（angle 必须是 5 公式之一）
  - 返回 dict 直接传给 `upsert_evidence`
- [x] P2.3.2 注册到 Tool Registry 为 `intel.extract_evidence`（cost meta + JSON Schema）
- [x] P2.3.3 `agents/policy.py` 加 IntelAgent 白名单允许 `intel.extract_evidence`
- [x] P2.3.4 测试：mock Kimi 返回固定 JSON → 验证 evidence row 正确入库

### P2.4 提取端点 + 批处理

- [x] P2.4.1 新建 `server/routers/intel.py`，注册到 `server/main.py`
- [x] P2.4.2 `POST /api/v1/intel/evidence/extract` body `{ ces_threshold: int, batch_size: int = 10 }`
  - 查询 `collected_notes` WHERE `ces_score > threshold AND note_id NOT IN evidence`
  - batch 调 Kimi（每次 10 条），upsert 到 `content_evidence`
  - 返回 `{ extracted_count, skipped_count, errors }`
- [x] P2.4.3 端点走 IdempotencyRoute + JWT
- [x] P2.4.4 `GET /api/v1/intel/evidence` list endpoint（按 angle / funnel 过滤，分页）

### P2.5 prompt_context 扩 evidence

- [x] P2.5.1 `agent_tools/prompt_context.py::build_strategy_prompt_context` 新增逻辑：
  - 若 `funnel_stage` 已知 → `backend.list_evidence(..., funnel_stage=funnel_stage, limit=3)`
  - 返回 dict 增 `evidence_refs: list[dict]` 字段
- [x] P2.5.2 `server/routers/content.py` 在 `/content/strategy` 和 `/content/generate` prompt 拼装新增 `evidence_block`：
  ```
  ── 同 funnel/同 angle 爆款样本 ──
  - 角度=X, hook="...", 洞察="..."
  ──────────
  ```
- [x] P2.5.3 测试：prompt 内含 evidence 段；evidence 缺失时优雅省略

### P2.6 前端 /insight 改造

- [x] P2.6.1 `/insight` 页加「提取爆款样本」按钮 + ces_threshold 输入
- [x] P2.6.2 调 `POST /api/v1/intel/evidence/extract` 显示进度（轮询 task_result 或同步返回）
- [x] P2.6.3 加 evidence 列表 view（按 angle 分组）

### P2.7 Commit

- [x] P2.7.1 后端 commit：`feat(intel): evidence pool extraction + prompt injection`
- [x] P2.7.2 前端 commit：`feat(insight): evidence extraction UI`

> **P2 完成（2026-06-02）**。commit 链：`b44c836`(数据层) → `ab83bbd`(智能核心) → `73d184e`(前端 UI)。
> 附带：`ad670fe`(LocalJsonBackend sidecar 并发锁) + `91be832`(5 套件对齐多租户+幂等契约，清理 37 个历史失败)。
> 全量回归 **299 passed / 8 skipped / 0 failed**；前端 `pnpm build` 通过；端点烟测 + 端到端实测均通过。
> **PG 相关项（P2.1.2 建表、P2.2.4 RLS 跨租户）按代码 reviewed 验收，集成测试因本机 PG 不可达 skip**，PG 部署后需补跑。

---

## P3 · CES → Playbook 学习闭环（1-2 天）

> **目标**：发布数据回写 → AnalystEvaluator 周报 → playbook.md → ContentAgent prompt + used_angles 三态

### P3.1 used_angles 三态 schema

- [x] P3.1.1 新建 `db/migrations/009_used_angles_tristate.sql`：把 `goals.data->'used_angles'` 从 `["反直觉型"]` 转成 `[{"angle":"反直觉型","status":"unknown","evidence_count":0,"last_ces":null}]`
- [x] P3.1.2 编写迁移脚本 `scripts/migrate_used_angles_to_tristate.py`（幂等，老数据 wrap 成 unknown 状态）
- [x] P3.1.3 `storage/pg_backend.py::load_goals` 和 `save_goals` 处理新 schema；老字符串数组通过 `used_angles_legacy` 字段保留 1 个版本
- [x] P3.1.4 测试：load 老 goal → 自动 wrap；save 新 goal → 反序列化正确

### P3.2 performance 回填端点

- [x] P3.2.1 新建 `server/routers/analytics.py`，注册到 `server/main.py`
- [x] P3.2.2 `POST /api/v1/analytics/performance` body：
  ```json
  { "content_id": "...", "likes": 100, "comments_count": 30, "shares": 20, "collects": 50, "follows": 8 }
  ```
  - 计算 CES 写回 `generated_content.meta.ces_score`
  - 更新 `goals.used_angles[angle]` 的 `last_ces` 和 `evidence_count`
  - 走 IdempotencyRoute
- [x] P3.2.3 测试：发 2 次同 content_id 不同 metrics → CES 是最新值；used_angles 对应 angle 已更新

### P3.3 AnalystEvaluator 扩 playbook 写入

- [x] P3.3.1 在 `agents/evaluators.py::AnalystEvaluator` 周报 logic 后追加 `_update_playbook(tenant_id)` 步骤：
  - 查 `generated_content` 最近 30 天按 angle group，取 top-3 平均 CES 最高 → "已验证爆款"
  - 取 bottom-3 → "已沉底"
  - 写到 `memory/<tenant>/content/playbook.md` 的 `<!-- analyst-auto: v2 -->` 块（保留 `<!-- manual -->` 块不变）
  - 同时把对应 `goals.used_angles` 项的 status 更新为 `validated_hit` / `sunk` / `unknown`
- [x] P3.3.2 `playbook.md` 写入前 backup 到 `playbook.md.bak`（运营人手动恢复入口）
- [x] P3.3.3 测试：mock 数据 → 跑 evaluator → playbook 含 top/bottom 角度 + used_angles 已更新

### P3.4 ContentAgent prompt 读 playbook

- [x] P3.4.1 `agents/content.py::ContentAgent` 的 system prompt 模板新增段：
  ```
  ── 已验证爆款规律（playbook）──
  {playbook_excerpt}
  ──────────
  ```
- [x] P3.4.2 `agent_tools/prompt_context.py::build_strategy_prompt_context` 增 `playbook_summary` 字段（读 `memory/<tenant>/content/playbook.md` 的 `<!-- analyst-auto: v2 -->` 块前 ~500 字符）
- [x] P3.4.3 `/content/strategy` 和 `/content/generate` 的 prompt 注入 playbook_summary（追加到现有 packaging 段之后）
- [x] P3.4.4 测试：playbook 含"top1=反直觉型" → prompt 出现该字符串

### P3.5 前端 used_angles 三态展示

- [x] P3.5.1 `/content` 内容卡片把"⚠️ 此角度已使用过"升级为：
  - `validated_hit` → "✅ 已验证爆款（最近 CES {n}）"
  - `sunk` → "❌ 沉底（最近 CES {n}）"
  - `unknown` → 不显示（之前的负向标记取消）
- [x] P3.5.2 `/goals/[id]` 页新增 used_angles 三态卡片，按 status 排序
- [x] P3.5.3 浏览器手工验收：seed 高/低互动 → 真实 evaluator → 目标对齐「角度表现」卡片三态正确（反直觉型=✅已验证爆款 CES300 / 数字清单型=❌沉底 CES30）。**发现：数据追踪页未接 `/api/v1/analytics/performance`，无纯 UI 驱动三态的路径（记入 backlog）**

### P3.6 Commit

- [x] P3.6.1 schema commit：`feat(goals): used_angles tristate schema + migration`
- [x] P3.6.2 后端 commit：`feat(analytics): performance feedback + playbook auto-update`
- [x] P3.6.3 ContentAgent commit：`feat(content): inject playbook into ContentAgent prompt`
- [x] P3.6.4 前端 commit：`feat(goals): used_angles tristate display`

---

## 总验收（Phase Gate）

P1+P2+P3 全部完成后：

- [x] G.1 跑 `python -m pytest tests/ -q --ignore=tests/test_pg_backend.py` → 全绿
- [x] G.2 跑 `python -X utf8 verify_web_skeleton.py` → 路由数 ≥ 46（v1 基线 + 本 change 新增 ≥ 5）
- [x] G.3 端到端：改 packaging 公式 → 采集 + 提取 evidence → 内容创作生成 → 录入发布数据 → 触发 evaluator → 看到 playbook 写入 + used_angles 状态变化（`verify_g3_e2e.py` 隔离 tmp 后端 + 真实 HTTP 路由跑通 7 步；采集+LLM 抽取为替身 seed，详见脚本头注）
- [x] G.4 性能（预算已重订，2026-06-03 用户拍板：放宽预算+记 backlog 后续优化）。
  > **背景**：原"≤60s"在"5 批**顺序**调用"设计下不现实——实测单批(10条)延迟 22.9/35.7/27.9s（均值~28.8s），×5≈144s，瓶颈 100% 是 LLM 延迟（provider=kimi/实际 DeepSeek，max_tokens=3000/批），非项目代码。
  > **重订验收口径（`verify_g4_perf.py`）**：
  >   - 编排开销（去重/分批/解析/50 次 upsert，我们能控的部分）≤ 2s —— **实测 103ms ✅**
  >   - LLM 调用数 = 5（10 条/批，非 50 次逐条）—— **实测 5 ✅**
  >   - 端到端真实顺序提取 50 条 ≤ 180s —— **实测均值 ~144s ✅**
  > **后续优化（backlog，非本 change 阻塞项）**：并行跑 5 批（当前 `extract_evidence_for_notes` 是顺序 for）可把 50 条压到 ≈单批~29s，真正回到 ≤60s；或降 max_tokens/换快模型。
- [x] G.5 文档：`docs/USER_GUIDE.md` 增「闭环深化使用」章节（包装编辑 + evidence 提取 + 数据回填）—— 新增 §9「内容闭环深化（v2 新增）」
- [x] G.6 归档（2026-06-03）：spec deltas 已合入 `openspec/specs/web-api/spec.md`（packaging / evidence / analytics / prompt 拼装契约 / used_angles 三态 5 个 requirement；`debug_prompt` 因未实现而未合入）；本目录已 `git mv` 到 `openspec/changes/archive/2026-06-03-content-lifecycle-v2/`。

---

## 启动门槛 checklist

- [x] content-lifecycle-v1 已归档（commit 67ecf76 之前）
- [x] P0 hotfix 已合并到 main（commit 226a052）
- [x] `prompt_context` aggregator + `packaging_rules` loader 已就位（commits fbb4010, 45b4e77）
- [x] `tests/test_content_loop_p0.py` 11/11 PASS
- [ ] PG 数据库 reachable（PG mode 才需要，local mode 跳过）
- [ ] 用户确认 proposal + tasks 后启动 P1
