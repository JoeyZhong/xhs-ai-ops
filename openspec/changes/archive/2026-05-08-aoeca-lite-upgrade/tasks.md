# Tasks: AOECA-Lite 架构升级

5 批独立可合并的 PR。**每批合并前由用户验收**。

---

## P0 · GOAP scratch_pad + 状态感知免疫压缩

> **目标**：Agent 主循环具备结构化推理 + 长会话不爆。
> **解耦于**：Phase 4、agent-skills。
> **可独立合并**：是。

### P0.1 scratch_pad 强制结构化

- [x] P0.1.1 `agents/base.py` 新增 `REASONING_DIRECTIVE` 常量（详见架构文档 §3.3.1）
- [x] P0.1.2 在 `AgentBase.run()` 拼 system prompt 时把 REASONING_DIRECTIVE 放在 SAFETY_DIRECTIVE 之前
- [x] P0.1.3 添加 feature flag `agent_reasoning.scratchpad_enabled`（settings.json，默认 true）
- [x] P0.1.4 单元测试：mock LLM 输出 scratch_pad，主循环不报错；老 LLM 输出（无 scratch_pad）也不报错（向后兼容）

### P0.2 状态感知压缩引擎

- [x] P0.2.1 新文件 `agents/compression.py`：`detect_immune_zone(messages) -> set[int]` 找出最后一轮 assistant + tool 索引
- [x] P0.2.2 `compress_messages(messages, immune_indices, target_tokens) -> messages` 把非免疫区压成摘要（调 Kimi summarize）
- [x] P0.2.3 `count_tokens(messages)` 估算（用 tiktoken 或字符长度兜底）
- [x] P0.2.4 `agents/base.py::run()` 主循环每轮检测 token 数，≥24k 触发压缩
- [x] P0.2.5 压缩前后写 audit `kind=context_compression`
- [x] P0.2.6 边界保护：scratch_pad 内容不进入下轮 messages（仅 assistant.content 进，tool_calls 进，scratch_pad 由 prompt 自带）

### P0.3 验证

- [x] P0.3.1 新建 `verify_phase5_p0.py`，覆盖：
  - immune zone 识别正确（最后一轮 assistant + 所有 tool 配对）
  - 压缩从不切断 tool_call ↔ tool_response 配对
  - 压缩后 messages 总长 < 16k
  - scratch_pad 解析失败/缺失时主循环不崩
  - 30 个 case，全部通过
- [x] P0.3.2 真实 Kimi 跑：构造 "Intel 抓 50 关键词 + 分析" 任务，确认到 30k tokens 不中断
- [x] P0.3.3 docs/ARCHITECTURE.md 加章节「P0: GOAP + Immune Compression」

---

## P1 · OCC + Idempotency + LLMProvider

> **目标**：Memory 写并发安全 / Tool 副作用幂等 / LLM 提供商可替换。
> **依赖**：P0（共用新 verify 框架）。
> **可独立合并**：是（每个子项可拆）。

### P1.1 MemoryLayer v2（OCC）

- [x] P1.1.1 `agents/memory.py::Entry` 数据类加 `rev: int = 0`
- [x] P1.1.2 文件序列化格式扩展：`§id: <id> §rev: <int>` 行头
- [x] P1.1.3 `parse_entries` 解析 rev；`serialize_entries` 写 rev
- [x] P1.1.4 `replace_entry/remove_entry` 加 `expected_rev: int` 参数；不符抛 `WriteConflictError`
- [x] P1.1.5 `add_entry` 自动设 rev=1
- [x] P1.1.6 `read_entry(scope, file, entry_id) -> (body, rev)` 新接口
- [x] P1.1.7 旧 entry（无 §rev:）兼容性：缺省 rev=0，第一次写入升到 1
- [x] P1.1.8 调用方迁移：`agent_tools/memory_tools.py::write_playbook_entry` 内部读 rev → 写 rev，捕获冲突重试 3 次

### P1.2 Idempotency 中间件

