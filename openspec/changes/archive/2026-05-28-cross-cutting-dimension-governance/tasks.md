# Tasks: cross-cutting-dimension-governance

3 个 phase，**严格串行**（每个 phase 完工 + 测试绿才能进下一个）。

> **前置**：P1 Step 3 完成（避免 verify_web_skeleton.py 冲突）
> **执行**：Opus 设计已完成（proposal + design + spec 模板）；DeepSeek 一棒执行 A / B / C
> **预计周期**：1 天

---

## A · 立 spec：data-dimensions capability（半小时，DeepSeek）

### A.1 spec 文件落盘

- [ ] A.1.1 新建 `openspec/specs/data-dimensions/spec.md`，按 `design.md §5` 的模板写
- [ ] A.1.2 spec 首批覆盖 4 个维度：
  - `tenant_id`（应用面：所有）— 已实际守护，本 spec 仅追认现状
  - `goal_id`（应用面：采集 / 内容生成 / 选题 / 策略 / 草稿）— **本 change 的首个 conformance**
  - `persona_id`（应用面：内容生成 / 人设管理）— spec 立但**不在本 change 验证**（标记为 future）
  - `funnel_stage`（应用面：选题 / 日历 / 策略）— spec 立但 P0/P1 已部分覆盖，本 change 不验证
- [ ] A.1.3 spec 末尾追加 "Future Dimensions" 段，列出 `knowledge_id` / `session_id` 作为锚点

### A.2 commit

- [ ] A.2.1 commit message: `docs(spec): introduce data-dimensions capability spec`
- [ ] A.2.2 only stage `openspec/specs/data-dimensions/spec.md`

---

## B · 修复 goal_id 4 层断裂（约半天，DeepSeek）

按依赖顺序：采集源 → 存储层 → 读取层 → API 层。每步先写测试再改实现。

### B.1 grep 全仓所有 `list_collected_data` / `save_collected_data` 调用点

- [ ] B.1.1 用 Grep 工具找出所有调用点，列在本 task 注释里供后续比对
- [ ] B.1.2 关键预期调用点：
  - `server/routers/notes.py:56` (list)
  - `server/stream_utils.py` (save，需查具体行)
  - `agent_tools/search.py` (save，需查具体行)
  - `agents/context.py` (list)
  - 测试文件若干

### B.2 采集源：`agent_tools/search.py`

- [ ] B.2.1 读 `agent_tools/search.py` 确认 `collect_for_keyword` / `collect_batch` 函数签名是否接受 `goal_id`
- [ ] B.2.2 若不接受 → 函数签名加 `goal_id: str` 必填参数
- [ ] B.2.3 在生成 df 时，每行追加 `goal_id` 列（值为入参）
- [ ] B.2.4 调 `backend.save_collected_data(...)` 时确保 df 含 goal_id
- [ ] B.2.5 测试：mock backend，传 goal_id="goal_001" → 验证 backend.save_collected_data 收到的 df 含 goal_id 列且值正确

### B.3 LocalJsonBackend：`storage/local_json.py`

