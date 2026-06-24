# Tasks: orchestrator-coordinator · V1 真·协调 Agent

3 个 phase，各自独立可合并 + phase 末验收。
> **前置**：`orchestrator-mvp` P1-P3 已实现(会话持久化 + 面板壳 + API + 决策卡，作脚手架复用)。
> **底座**：`AgentBase` 主循环、`HermesMaster.submit`、`EventSourceResponse`(对齐 `collect_stream`)均已就绪。

---

## P1 · 协调 Agent 内核(回路 + 元工具 + 可恢复)

> **目标**：一个真 agent，按意图动态调用子 agent、能追问、能收尾解读；暂停/恢复走无状态重放。

### P1.1 内核
- [x] P1.1.1 新建 `agents/orchestrator_agent.py`：专用协调循环复用 AgentBase 机器件(见契约 §C 落地细化)
- [x] P1.1.2 元工具 `run_subagent(archetype, task)`(V1a：映射现有 type → `HermesMaster.submit`，白名单校验)
- [x] P1.1.3 元工具 `ask_user` / `raise_decision_card`：抛 `_PauseSignal` → 主循环写 `pending` + status 返回
- [x] P1.1.4 元工具 `finish(summary)`：收尾输出人话建议
- [x] P1.1.5 system prompt：注入 goal methodology + 协调纪律(何时追问/只答/多步)
- [x] P1.1.6 防失控：迭代上限 + token 预算约束主回路，到顶优雅收尾

### P1.2 可恢复(无状态重放)
- [x] P1.2.1 对话历史(含 tool_calls / 子 agent tool 结果)落 session.messages；trace/pending 字段在 local backend 补齐(P1 内)
- [x] P1.2.2 恢复 = 重建 messages + 新答复续跑；已跑子 agent 结果不重跑(test 验证)
- [x] P1.2.3 复用 `agents/compression.py` 免疫压缩控历史膨胀

### P1.3 测试
- [x] P1.3.1 stub LLM + stub master，**动态分支**：纯问答→不调子 agent；只析→只 analyst；规划并写→intel→analyst→content → `tests/test_orchestrator_agent.py`
- [x] P1.3.2 追问/决策卡暂停 + 答复恢复(不重跑)
- [x] P1.3.3 防失控：迭代上限优雅收尾；非法 archetype 拦截
- [x] P1.3.4 trace 落库可恢复 → **8/8 通过**(并存 sessions 7 + mvp 8 = 23/23)

### P1.4 Commit
- [x] P1.4.1 `feat(orchestrator): coordination-loop agent (subagents-as-tools, resumable)`

---

## P2 · SSE 流式 + 会话 trace 持久化

> **目标**：协调过程实时推送，断连/刷新可恢复全过程。

### P2.1 存储
- [x] P2.1.1 migration `011_orchestrator_trace.sql`：`orchestrator_sessions` 增 `trace` JSONB、`pending` JSONB；status 枚举改 `thinking/awaiting_user/awaiting_decision/done/cancelled`(reviewed-only)
- [x] P2.1.2 双 backend(pg + local)`update_session` 支持新字段(沿用 OCC)
- [x] P2.1.3 测试：trace 追加 / pending 读写 / 恢复

### P2.2 端点
- [x] P2.2.1 `POST /api/v1/orchestrator/converse/stream`(`EventSourceResponse`，线程跑回路 + Queue + stop_event，对齐 collect_stream)
- [x] P2.2.2 事件：thinking / subagent_start / subagent_result / decision_card / awaiting_user / final / error / done；每事件落 session.trace
- [x] P2.2.3 旧 `/converse`(P1 薄壳)改为调新内核(非流式回退入口保留)
- [x] P2.2.4 `GET /session/{id}` 返回 trace + pending(刷新恢复)
- [x] P2.2.5 测试：stream 端到端事件序列；断连 stop_event；恢复

### P2.3 Commit
- [x] P2.3.1 `feat(orchestrator): SSE coordination stream + trace persistence`

---

## P3 · Chat-first 前端重构 + 流式聊天面板