- [x] P1.2.1 新文件 `agent_tools/idempotency.py`：`compute_key(tool, args, agent_role, task_id)` SHA256
- [x] P1.2.2 `IdempotencyCache`：内存 dict + 持久化 `xhs_data/idempot/<tenant>.jsonl`，24h TTL
- [x] P1.2.3 `agent_tools/registry.py::invoke()` 在调用前查 cache；命中直接返回
- [x] P1.2.4 副作用工具白名单（仅 `content_gen.*` / `memory.*` / `kimi.complete` 走幂等检查）
- [x] P1.2.5 失败结果不入 cache（只缓存成功）

### P1.3 LLMProvider 抽象

- [x] P1.3.1 新文件 `agent_tools/llm_provider.py`：`LLMProvider` Protocol 定义
- [x] P1.3.2 `KimiProvider`（把现有 `agent_tools/kimi.py::call_kimi*` 内部逻辑迁过来）
- [x] P1.3.3 `MockProvider`（固定响应，用于测试 + Kimi 故障兜底）
- [x] P1.3.4 `FailoverProvider(primary=Kimi, fallback=Mock)` — 限频/超时切到 fallback
- [x] P1.3.5 `agent_tools/kimi.py::call_kimi_with_tools` 改为薄壳调 `_default_provider().call_chat_completions(...)`
- [x] P1.3.6 `settings.json` 加 `llm_provider: "kimi" | "mock" | "failover"` 配置项

### P1.4 验证

- [x] P1.4.1 `verify_phase5_p1.py`：OCC 冲突 / Idempotency 命中 / Provider 切换共 40+ cases
- [x] P1.4.2 手工：开 2 个 Streamlit 实例同时改 playbook，确认冲突被检测
- [x] P1.4.3 手工：删除 Kimi key 启动 Console，确认 Failover 接管不报错

---

## P2 · TaskLedger + Master.submit_dag

> **目标**：多步任务一次提交，自动串联执行。
> **依赖**：P1（TaskLedger 写入需 OCC 保护）。
> **可独立合并**：是（不影响老 submit() 单步路径）。

### P2.1 TaskLedger 数据模型

- [x] P2.1.1 新文件 `agents/task_ledger.py`：`TaskNode` dataclass（id, type, prompt, status, blocked_by[], blocks[], result, created_at, updated_at, rev）
- [x] P2.1.2 `TaskLedger`：CRUD + 持久化 `xhs_data/tasks/ledger_<tenant>.jsonl`
- [x] P2.1.3 拓扑排序 + 死锁检测（A blocks B blocks A）
- [x] P2.1.4 状态机：pending → in_progress → completed | failed | cancelled

### P2.2 Master.submit_dag

- [x] P2.2.1 `agents/master.py::HermesMaster.submit_dag(plan: list[TaskNode]) -> list[TaskResult]` 新接口
- [x] P2.2.2 拓扑排序后顺序执行 + 同层并行（ThreadPoolExecutor max_workers=2）
- [x] P2.2.3 前置任务结果可作为后置任务 prompt 的变量插值（如 `${task-1.content}`）
- [x] P2.2.4 任何 task fail → 后续 blocked task 标 cancelled，整个 dag 标 partial_failure
- [x] P2.2.5 启动时扫 ledger，把 in_progress 强制 cancelled（防 Streamlit 重启残留）

### P2.3 Console 集成

- [x] P2.3.1 dashboard.py Agent Console 加「DAG 模式」tab
- [x] P2.3.2 用户输入高级意图 → Kimi 拆 plan（Master 内置 planner prompt） → 用户确认 plan → submit_dag
- [x] P2.3.3 实时显示 ledger 状态（pending/in_progress/completed 计数）
- [x] P2.3.4 失败 task 可单点重试

### P2.4 FastAPI 接口（可选）

- [x] P2.4.1 `server/main.py` 加 `POST /api/v1/dag` 接收 plan + 异步返回 task_ids
- [x] P2.4.2 `GET /api/v1/dag/<dag_id>` 查询当前状态
- [x] P2.4.3 OpenAPI doc 自动生成（FastAPI 内置）

### P2.5 验证

