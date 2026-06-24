# Spec Delta: agent-architecture

> AOECA-Lite v2 架构升级 capability。所有条款 `## ADDED`。
> 覆盖 P0 GOAP/压缩、P1 OCC/幂等/LLMProvider、P2 DAG/TaskLedger、P3 调度器/周报、P4 子进程沙箱。

---

## ADDED Requirement: GOAP 结构化推理（scratch_pad）

系统 SHALL 在 AgentBase.run() 的 system prompt 中注入 REASONING_DIRECTIVE，强制 Sub Agent 每轮输出结构化 scratch_pad（观察→思考→计划→行动）。

### Scenario: scratch_pad 解析成功
- **WHEN** LLM 返回含 `<scratch_pad>` 区块的响应
- **THEN** AgentBase 提取并解析为结构化内容
- **AND** scratch_pad 不进入下一轮 messages（仅 assistant.content + tool_calls 进）

### Scenario: scratch_pad 向后兼容
- **WHEN** 旧 LLM 输出不含 `<scratch_pad>`
- **THEN** 主循环不报错，正常处理 tool_calls / content

---

## ADDED Requirement: 状态感知免疫压缩

系统 SHALL 在 token 数 ≥ 12k 时触发上下文压缩，保护最后一轮 assistant + tool 配对（免疫区）不被切断。

### Scenario: 免疫区识别正确
- **GIVEN** messages 含多轮 assistant/tool 交替
- **WHEN** detect_immune_zone() 被调用
- **THEN** 返回最后一轮 assistant 及其所有配对的 tool_response 索引

### Scenario: 压缩不切断 tool 链
- **WHEN** compress_messages() 压缩非免疫区
- **THEN** 免疫区内 tool_call ↔ tool_response 配对完整保留
- **AND** 压缩后 messages 总长 < 16k

---

## ADDED Requirement: Memory OCC（乐观并发控制）

MemoryLayer Entry SHALL 含 rev 字段；replace/remove_entry 须传入 expected_rev，不符时抛 WriteConflictError。

### Scenario: 冲突检测
- **GIVEN** entry rev=2
- **WHEN** 两个并发写分别传 expected_rev=2
- **THEN** 先写者成功（rev→3），后写者抛 WriteConflictError

### Scenario: 向后兼容
- **GIVEN** 旧 entry 无 §rev: 行头
- **WHEN** 首次写入
- **THEN** 自动设为 rev=1

---

## ADDED Requirement: Tool 幂等中间件

registry.invoke() SHALL 对副作用工具（content_gen.* / memory.* / kimi.complete）查 SHA256 缓存，24h 内相同 args 命中即返回缓存结果，不重复调用。

### Scenario: 命中缓存
- **GIVEN** 10 分钟内用相同 args 调用 content_gen.generate_batch
- **WHEN** 第二次调用
- **THEN** 直接返回缓存结果，不消耗 LLM quota

### Scenario: 失败不入缓存
- **GIVEN** 某次调用抛异常
- **THEN** 结果不入缓存，下次相同 args 仍会重试

---

## ADDED Requirement: LLMProvider 抽象

系统 SHALL 提供 LLMProvider Protocol，支持 KimiProvider / MockProvider / FailoverProvider 三种实现，settings.json 可切换。

### Scenario: 故障切换
- **GIVEN** FailoverProvider(primary=Kimi, fallback=Mock)
- **WHEN** Kimi 限频/超时
- **THEN** 自动切到 MockProvider，Agent 不崩溃

### Scenario: 无 Kimi key 启动
- **GIVEN** settings.json 中无 Kimi API Key
- **WHEN** 启动 Agent Console
- **THEN** MockProvider 接管，所有请求返回固定响应

---

## ADDED Requirement: DAG 任务编排（TaskLedger）

HermesMaster SHALL 提供 submit_dag(plan) 接口，接收 TaskNode 列表，自动拓扑排序后串行执行，支持变量插值和失败传播。

### Scenario: 自动拓扑执行
- **GIVEN** plan = [A, B(blocked_by=A), C(blocked_by=A)]
- **WHEN** submit_dag(plan)
- **THEN** A 先执行，B/C 在 A 完成后按拓扑序执行

### Scenario: 失败传播
- **GIVEN** DAG 中节点 B 执行失败
- **THEN** B 的下游节点全部标 cancelled
- **AND** 整个 DAG 状态为 partial_failure

### Scenario: 重启恢复
- **GIVEN** Streamlit 重启时 ledger 中有 in_progress 节点
- **WHEN** 启动扫描 ledger
- **THEN** in_progress 节点强制标 cancelled

---

## ADDED Requirement: BackgroundScheduler + AnalystEvaluator

系统 SHALL 提供 APScheduler 包装，支持 cron 注册，周一 09:00 自动触发 AnalystEvaluator 生成周报 draft entry。

### Scenario: 自动周报
- **GIVEN** scheduler.enabled=true
- **WHEN** 周一 09:00 到达
- **THEN** AnalystEvaluator 组装过去 7 天 audit 摘要
- **AND** 输出写入 memory/default/content/playbook.md（status=draft, source=scheduler）
- **AND** 同时生成 xhs_data/weekly_reports/<date>.md 供人读

### Scenario: Draft 不入 Content prompt
- **GIVEN** playbook 中有 status=draft 的 entry
- **WHEN** ContentAgent 构建 system prompt
- **THEN** 仅注入 status=active 的 entry，跳过 draft/rejected

---

## ADDED Requirement: Subprocess Sandbox-Lite

系统 SHALL 提供 safe_run() 包装 subprocess，强制 timeout，Linux 下支持 rlimit AS 内存上限，超时后 kill_tree 杀子进程组。

### Scenario: 超时自动 kill
- **GIVEN** 脚本死循环
- **WHEN** safe_run(cmd, timeout=300)
- **THEN** 300 秒后抛 SubprocessTimeoutError，进程及其子进程被 kill

### Scenario: Linux 内存上限
- **GIVEN** Linux 环境
- **WHEN** safe_run(cmd, mem_mb=1024)
- **THEN** preexec_fn 设置 RLIMIT_AS = 1GB，超限时 OOM kill

### Scenario: Windows 降级
- **GIVEN** Windows 环境
- **WHEN** safe_run(cmd, mem_mb=1024)
- **THEN** 正常执行 timeout 保护，记录 warning「rlimit AS unavailable」

---

## ADDED Requirement: Draft 审阅采纳流

系统 SHALL 提供 playbook draft 的 CRUD 接口：list drafts / accept / reject / edit。

### Scenario: 采纳后生效
- **GIVEN** draft entry id=weekly-2026-05-08
- **WHEN** 调用 accept → status 变为 active
- **THEN** ContentAgent 下次 system prompt 包含该 entry

### Scenario: 驳回后 Analyst 仍可见
- **GIVEN** rejected entry
- **WHEN** AnalystAgent 构建 system prompt
- **THEN** 仍读到该 entry（status=all，追溯学习）
- **AND** ContentAgent 看不到（仅 status=active）
