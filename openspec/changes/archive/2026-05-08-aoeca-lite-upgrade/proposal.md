# Proposal: AOECA-Lite 架构升级（Spider_XHS v2）

> **依据文档**：`architect/Spider_XHS v2 架构设计.md` + `architect/Hermes 与 Openclaw 架构对比设计.md`
> **状态**：✅ 已确认（2026-05-03 用户拍板，可开工）— P0 → P4 顺序实施
> **预计周期**：5 批 PR，每批 0.5–1.5 天

## 用户确认的范围（2026-05-03）

| # | 决策点 | 答复 | 备注 |
|---|---|---|---|
| 1 | P0 优先 | ✅ 接受 | |
| 2 | OCC 提前 | ✅ 接受 | |
| 3 | JSONL ledger | ✅ 够用 | |
| 4 | BG 自动写 playbook | ✅ 接受 | **附加约束**：entry 默认 `status=draft`，Console 提供「审阅采纳」按钮 |
| 5 | Skills 系统 | ❎ 解耦 | `add-agent-skills` 不纳入本次升级 |

---

## Why

参照 Hermes Agent 与 OpenClaw 范式的对比研究（`architect/Hermes 与 Openclaw 架构对比设计.md`），对 Spider_XHS 当前架构做了 9 项追问（详见 `architect/Spider_XHS v2 架构设计.md` §一），其中 **2 项 🔴 必修 + 4 项 🟡 应修**：

🔴 **必修**：
1. **Sub Agent 主循环无结构化推理**（缺 GOAP scratch_pad）— Kimi 直接调工具，规划质量靠运气
2. **上下文窗口完全没有压缩**（且若加朴素压缩会切断 tool_call/tool_response 配对，复刻 Hermes #647 bug）— Agent 任务跑过 25k tokens 必然失败

🟡 **应修**：
3. Master 单步调度，无 Task Ledger / DAG，多步任务靠用户手按按钮
4. 共享 Memory 无 OCC 版本戳，写冲突靠「Streamlit 单用户」假设掩盖（Phase 4 必出问题）
5. Tool 副作用无 Idempotency Key，Streamlit rerun 会触发重复写入
6. Phase 3 反馈闭环只是被动的（无 Background Evaluator 自动周报）

⚪ **可缓**（已识别但本次不全做，留接口）：
7-9. Skills 渐进式披露 / 多 LLM Provider / 子进程沙盒强化

继续在当前架构上叠加 Phase 4 多租户会放大上述问题——Phase 4 之前必须把单 Agent 主循环的鲁棒性补齐。

## What

按 5 批 PR 渐进升级：

| 批次 | 范围 | 体量 | 与现有 Phase 关系 |
|---|---|---|---|
| **P0**: GOAP scratch_pad + 状态感知免疫压缩 | `agents/base.py` + 新文件 `agents/compression.py` | M (~600 LOC) | 解耦于 Phase 4，可立即开工 |
| **P1**: OCC（MemoryLayer v2）+ Idempotency + LLMProvider 抽象 | `agents/memory.py` + `agent_tools/registry.py` + `agent_tools/llm_provider.py`（新） | M (~500 LOC) | **Phase 4 前置准备**（OCC 是多租户硬前提） |
| **P2**: TaskLedger + Master.submit_dag | `agents/master.py` + 新文件 `agents/task_ledger.py` + storage/ledger 表 | L (~800 LOC) | 给 FastAPI strangler 暴露 HTTP 接口 |
| **P3**: BackgroundScheduler + Weekly AnalystEvaluator | 新文件 `agents/scheduler.py` | S (~300 LOC) | 依赖 P2（要写入 ledger） |
| **P4**: Subprocess Sandbox-Lite（timeout + rlimit） | 新文件 `xhs_utils/safe_run.py` + 调用方迁移 | S (~250 LOC) | 解耦，可与 P0 并行 |

**总计** ~2,450 LOC 新增 / 修改，分 5 个独立可验证 PR。

详细任务拆分见 `tasks.md`。

## What Stays the Same（不动的部分）