- [x] P2.5.1 `verify_phase5_p2.py`：拓扑/死锁/状态机/失败传播共 30+ cases
- [x] P2.5.2 手工：「Intel 抓→Analyst 分析→Content 生成 3 篇」一条命令完成
- [x] P2.5.3 手工：第 2 步失败，第 3 步正确变 cancelled

---

## P3 · BackgroundScheduler + Weekly Evaluator

> **目标**：Analyst 主动周报、Cookie 健康检查自动化。
> **依赖**：P2（要写 ledger）。
> **可独立合并**：是（默认关闭，开启后才生效）。

### P3.1 调度器

- [ ] P3.1.1 新文件 `agents/scheduler.py`：包装 APScheduler.BackgroundScheduler
- [ ] P3.1.2 启动时机：dashboard.py 首次加载 + file lock 防多 worker 重启重复
- [ ] P3.1.3 注册 cron：周一 09:00 → AnalystEvaluator；每日 06:00 → cookie_health_check
- [ ] P3.1.4 settings.json 加 `scheduler.enabled: bool` 默认 false（用户首次启用）

### P3.2 AnalystEvaluator + Draft/Review 工作流

- [ ] P3.2.1 新文件 `agents/evaluators.py::AnalystEvaluator`：单次任务，组装 prompt = 「过去 7 天的 audit 摘要 + 性能数据 + 现有 playbook」
- [ ] P3.2.2 通过 Master.submit(AgentTask(type="analyst", prompt=...)) 触发
- [ ] P3.2.3 Analyst Agent 输出 → 自动调 `memory.write_playbook_entry(op=add, status="draft", source="scheduler")` 写本周复盘 entry（id=`weekly-YYYY-MM-DD`）
- [ ] P3.2.4 同时把摘要写到 `xhs_data/weekly_reports/<date>.md` 给用户人读

### P3.2-Draft 用户审阅采纳流（用户确认决策 #4 的强约束）

- [ ] P3.2.D1 `agents/memory.py::Entry` 加 `status: Literal["draft","active","rejected"] = "active"` 与 `source: str = "manual"` 元字段
- [ ] P3.2.D2 entry 文件格式扩展：`§id: <id> §rev: <int> §status: <st> §source: <src>` 行头；解析向后兼容（缺省 status=active）
- [ ] P3.2.D3 `memory.write_playbook_entry` 工具签名加可选 `status` / `source` 参数；BG Evaluator 强制传 `status="draft"`
- [ ] P3.2.D4 `agents/content.py` build_system_prompt 时 **仅注入** `status=active` 的 entry（跳过 draft / rejected）
- [ ] P3.2.D5 `agents/analyst.py` build_system_prompt 仍读全部 status（追溯学习）
- [ ] P3.2.D6 dashboard 「📅 自动化任务」页加「待审阅 draft」列表：每条显示 entry id / 来源 / 创建时间 / body 预览
- [ ] P3.2.D7 三个按钮：**采纳**（status→active, rev+1）/ **驳回**（status→rejected, rev+1）/ **编辑后采纳**（弹编辑框，保存时 status→active, rev+1）
- [ ] P3.2.D8 顶部红条提示：「您有 N 条 AI 自动学习的内容策略待审阅」（点击跳转到 P3.2.D6 列表）
- [ ] P3.2.D9 单元测试：draft entry 不进 Content prompt / 采纳后立即进 / 驳回不再显示

### P3.3 cookie_health_check

- [ ] P3.3.1 新建 cron handler：调一次 `search.collect_notes(keyword="测试", limit=1)`
- [ ] P3.3.2 失败 → 写 `xhs_data/health/cookie_alert.json` + 下次 dashboard 启动顶部红条提示

### P3.4 Console 集成

- [ ] P3.4.1 dashboard 加「📅 自动化任务」侧边栏入口
- [ ] P3.4.2 显示已注册的 cron + 下次触发时间 + 历史运行记录

### P3.5 验证

