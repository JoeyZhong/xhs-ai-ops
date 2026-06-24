# Design Notes: skill-read-budget-throttle

> 本文件记录设计阶段的关键拍板，便于实施时不需要回溯讨论。

## 1. 为什么 snapshot 不需要改

`_collect_memory_snapshot()` 在 `AgentBase.run()` 入口跑一次，被 `_cached_system_prompt` 跨 run 缓存（`agents/base.py:283/306`）。

但 **skill 全文读取已经是磁盘穿透**：
- `agent_tools/skills.py` 的 `_read_skill_handler` → `mem.read_skill(...)` → `agents/memory.py:209` `_scope_path()` 每次重新解析磁盘 → `_read_skill_content()` 现读现给。

冻结的只是 system prompt 里的「索引块」（name + when_to_use 摘要）。Dashboard 新增 skill 在当前 run 内**不会出现在索引里**，但 LLM 也就不知道这个 skill 存在 → 不会去 read → **不构成读到旧内容的 bug**。

**结论**：本次 change 不动 snapshot。索引刷新与跨 run 缓存是另一独立议题。

## 2. 为什么不在 `REASONING_DIRECTIVE` 加硬规则

讨论过的三个方案：

| 方案 | 评估 |
|---|---|
| `REASONING_DIRECTIVE` 加 「单任务 ≤ 1」 | Analyst 多 SOP 复合分析场景会被卡死；规则散在 prompt 里依赖 LLM 自律 |
| 硬规则 + scratch_pad 列 read 理由 | 复杂度高，且依赖 `scratchpad_enabled` flag |
| **工具层计数熔断 + 错误返回** ← **选这条** | 确定性强、改动收口、不依赖 LLM 自律；阈值可配置 |

## 3. 熔断文案的字面量约束（**不要改**）

```
🚨 [SKILL_BUDGET_EXHAUSTED] 本任务的技能读取额度已耗尽。严禁更换参数或重复尝试本工具调用。请直接综合已读取的方法论输出最终答案（AgentResult.success）。
```

逐字解释：

| 元素 | 作用 | **改动风险** |
|---|---|---|
| `🚨 [SKILL_BUDGET_EXHAUSTED]` | 审计 grep 锚点 + LLM Attention 高权重标签 | 改后审计日志、测试断言全要联动改 |
| 「严禁更换参数或重复尝试**本工具调用**」 | 自然语言切断，避免出现 `skills.read` 字面量被 `_YAML_DESC_CALL_RE`/`_JSON_DESC_CALL_RE` 误判为 pseudo tool call（参考 `agents/base.py` 2026-05-12 修复历史） | 改回包含 `skills.read` 字面量 → stuck-detection 误伤 → 死循环 |
| 「请直接综合已读取的方法论输出最终答案」 | **不**引用 `scratch_pad`，避免与 `scratchpad_enabled=False` 配置冲突触发空响应（参考 `agents/base.py:94` `_strip_scratch_pad`） | 改成「请在 scratch_pad 整理」→ flag 关闭时空输出 |
| 「（AgentResult.success）」 | 给 LLM 明确出口语义 | 删掉可能让 LLM 转去调其他工具兜底 |

## 4. 计数粒度与生命周期

- **Key**：`task_id`（`AgentTask.task_id`，每个 Sub Agent 的一次 `run()` 唯一）
- **不用** `goal_id` 或 DAG session id —— 编排层（Master）不直接调 `skills.read`，跨 task 限额不是当前痛点
- **物理落点**：模块级 `defaultdict(int)`，不持久化
- **清理**：`AgentBase.run()` 的 `finally` 调 `clear_budget(task_id)`，异常退出也会走 finally

## 5. 失败路径不计数 —— 为什么

| 失败路径 | 是否计数 | 理由 |
|---|---|---|
| scope/name 缺失 | ❌ | 参数错误，未消耗任何 token |
| scope mismatch | ❌ | 跨 scope 拒绝，未读取磁盘 |
| memory layer 缺失 | ❌ | 内部错误，与 LLM 行为无关 |
| skill not found | ❌ | LLM 在「探索可用 skills」，惩罚等于剥夺试错权 |
| idempotency 缓存命中 | ❌（天然） | 缓存返回不进 handler；同一 skill 读 N 次只算 1 次的语义正确 |
| **`mem.read_skill` 返回有效 content** | ✅ | 唯一的 token 消耗点 |

## 6. 阈值默认 2 的理由

- 实际观察：三个 Sub Agent 的当前 6 个初始 skill，没有一个任务需要并发读 3 个以上 SOP
- 极端场景（Analyst 同时做 10-3-1 + 流量异常排查）只需 read 2 个
- 留运营调参口 `config/settings.json`，发现卡死可临时拉高

## 7. 不在本次 change 范围（明确划线）

- **不**导出 `clear_budget` 给 Master / DAG 编排层
- **不**对 skill 全文做 token 截断
- **不**做 role 级差异化阈值（`skill_read_budget_by_role`）
- **不**改 `_collect_memory_snapshot` 或 `_cached_system_prompt` 缓存策略
- **不**新增 `skills.write` 工具（Analyst → Content 知识沉淀继续走 `memory.write_playbook_entry` 通道）

以上任一需求若被实际场景触发，应**单独提案**。

## 8. 与历史 bug 的关系

本 change 是 2026-05-12 「YAML 伪工具调用死循环」修复（obs 360-407）的**预防性补强**：
- 历史 bug：LLM 输出 YAML 描述的伪 tool call → stuck-detection 来兜底
- 本 change：LLM **真的**在反复调真 tool（不是伪 call）但被 skill 全文污染 → 工具层提前熔断，**不依赖** stuck-detection 兜底

两者互补，stuck-detection 仍是最后防线，本预算机制是前置闸门。