- 三 Sub Agent 角色不变（Intel/Content/Analyst）
- ToolPolicy 写权限矩阵不变
- 现有 11 个 Tool 的 handler 不变（仅 registry 加中间件）
- Streamlit 8 页面 UI 不变（Agent Console 加新「DAG 任务」入口）
- 老脚本 `run_search.py` / `content_generator.py` 等不动
- Phase 4 Supabase 计划不动（继续待启动）
- `add-agent-skills` propose 不动（解耦推进）

## Out of Scope（明确不做）

详见 `architect/Spider_XHS v2 架构设计.md` §六。

要点：
- ❌ Docker 容器化
- ❌ NATS / Kafka / Redis
- ❌ DSPy / GEPA 自动重写 skills
- ❌ WebSocket 双向网关
- ❌ 真接第二家 LLM 提供商（仅做接口抽象 + Mock）
- ❌ Web 前端替换 Streamlit（Phase 5+）

## Decision Log（关键设计决策）

| # | 决策 | 备选 | 取舍 |
|---|---|---|---|
| D1 | scratch_pad 不进入下一轮 messages | 完整保留 | 控 token 成本 |
| D2 | 压缩阈值 24k tokens（72% of 32k） | 28k (87%) | 给 system prompt + immune zone 留 buffer |
| D3 | TaskLedger 用 JSONL，不上 SQLite | SQLite | 单用户体量 + 跟现有 audit 一致 |
| D4 | OCC 用 meta.rev int 自增，非 ETag/CRDTs | CRDT | 无并发合并语义需求 |
| D5 | BackgroundScheduler 内嵌进程 | 独立进程 | 单用户、不复杂 |
| D6 | LLMProvider 接口先做 Kimi+Mock，不接 DeepSeek | 直接接 | 等 Kimi 真出问题再接，避免预测性工程 |
| D7 | Subprocess sandbox 仅 timeout+Linux rlimit | Docker | 跨平台复杂度太高 |

## Risks

| 风险 | 缓解 |
|---|---|
| GOAP 让输出变长 → token 成本 +20-30% | scratch_pad 仅当轮可见；temperature 调到 0.4 |
| 压缩算法漏一个边界 → 切断工具栈 → 模型幻觉 | 90+ cases verify_phase5.py 覆盖；先在 dry-run 模式跑 1 周 |
| TaskLedger 持久化与 Streamlit rerun 配合出 bug | 启动时扫 ledger 把 in_progress 强制 cancelled |
| BackgroundScheduler 与 hot-reload 重复触发 | file lock + replace_existing=True |
| 5 批 PR 跨度大，期间日常运营被阻塞 | 每批独立合并；老路径全保留兜底 |

## Migration Strategy

- **每批独立可回滚**：所有新接口加 feature flag，老路径默认保留
- **Verify 脚本配套**：每批新增 verify_phase5_p<N>.py，pass 才合
- **生产体感监控**：每批合并后跑 3-5 个真实 Agent 任务对比新旧输出质量
- **文档同步**：合并时更新 `docs/ARCHITECTURE.md` 对应章节

---

## Acceptance（用户验收清单）

P0 完成后：
- [ ] 跑一次 30k tokens 的 Agent 会话不中断
- [ ] verify_phase5_p0.py 全部通过
- [ ] Console 输出可见 scratch_pad 结构

P1 完成后：
- [ ] 同时跑 2 个 Agent 写 playbook，OCC 冲突被正确捕获
- [ ] 拔掉 Kimi key，Mock provider 接管，Agent 仍跑通

P2 完成后：
- [ ] 一条 prompt「为下周三发布做内容准备」自动跑完 Intel→Analyst→Content 三步
- [ ] 中断重启 Streamlit，未完成 DAG 状态正确恢复

P3 完成后：
- [ ] 周一 09:00 自动出现新 playbook entry
- [ ] 用户可在 Console 删除 / 接受 / 修改这条 entry

P4 完成后：
- [ ] 模拟 run_search.py 卡死场景，5 分钟自动 kill
- [ ] Linux 上 content_generator 内存超 1GB 自动 kill

总验收：用户在 Console 跑「日常一周内容运营」体感比 v1 流畅、产出质量更高。