- [ ] P3.5.1 `verify_phase5_p3.py`：scheduler 启动/停止/cron 触发共 15+ cases
- [ ] P3.5.2 手工：临时改 cron 为 1 分钟后，确认到时 ledger 出现新 task
- [ ] P3.5.3 手工：周一查看，playbook 多出周报 draft entry，且 Content Agent 不读它
- [ ] P3.5.4 手工：在「📅 自动化任务」页点采纳 → 重启 Content session → system prompt 含新 entry
- [ ] P3.5.5 手工：点驳回 → entry 标 rejected → Analyst 仍能读到（追溯）但 Content 看不到

---

## P4 · Subprocess Sandbox-Lite

> **目标**：subprocess 调用强制 timeout + Linux rlimit。
> **依赖**：无。
> **可独立合并**：是。

### P4.1 safe_run 工具

- [ ] P4.1.1 新文件 `xhs_utils/safe_run.py`：`safe_run(cmd, timeout=300, mem_mb=1024) -> CompletedProcess`
- [ ] P4.1.2 Linux：preexec_fn 调 `resource.setrlimit(RLIMIT_AS, mem_mb*1024*1024)`
- [ ] P4.1.3 Windows：仅 timeout（超时 kill）+ 记录警告「内存上限不可用」
- [ ] P4.1.4 超时 → kill_tree（kill 子进程组）+ 抛 `SubprocessTimeoutError`

### P4.2 调用方迁移

- [ ] P4.2.1 `dashboard.py::run_script` 改用 safe_run
- [ ] P4.2.2 `agent_tools/browser_fallback.py` 走 safe_run
- [ ] P4.2.3 `content_generator.py` 调用方加 timeout=600（生成可能慢）
- [ ] P4.2.4 兼容性：保留旧 subprocess.Popen 入口，仅高风险场景强制 safe_run

### P4.3 验证

- [ ] P4.3.1 `verify_phase5_p4.py`：timeout 触发 / kill_tree 完整 / Linux rlimit 生效共 10+ cases
- [ ] P4.3.2 手工 Linux：跑一个故意死循环脚本，5 分钟自动 kill
- [ ] P4.3.3 手工 Windows：同上验证 timeout（无内存上限确认警告）

---

## 归档（5 批全部合并后）

- [ ] 把 5 批 capability spec ADDED 条款合入 `openspec/specs/<capability>/spec.md`
- [ ] 移动整个 change 目录到 `openspec/changes/archive/2026-XX-XX-aoeca-lite-upgrade/`
- [ ] 在 `docs/ARCHITECTURE.md` 加 v2 章节，旧 v1 章节标 [Legacy]
- [ ] 在 `openspec/project.md` 更新「关键模块」表
- [ ] 在 `CLAUDE.md` 顶部更新版本到 v2

---

## 与已有 change 的协调

| 已有 change | 协调 |
|---|---|
| `agent-architecture-refactor`（Phase 1+2+3 已交付，Phase 4 待启动） | 本升级**优先于** Phase 4。Phase 4 的 SupabaseBackend 实现要兼容 P1 的 OCC 接口（meta.rev → Supabase 的 row version） |
| `add-agent-skills`（已 propose 0/23） | 解耦推进。本升级 P0 的 GOAP scratch_pad 落地后，Skills 落地时只需在 `<actions>` 内可声明 `skill_view(<id>)`，无需改 AgentBase |
| `frontend-refactor`（2026-05-07 提前启动） | 与 P3 / P4 **并行**推进，互不阻塞。**协调点**：P3.2.D1-D5（draft review backend）必须先于 frontend-refactor F3.6（draft review UI），P3.3 cookie 健康提示 backend 与 F3.6.1 UI 互为前置。详见 `openspec/changes/frontend-refactor/tasks.md`。 |

---

## 总进度跟踪

```
P0 │ ████████████████████ │ 13/13 ✅
P1 │ ████████████████████ │ 22/22 ✅
P2 │ ████████████████████ │ 19/19 ✅
P3 │ ░░░░░░░░░░░░░░░░░░░░ │ 0/26  (含 Draft/Review 流 9 + 新增手工验证 2)
P4 │ ░░░░░░░░░░░░░░░░░░░░ │ 0/11
归档│ ░░░░░░░░░░░░░░░░░░░░ │ 0/5

合计 54 / 96
```
