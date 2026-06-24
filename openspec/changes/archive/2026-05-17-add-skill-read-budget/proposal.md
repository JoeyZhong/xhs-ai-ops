# Proposal: 给 `skills.read` 工具加 per-task 读取预算熔断

## Why

现有 Skills 系统（capability `agent-skills`，2026-05-15 上线）存在 **「贪婪读取」死循环风险**：

- `_build_skills_block()` 在 system prompt 注入 skill 索引（name + when_to_use），LLM 只要看见就有**主动 read** 的倾向，即便当前任务不需要。
- `skills.read` 单次返回 skill 全文，无任何上限 —— 一个写得详细的 SOP 文件（3-5 KB）一次性塞进 messages 后，下一轮 LLM 可能因为上下文被污染继续触发另一个 skill 的 read。
- 单次 Agent 运行直接撞 `max_iterations` 或 `task.budget_tokens` 熔断，外在表现是「agent 跑了 8 轮全在读 skill 文件，最终输出空」（与 2026-05-12 修过的 YAML 伪工具调用死循环现象同源，但触发面更宽）。
- 现有 `REASONING_DIRECTIVE`（`agents/base.py:66`）和 skill 索引块对此**完全没有任何硬约束**，只有「when_to_use 匹配时调用」这种软引导。

## What

在 `agent_tools/skills.py` 的 `_read_skill_handler` 层加 **per-task 计数熔断器**，零侵入主循环：

1. **配置驱动阈值**：`config/settings.json` 新增 `skill_read_budget`（默认 `2`），运营可调。
2. **模块级计数器**：`_BUDGET_COUNTERS: dict[task_id, int]`，仅在**成功 read** 后 +1。失败/scope mismatch/skill not found/idempotency 命中均**不占额度**。
3. **生命周期挂钩**：`AgentBase.run()` 在 `try/finally` 末尾调 `clear_budget(task_id)`，进程不会随长期运行膨胀。
4. **熔断返回体**：到达阈值后下一次调用返回带 `[SKILL_BUDGET_EXHAUSTED]` 标签、**自然语言断言** 的 `ok:false` 文案 —— 既保持工具错误协议一致，又借 Attention 高权重压制 LLM 重试。
5. **零主循环改动**：不动 `REASONING_DIRECTIVE`，不动 stuck-detection，不动 system prompt 模板。

## Impact

**修改 capability：**
- `agent-skills` — 在 `skills.read` Tool 的需求基础上新增「读取预算」子需求

**改造现有代码：**
- `config/settings.json` — 新增字段 `skill_read_budget`（默认 2）
- `agent_tools/skills.py` — 新增 `_BUDGET_COUNTERS` / `clear_budget(task_id)` / handler 内的熔断分支
- `agents/base.py` — `run()` 末尾 `finally` 调 `clear_budget(task.task_id)`
- `tests/test_skills.py` — 加 4 个用例（参见 tasks.md §3）

**不影响：**
- `REASONING_DIRECTIVE` 和 system prompt 模板
- stuck-detection / pseudo-tool-call 正则（熔断文案已规避 `skills.read` 字面量）
- `_collect_memory_snapshot` 与 skill 索引块（**纯只读** snapshot 已经是磁盘穿透，参考 design.md §1）
- `agents/policy.py` 的 `allow_patterns`（`skills.read` 仍在白名单内）
- Dashboard Skills 管理页

## Risk

| 风险 | 缓解 |
|------|------|
| 阈值 2 对 Analyst 过严（10-3-1 + 流量异常排查同任务并存场景） | 阈值经 `config/settings.json` 暴露，运营可调；如确需 role 级差异化，下个迭代加 `skill_read_budget_by_role: {analyst: 3, ...}` |
| 模块级 dict 泄漏（task_id 不主动清理） | `AgentBase.run()` 的 `finally` 显式 `clear_budget`；异常退出 finally 仍会执行；进程 SIGKILL 时 dict 随进程消亡 |
| 熔断文案被 stuck-detection 误判为 pseudo tool call | 文案严格避开 `skills.read` 字面量（参见 design.md §3） |
| 熔断文案绑死 scratch_pad，与 `scratchpad_enabled=False` 冲突 | 文案不引用 scratch_pad，直接引导「输出最终答案」（参见 design.md §4） |
| LLM 收到 `ok:false` 后换 name 重试 | `[SKILL_BUDGET_EXHAUSTED]` 标签 + 「严禁更换参数或重复尝试本工具调用」自然语言断言 |
| 跨 DAG 累积预算的需求未来出现 | 本次不导出 `clear_budget` 到 `__init__.py`，留 TODO；Master 调 `submit_dag()` 不直接调 `skills.read`，**单 task 粒度已足够** |

## 开发量估算

- 单 capability delta，3 个核心文件改动 + 1 个 settings 字段 + 4 个测试用例
- 不涉及 LLM 调用模式变化、不涉及前端
- 预计 1 phase 完成（约 1-2 小时实施 + 验证）