> **目标**：把主入口收敛为一个聊天界面（主助手即门面），14 页降级为 `/admin` 后台；聊天页执行区走 SSE 流式。参考 Ant Design X 体感,用 base-ui/tailwind 实现(不引入 antd)。
> **设计文档**：`docs/superpowers/specs/2026-06-05-chat-first-redesign-design.md`（chat-first + Claude 暖调 + 后台入口方案 A，已与用户确认）。
> **重要**：本阶段取代 `baton-frontend-codex.md` 中「聊天面板挂现有侧边栏下」的旧前提——流式聊天直接建在新根聊天页 `/`，避免返工。

### P3.0 前端壳重构（chat-first, Claude design）
- [x] P3.0.1 路由重构：聊天主页落 `/`（极简顶栏外壳，无侧边栏）；现有 14 页整组迁到 `(admin)` group → `/admin/*`，复用现有 `Sidebar.tsx` 深色外壳 + 全局告警 banner
- [x] P3.0.2 兼容 redirect：旧 `/assistant` → `/`；旧顶层 `/goals` `/insight` … → `/admin/*`（防旧书签 404，一迭代后可移除）；全量更新内部 `<Link href>`
- [x] P3.0.3 聊天空状态（首屏引导）：衬线欢迎语 + 大圆角输入框（内嵌 🎯goal 选择 + 品牌红发送）+ 4 个建议气泡 + 底部能力说明
- [x] P3.0.4 顶栏 `⚙️ 管理后台`（方案 A，进 `/admin`）+ 头像；`/admin` 侧栏移除「🧭 主助手」项、加返回主助手入口
- [x] P3.0.5 主题 token：`globals.css` 改 Claude 暖调（`--bg #faf9f5`、暖灰文本/边框、卡片底 `#fffefb`），品牌红 `--brand` 不变；hero 衬线字体

### P3.1 流式对话流（建在根聊天页）
- [x] P3.1.1 `orchestratorApi` 增 stream 消费(SSE，复用 `sseUrl` token 走 query)
- [x] P3.1.2 发首条消息后空状态 → 单列对话流（输入框 pin 底部）：思考/调子 agent/结果/最终建议；增量渲染 + 打字指示 + 自动滚动
- [x] P3.1.3 `ask_user`/决策卡渲染为可交互气泡;答复→再发起一轮 stream(带 session_id)续跑
- [x] P3.1.4 进页 localStorage 续接 + `GET /session/{id}` 恢复 trace（含 `pending` 待答态）
- [x] P3.1.5 移除旧"一次性计划→确认→DAG 轮询"的 UI(被流式取代)；旧 `assistant/page.tsx` 退役
- [x] P3.1.6 内容关联最小钩子（骨架①，设计 §4.4）：生成内容在流里就地成内容卡 + 跳 `/admin/content`/草稿箱精修；生成笔记落库写 `source_session_id` 溯源字段（前端暂不做反向跳转 UI）
  > 多会话历史侧栏 UI、按 goal 列会话 API、后台→源对话反向跳转 = **留后续 phase**，本期不建

### P3.2 验收
- [x] P3.2.1 `verify_orchestrator.py` 重写:覆盖动态分支(只答/只析/多步)+追问恢复+流式事件+结果解读
- [x] P3.2.2 完工自测：`tsc --noEmit` + `eslint` + `next build` 全绿
- [x] P3.2.3 浏览器手工验收(用户自点):进站即聊天空状态→一句话→看到边做边出的协调过程→追问能答→最终给建议;刷新可恢复;`⚙️ 管理后台`可进 14 页

### P3.3 Commit
- [x] P3.3.1 `refactor(frontend): chat-first shell + /admin backend (Claude design)`
- [x] P3.3.2 `feat(assistant): streaming coordination chat as main entry`

---

## 总验收(Phase Gate)
- [x] G.1 pytest 全绿(不含 PG 专用)
- [x] G.2 `verify_web_skeleton.py` 通过(新增 stream 端点纳入覆盖)
- [x] G.3 端到端:**动态**——不同意图走出不同协调路径(证明不僵化)+ 追问 + 结果解读
- [x] G.4 刷新/断连恢复:trace + pending 可恢复
- [x] G.5 文档:`docs/USER_GUIDE.md` 主助手章节改写为"协调式 + chat-first 主入口";`CLAUDE.md` Dashboard 页面结构表更新为 chat 主页 + `/admin` 后台;`docs/STATUS.md` 更新 V1 行
- [x] G.6 归档:spec deltas 合入 `openspec/specs/orchestrator/spec.md`;目录移 `archive/<date>-orchestrator-coordinator/`;`orchestrator-mvp` 一并收口