- [ ] B.3.1 `save_collected_data` 改文件名为 `spider_xhs_采集结果_{goal_id_safe}_{ts}.xlsx`
  - 引入 `_sanitize_goal_id(gid)`：替换 `/`、`\`、空格 → `_`；空字符串 → `unassigned`
  - goal_id 从 `meta.get("goal_id")` 取；meta 不传 goal_id 时用 `unassigned`（不报错，但 verify 守护会捕获不合规调用）
- [ ] B.3.2 `list_collected_data` 签名增加 `goal_id: Optional[str] = None`
  - 不传或为 None：glob `spider_xhs_采集结果_*.xlsx`（含老格式）
  - 传具体 goal_id：glob `spider_xhs_采集结果_{goal_id_safe}_*.xlsx`（严格过滤，老格式跳过）
  - 仍保留 `since: datetime` 过滤
- [ ] B.3.3 测试：
  - 写入 2 条记录（goal_001 / goal_002）→ 文件名分别带前缀
  - list 不带 goal_id → 返回 2 条
  - list goal_id="goal_001" → 返回 1 条
  - list goal_id="goal_001" + 历史老格式文件存在 → 老文件被跳过（不返回）

### B.4 PgBackend：`storage/pg_backend.py`

- [ ] B.4.1 `list_collected_data` 签名加 `goal_id: Optional[str] = None`
- [ ] B.4.2 WHERE 子句条件构造：`where = ["tenant_id = %s", "collected_at >= %s"]`；若 goal_id 非 None 追加 `where.append("goal_id = %s")` + `params.append(goal_id)`
- [ ] B.4.3 测试：用 `test_pg_backend.py` 现有 fixture，写入 2 条不同 goal_id → list goal_id="goal_001" 只返回 1 条
- [ ] B.4.4 若 PG 测试环境不可用（无连接），跳过 PG 测试但保留代码改动，commit 时在 message 写明 "PG test skipped, code change reviewed only"

### B.5 API 层：`server/routers/notes.py`

- [ ] B.5.1 兼容策略：query `goal_id="default"` 或空串或不传 → 视为 None 传给 backend；具体 goal_id 透传
- [ ] B.5.2 `_run()` 内 `backend.list_collected_data(auth.tenant_id, since=since, goal_id=goal_id if goal_id and goal_id != "default" else None)`
- [ ] B.5.3 测试 `tests/test_notes_router.py`（新建或已有）：
  - GET /api/v1/notes?goal_id=goal_001 → backend 收到 goal_id="goal_001"
  - GET /api/v1/notes?goal_id=default → backend 收到 goal_id=None
  - GET /api/v1/notes → backend 收到 goal_id=None

### B.6 端到端集成测试

- [ ] B.6.1 新建 `tests/test_goal_id_isolation.py`，1 个 test：
  - setup：LocalJsonBackend 写 2 个 goal 各 1 条采集记录
  - 启 TestClient + JWT
  - GET /api/v1/notes?goal_id=goal_001 → 仅返回 1 条且属于 goal_001
  - GET /api/v1/notes?goal_id=goal_002 → 仅返回 1 条且属于 goal_002
  - GET /api/v1/notes → 返回 2 条
- [ ] B.6.2 跑全量 lifecycle 相关测试，确保无回归

### B.7 commit

- [ ] B.7.1 commit message: `feat(storage): goal_id full-chain isolation for collected_notes (conformance for data-dimensions spec)`
- [ ] B.7.2 stage 文件：agent_tools/search.py / storage/local_json.py / storage/pg_backend.py / server/routers/notes.py / tests/test_goal_id_isolation.py / tests/test_notes_router.py（如有）
- [ ] B.7.3 server/errors.py 不动（本 change 不引入 MISSING_GOAL_ID，因为读路径宽松；如果将来加严写路径再加 code）

---

## C · 架构守护：verify S7 节 + AGENTS.md 模板（约 1.5 小时，DeepSeek）

### C.1 verify_web_skeleton.py 新增 S7 节

- [ ] C.1.1 在 S6 节之后追加 S7 节，标题 "横切数据维度守护"
- [ ] C.1.2 实现"7.1 backend 签名守护"：
  - 解析 `openspec/specs/data-dimensions/spec.md`，提取 conformance 维度（首批只有 goal_id；其他维度标 `future` 跳过）
  - 用 inspect 读 `storage.base.StorageBackend` Protocol 中相关方法签名
  - 断言：`list_collected_data` 参数列表必须含 `goal_id`
- [ ] C.1.3 实现"7.2 routes 参数守护"：
  - 从 spec 提取需要暴露 goal_id query 的 endpoint 清单：`/api/v1/notes`
  - 用 FastAPI route 反射断言 endpoint 的 query 参数清单含 `goal_id`
- [ ] C.1.4 不实现"7.3 prompt scope 守护"（design.md §3 已说明留 P2/P3）
- [ ] C.1.5 跑 `python -X utf8 verify_web_skeleton.py` → 期望 48/48（v1 47 + S7 新增至少 1 项）

### C.2 openspec/AGENTS.md 新增"横切维度影响审查"必填段

- [ ] C.2.1 读 `openspec/AGENTS.md` 当前的 proposal 模板段
- [ ] C.2.2 在 "Spec 写法约定" 之前插入新段 "横切维度影响审查（proposal 必填）"，内容：

```markdown
## 横切维度影响审查（proposal 必填）

每个 proposal.md 必须含此段，对所有已立维度（见 `openspec/specs/data-dimensions/spec.md`）勾选：

- [ ] tenant_id: 已保留 / 不涉及 / 新增依赖
- [ ] goal_id: 已保留 / 不涉及 / 新增依赖
- [ ] persona_id: 已保留 / 不涉及 / 新增依赖
- [ ] funnel_stage: 已保留 / 不涉及 / 新增依赖

新增数据维度时，必须在 `data-dimensions/spec.md` 中新增 `## Requirement`，并把字段加进本清单。
```

- [ ] C.2.3 不追溯历史 change（archive 不动）

### C.3 commit

- [ ] C.3.1 commit message: `feat(governance): verify_web_skeleton S7 + AGENTS.md cross-cutting review checklist`
- [ ] C.3.2 stage: verify_web_skeleton.py + openspec/AGENTS.md

---

## 总验收

- [ ] G.1 测试：`python -m pytest tests/test_goal_id_isolation.py tests/test_notes_router.py tests/test_local_json_lifecycle_backend.py tests/test_content_loop_p0.py tests/test_packaging_router.py -v` 全绿
- [ ] G.2 守护：`python -X utf8 verify_web_skeleton.py` ≥ 48/48
- [ ] G.3 端到端手工：
  - 触发采集（goal_001）→ 查文件名带 `goal_001_` 前缀
  - GET /api/v1/notes?goal_id=goal_001 → 只返回 goal_001 数据
  - GET /api/v1/notes?goal_id=goal_002 → 不返回 goal_001 数据
- [ ] G.4 归档：本 change 移到 `openspec/changes/archive/<date>-cross-cutting-dimension-governance/`，spec delta 合入 `openspec/specs/data-dimensions/spec.md`（已经在 specs/ 下，归档时把 changes/ 下的复制覆盖式合入）

---

## 启动门槛 checklist

- [x] Opus 设计完成（proposal + design + tasks + handoff）
- [ ] P1 Step 3 完成（避免冲突）
- [ ] DeepSeek 阅读 handoff（`docs/handoff/cross-cutting-deepseek.md`）
