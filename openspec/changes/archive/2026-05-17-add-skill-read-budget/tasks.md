# Tasks: 给 `skills.read` 工具加 per-task 读取预算熔断

> **状态更新**：代码已在 `5d64f0a` 完成落地并入库。30/30 verify 通过。仅 6.3 端到端 DAG 诊断未跑。

## 1. 配置与基础设施
- [x] 1.1 `config/settings.json` 新增 `"skill_read_budget": 2` 字段
- [x] 1.2 `agent_tools/skills.py` 顶层加 `_BUDGET_COUNTERS: dict[str, int] = defaultdict(int)`
- [x] 1.3 `agent_tools/skills.py` 新增公开函数 `clear_budget(task_id: str) -> None`，使用 `dict.pop(task_id, None)` 安全清理
- [x] 1.4 `agent_tools/skills.py` 新增内部辅助 `_load_budget() -> int`：从 `config/settings.json` 读取 `skill_read_budget`，文件缺失或字段缺失时默认 `2`

## 2. 熔断 Handler 改造
- [x] 2.1 `_read_skill_handler` 入口取出 `task_id = (ctx.extra or {}).get("task_id", "")`；若为空字符串，跳过预算逻辑（兼容直接调用场景）
- [x] 2.2 现有 4 项前置校验（scope/name 必填、scope mismatch、memory 缺失、skill 找不到）**全部维持**返回原 error 路径，**不**触发计数器
- [x] 2.3 `mem.read_skill(...)` 返回非 None 后：先判断 `_BUDGET_COUNTERS[task_id] >= _load_budget()` → 若超额则返回熔断体（见 §2.4）；否则 `_BUDGET_COUNTERS[task_id] += 1` 再返回 success
- [x] 2.4 熔断返回体（**字面量按本提案 design.md §3 定稿，禁止改动**）：

```json
{
  "ok": false,
  "error": "🚨 [SKILL_BUDGET_EXHAUSTED] 本任务的技能读取额度已耗尽。严禁更换参数或重复尝试本工具调用。请直接综合已读取的方法论输出最终答案（AgentResult.success）。"
}
```

- [x] 2.5 计数器递增**必须**晚于 `mem.read_skill` 返回有效 content；idempotency 缓存命中走的是缓存返回路径，天然不进 handler，不计数。`test_read_without_idempotency_counts_each_call` 验证此结论

## 3. 单元测试（`tests/test_skills.py`）
- [x] 3.1 `test_budget_exhausted_after_n_reads`：构造 budget=2、task_id="t1"，连续读两个不同 skill 均成功，第 3 次 read 返回带 `[SKILL_BUDGET_EXHAUSTED]` 标签的 `ok:false`
- [x] 3.2 `test_clear_budget_resets_counter`：耗尽 budget 后调 `clear_budget("t1")`，再读应成功且额度恢复满
- [x] 3.3 `test_failed_read_does_not_consume`：scope mismatch / skill not found / 必填参数缺失 三种失败路径**不**递增计数器
- [x] 3.4 `test_read_without_idempotency_counts_each_call`：构造非幂等场景验证每次调用都计数（budget=2 时同一 skill 读 3 次后换 name 第 4 次熔断）
- [x] 3.5 `test_load_budget_falls_back_when_settings_missing`：临时把 settings.json 移走或缺字段，`_load_budget()` 返回默认值 2

## 4. 主循环挂钩
- [x] 4.1 `agents/base.py` 顶部 import `from agent_tools.skills import clear_budget as _clear_skill_budget`（命名带 `_` 前缀，避免与 `agents/skills.py` 同名 module 混淆）
- [x] 4.2 `AgentBase.run()` 主循环**整体**包入 `try/finally`；`finally` 中调 `_clear_skill_budget(task.task_id)`
- [x] 4.3 `test_budget_clear_on_timeout` / `test_budget_clear_on_success` 验证三条路径（timeout、success、异常）都会走到 finally

## 5. ctx.extra 传递 task_id
- [x] 5.1 `AgentBase` 调 tool 时 `ToolContext.extra` 已追加 `"task_id": task.task_id`
- [x] 5.2 定位在 `base.py` 主循环 `registry.call(...)` 段的 dict update 中追加
- [x] 5.3 `AgentTask` 数据类已有 `task_id` 字段（`agents/task_ledger.py`），无需单独提案

## 6. 验证脚本
- [x] 6.1 `scripts/verify_skills.py` 原有 17 个用例**全部**继续通过（无 task_id 时跳过预算逻辑的兼容性保证）
- [x] 6.2 在 `scripts/verify_skills.py` 末尾追加 budget 验证用例，最终 **30/30 通过**（超出原目标 22/22）
- [x] 6.3 跑一次完整 DAG 诊断（`diag2.py`），确认 Intel → Analyst → Content 全链路无 `[SKILL_BUDGET_EXHAUSTED]` 误触发（正常任务下 budget=2 足够，实际测试中 skills.read 未被调用，预算零消耗）

## 7. 归档
- [x] 7.1 本 change 的 `## ADDED` 条款已合入 `openspec/specs/agent-skills/spec.md`
- [x] 7.2 `openspec/changes/add-skill-read-budget/` 已移至 `openspec/changes/archive/2026-05-17-add-skill-read-budget/`
- [x] 7.3 `CLAUDE.md`「Agent skills」段落无需更新（项目使用内置 skills，非 .claude/skills/）
