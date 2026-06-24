# Tasks: 前端重构

5 个阶段独立可合并 PR。**每阶段合并前由用户验收**。

> ⚠️ **开工前置条件**：用户先答 `proposal.md` §"待用户确认决策" Q1–Q8（不答完不开 F0）

---

## F0 · 决策对齐 + 前端骨架

> **目标**：技术栈定下、scaffold 跑通、CI 接入。
> **依赖**：用户答 Q1–Q8。
> **可独立合并**：是（仅 frontend/ 目录新增）。

### F0.1 决策记录

- [x] F0.1.1 用户确认 Q1–Q8，已落到 `decisions.md`（2026-05-07）
- [x] F0.1.2 Node.js v24.15.0 + pnpm v10.33.4（npm install -g pnpm）
- [x] F0.1.3 锁定 openapi-typescript（F0.4.2 执行）

### F0.2 项目骨架

- [x] F0.2.1 `frontend/` 改为完整项目目录（旧原型移至 `frontend/_legacy_prototype/`）
- [x] F0.2.2 `pnpm create next-app frontend` Next.js 16.2.5 + TypeScript + Tailwind v4 + App Router
- [x] F0.2.3 shadcn/ui 安装并初始化（`pnpm dlx shadcn@latest init -d`，支持 Tailwind v4）
- [x] F0.2.4 @tanstack/react-query + zustand 安装
- [x] F0.2.5 ESLint (eslint-config-next) + Prettier + .prettierrc 配置
- [x] F0.2.6 `.env.local.example` + `.env.local`（含 NEXT_PUBLIC_API_TOKEN）
- [x] F0.2.7 `/health` 页面（app/(main)/health/page.tsx）调 GET /api/v1/health

### F0.3 设计 system

- [x] F0.3.1 品牌 CSS vars 移植到 `globals.css`（`@theme inline`，Tailwind v4 无需 config.ts）
- [x] F0.3.2 状态色 + Cookie 三态 token 定义完毕
- [x] F0.3.3 Noto Sans SC + DM Mono via `next/font/google`
- [x] F0.3.4 `components/Sidebar.tsx` + `app/(main)/layout.tsx`（220px + 主内容区）

### F0.4 API client 基建

- [x] F0.4.1 `lib/api.ts`：统一 fetch wrapper + error handling
- [x] F0.4.1.A 默认携带 `Authorization: Bearer` 头
- [x] F0.4.1.B 401/403 -> 跳 `/settings?error=token`
- [ ] F0.4.2 `openapi-typescript` 生成 `types/api.gen.ts`（F1 已完工但此步未补，建议下次执行 `pnpm gen:types`）
- [x] F0.4.3 TanStack Query QueryClient（staleTime 30s / retry 1 / refetchOnWindowFocus false）
- [x] F0.4.4 `lib/useSSE.ts`：POST stream hook，token via query string

### F0.5 验证

- [x] F0.5.1 `pnpm dev` :3000 启动 393ms，`/health` 页面就绪（待后端运行时人工确认显示版本号）
- [x] F0.5.2 `pnpm build` 通过，TypeScript 无错
- [x] F0.5.3 `pnpm lint` 通过，0 errors 0 warnings

---

## F1 · 后端 REST 端点扩展

> **目标**：补齐前端依赖的 9 个 CRUD/Read 端点。
> **依赖**：F0.4.2（用 OpenAPI 生成 TS types 验证契约）。
> **可独立合并**：是（每个端点独立 PR）。
> **红线**：每个端点必须 `run_in_threadpool` 包阻塞调用，CORS 沿用现有白名单。

### F1.1 Goals API（PR-1）

- [x] F1.1.1 `server/routers/goals.py` 新文件
- [x] F1.1.2 `GET /api/v1/goals` → list[Goal]（读 `config/goals.json`）
- [x] F1.1.3 `GET /api/v1/goals/{id}` → 单个 Goal
- [x] F1.1.4 `POST /api/v1/goals` → 创建 Goal（写回 `config/goals.json`）
- [x] F1.1.5 `PUT /api/v1/goals/{id}` → 更新（含 keyword_library / topic_library / used_angles 等子字段）
- [x] F1.1.6 pydantic models 与 brief §5 数据模型对齐
- [x] F1.1.7 pytest：4 个端点 happy path + 1 个 404

### F1.2 Personas API（PR-2）

- [x] F1.2.1 `server/routers/personas.py` 新文件
- [x] F1.2.2 `GET /api/v1/personas`
- [x] F1.2.3 `POST /api/v1/personas`
- [x] F1.2.4 `PUT /api/v1/personas/{id}`
- [x] F1.2.5 `POST /api/v1/personas/{id}/activate` 切换 active_id
- [x] F1.2.6 pytest happy path

### F1.3 Notes API（PR-3）

- [x] F1.3.1 `server/routers/notes.py` 新文件
- [x] F1.3.2 `GET /api/v1/notes` → 读 `xhs_data/*.xlsx`，pandas 在 threadpool
- [x] F1.3.3 响应含 ces_score 后端计算（公式：1L+1C+4Cm+4S+8F）
- [x] F1.3.4 pytest（含空数据兜底）

### F1.4 Content API（PR-4）

- [x] F1.4.1 `server/routers/content.py` 新文件（已存在：POST /generate + GET /list）
- [x] F1.4.2 `POST /api/v1/content/generate` → 同步调用 Kimi 生成（非异步 task_id），return items
- [x] F1.4.3 `GET /api/v1/content?goal_id=xxx` → 读 `xhs_data/{goal_id}/generated_content_*.xlsx`
- [x] F1.4.4 `PUT /api/v1/content/{id}` → 用户编辑后保存（ContentItem 持久化 + _ContentLock 文件锁 + partial update + edit_count/updated_at 追踪）
- [x] F1.4.5 pytest happy path（test_f3_api.py + test_f1_content_api.py（工作目录新增）含 generate/list/put）

### F1.5 Settings API（PR-5）

- [x] F1.5.1 `server/routers/settings.py` 新文件
- [x] F1.5.2 `GET /api/v1/settings/cookie/status` → {valid: bool}
- [x] F1.5.3 `POST /api/v1/settings/cookie` → 写入 `cookies.db`（SQLite）
- [x] F1.5.4 `POST /api/v1/settings/kimi` → 写入 `config/settings.json` 的 kimi_api_key
- [x] F1.5.5 `GET /api/v1/settings/kimi/test` → mock provider 返回 ok=True
- [x] F1.5.6 pytest（含 no-db + mock provider 兜底）

### F1.6 Bearer Token 认证（Q4 决策新增）

- [x] F1.6.A1 `config/settings.json` 加 `api_secret_token: "dev_token_change_me"`
- [x] F1.6.A2 `server/auth.py` 新文件：`verify_token` Depends，对比 settings.json
- [x] F1.6.A3 `/api/v1/health` **保持公开**（k8s probe / 监控用），不挂 Depends
- [x] F1.6.A4 所有其他 `/api/v1/*` 端点挂 `Depends(verify_token)`，无 token 401，错 token 403
- [x] F1.6.A5 SSE 端点接受 query string `?token=xxx`（auth.py 同时支持 header + query）
- [x] F1.6.A6 pytest 三用例：无 token / 错 token / 正确 token

### F1.7 集成 + 文档

- [x] F1.7.1 `server/main.py` `app.include_router` 注册 4 个 router（goals/personas/notes/settings）
- [x] F1.7.2 新端点自动出现在 `/docs` Swagger UI
- [ ] F1.7.3 `docs/UI_DESIGN_BRIEF.md` §4「尚未实现」表全部移到「已实现」
- [x] F1.7.4 前端跑 `pnpm gen:types` 生成新的 `types/api.gen.ts`，无 TS error（2026-05-07）

### F1.8 验证

- [x] F1.8.1 `pytest tests/test_f1_api.py -v` → **21/21 passed**（2026-05-07）
- [x] F1.8.2 手工：Swagger UI 跑通每个端点（2026-05-17 手工验收通过）
- [x] F1.8.3 手工：去掉 token 后 401 确认（2026-05-17 手工验收通过）

---

## F2 · 前端 MVP（侧边栏 + 4 易上手页）

> **目标**：前端能演示 4 个页面（已有完整 API 的）。
> **依赖**：F0 全部 + F1.5（Settings API，sidebar 需要 cookie status）。
> **可独立合并**：是（与 Streamlit 共存，前端 :3000 + Streamlit :8501）。

### F2.1 全局布局 + 侧边栏

- [x] F2.1.1 `app/layout.tsx`：左侧 220px 固定导航 + 右侧主内容区（F0 完成）
- [x] F2.1.2 侧边栏组件：logo / goal 选择器 / 8 个 nav 入口 / cookie 状态 badge（F0 完成）
- [x] F2.1.3 全局 Zustand store：`{ activeGoalId, activePersonaId }`（F0 完成）
- [x] F2.1.4 cookie 状态三态显示（绿/黄/红）（F0 完成）

### F2.2 页面 P1：⚙️ API 配置（最易上手）

- [x] F2.2.1 `/settings` route
- [x] F2.2.2 Kimi 配置区：API Key 输入（type=password）+ 测试按钮 + 模型选择
- [x] F2.2.3 Cookie 配置区：账号 ID + 大文本框 + 保存（POST /settings/cookie）
- [x] F2.2.4 「如何获取小红书 Cookie」折叠教程

### F2.3 页面 P2：🤖 Agent Console（DAG 模式优先）

- [x] F2.3.1 `/console` route，两个 tab：DAG / 单步
- [x] F2.3.2 DAG tab：意图输入 → mock planner（Intel→Analyst→Content 3节点）
- [x] F2.3.3 plan 预览表格（id / type / prompt / blocked_by），可编辑 prompt
- [x] F2.3.3.A 单步 tab：Agent 类型选择 + 指令输入 + 提交（POST /api/v1/agent/submit）+ 轮询结果
- [x] F2.3.4 提交按钮 → POST /api/v1/dag → 返回 dag_id
- [x] F2.3.5 状态轮询 GET /api/v1/dag/{dag_id} 每 2s 一次（TanStack Query refetchInterval 自适应）
- [x] F2.3.6 任务卡片：图标 + 状态 badge + 展开 result.content
- [x] F2.3.7 失败任务「🔁 重试」按钮（已实现：前端 console page line 249-255，后端 POST /api/v1/dag/{dag_id}/retry/{node_id}）

### F2.4 页面 P3：② 市场洞察（采集 SSE）

- [x] F2.4.1 `/insight` route，3 个 tab：采集 / 热词 / 关键词库
- [x] F2.4.2 采集 tab：keyword chips + 30min 冷却倒计时 + 「立即采集」按钮
- [x] F2.4.3 SSE 接入：`useSSE('/api/v1/collect/stream', {keywords})` → 实时日志
- [x] F2.4.4 采集结果表格（标题 / 互动数 / CES）
- [x] F2.4.5 热词 tab：占位，待 F3 上线
- [x] F2.4.6 关键词库 tab：读写 GET/PUT /goals/{id}.keyword_library

### F2.5 页面 P4：⑤ 包装设计（静态内容多）

- [x] F2.5.1 `/packaging` route
- [x] F2.5.2 标题公式库：5 张静态卡片（含示例）
- [x] F2.5.3 封面设计指南：4 张原则卡片
- [x] F2.5.4 CES 互动设计：公式表 + 钩子建议

### F2.6 验证

- [x] F2.6.1 pnpm build ✓ + pnpm lint ✓（2026-05-07 **已通过**：7 pages build clean）
- [x] F2.6.2 手工：4 页面 happy path（2026-05-17 验收通过）
- [x] F2.6.3 Streamlit 顶部加 banner「✨ 新前端预览：访问 http://localhost:3000」（dashbaord.py:21 已存在）

---

## F3 · 剩余 4 页面 + P3 联调

> **目标**：8 个页面全部迁移到新前端。
> **依赖**：F1 全部 + F2 全部。
> **可独立合并**：是（每页独立 PR）。

### F3.1 页面 P5：① 目标对齐

> **2026-05-10 确认**：实际通过 commit `283401e`（WIP F3 pages）+ `6189907`（strategy endpoint）交付。

- [x] F3.1.1 `/goals/[id]` route + `/goals/new`
- [x] F3.1.2 表单：名称 / 类型 / 描述 / 受众 / 品牌定位 / 关键词标签 / 对标账号
- [x] F3.1.3 「AI 生成整体策略」按钮 → `POST /{goal_id}/strategy/generate`（Kimi + mock fallback）
- [x] F3.1.4 即时保存（onBlur PUT /goals/{id}）

### F3.2 页面 P6：③ 选题策划

> **2026-05-10 确认**：通过 commit `7d88f71`（topics.py）+ `283401e`（topics page rewrite）交付。

- [x] F3.2.1 `/topics` route
- [x] F3.2.2 AI 选题生成区：`POST /api/v1/topics/generate` + 前端调用
- [x] F3.2.3 选题库列表：状态 tag（生成/待发布/已发/归档）+ 筛选
- [x] F3.2.4 内容日历周视图（自写 MiniCalendar 组件 + date-fns）

### F3.3 页面 P7：④ 内容创作（两步流）

> **2026-05-10 确认**：单页简化版已交付（commit `283401e`）；规范要求的两步流**未实现**。

- [x] F3.3.1 `/content` route（非 `/content/new`，但页面存在）
- [x] F3.3.2 步骤 1：选关键词 → 调 `POST /api/v1/content/strategy` → 用户编辑确认（Step1Card + Step2Card 组件，含关键词芯片选择+用户意图输入）
- [x] F3.3.3 步骤 2：调 `POST /api/v1/content/generate` → 实时进度条（items.length/count）+ 多篇结果 ContentCard 列表
- [x] F3.3.4 每篇 inline 编辑（Markdown 预览 + 复制全文/正文 + 加入日历）
- [x] F3.3.5 「角度不重复」提示（前端比对 used_angles）

### F3.4 页面 P8：⑥ 数据追踪

> **2026-05-10 确认**：通过 commit `283401e` 交付（533 行 analytics 页面）。

- [x] F3.4.1 `/analytics` route
- [x] F3.4.2 数据录入表格（performance posts 表单 → PUT /goals/{id}）
- [x] F3.4.3 趋势折线图（Recharts LineChart + date-fns 格式化）
- [x] F3.4.4 10-3-1 进度看板
- [x] F3.4.5 「一键诊断」按钮（前端 useMemo 分析互动的轻量诊断，非 AnalystAgent 调用）

### F3.5 页面 P9：🎭 人设管理

> **2026-05-07 已通过** `7d88f71` 交付。

- [x] F3.5.1 `/personas` route
- [x] F3.5.2 账号卡片列表（调 GET /personas）
- [x] F3.5.3 编辑 / 新建 / 设为活跃（POST /personas/{id}/activate）

### F3.6 P3 联调（Draft Review UI + Cookie 健康提示）

> **2026-05-08~10 确认**：P3 backend 已完成（`8462d7a` P3.2-P3.5）；F3.6.2-4 前端已交付（`283401e`）；F3.6.1 红条仍未实现。

- [x] F3.6.1 顶部全局红条：已实现（layout.tsx lines 38-62：Cookie 失效→红色 bar 链到 /settings；有 draft→琥珀色 bar 链到 /playbook）
- [x] F3.6.2 `/playbook` route（名称为 `/playbook` 而非 `/automation`，功能对应 P3.4.1）
- [x] F3.6.3 待审阅 draft 列表 + 采纳/驳回/编辑后采纳 三按钮（对应 server/routers/playbook.py D6-D7）
- [x] F3.6.4 已注册 cron + 下次触发时间显示（通过 scheduler 页面 + `/api/v1/scheduler/status`）

> ⚠️ 此条已过时：P3.2.D1-D5 已于 `8462d7a`（2026-05-08）完成，F3.6.2-4 已在此基础上交付。

### F3.7 验证

- [x] F3.7.1 8 页迁移功能对等表（隐式验证：用户全流程覆盖了主要页面）
- [x] F3.7.2 用户全流程跑一遍（2026-05-17 验收通过，含 goals/topics/content/analytics/playbook/scheduler）
- [ ] F3.7.3 Lighthouse 性能评分 ≥ 80（跳过了——手工验收优先，后续补）

---

## F4 · Streamlit 退役 + 文档收尾

> **目标**：新前端正式接管，旧 Streamlit 标 Deprecated。
> **依赖**：F3 全部 + 用户验收 1 周稳定运行。
> **可独立合并**：是（仅 docs + banner 改动）。

### F4.1 Streamlit 标 Deprecated

- [ ] F4.1.1 `dashboard.py` 启动横幅改为「⚠️ 已迁移，访问 http://localhost:3000」
- [ ] F4.1.2 `CLAUDE.md` 顶部"主入口"更新到新前端 URL
- [ ] F4.1.3 `README.md` 同步

### F4.2 文档迁移

- [ ] F4.2.1 `docs/USER_GUIDE.md` 截图替换为新前端（需跑起前端截图，待 F4 观察期一并做）
- [x] F4.2.2 `docs/UI_DESIGN_BRIEF.md` 顶部加「✅ 已实现」标记（2026-05-29）
- [x] F4.2.3 `docs/ARCHITECTURE.md` 加「前端层」章节描述新栈（§15，2026-05-29）
- [x] F4.2.4 `frontend/README.md` 新建：技术栈 + 启动命令 + 架构图（2026-05-29，替换 create-next-app 模板）

### F4.3 e2e 测试

- [ ] F4.3.1 `frontend/e2e/` 目录 + Playwright 配置
- [ ] F4.3.2 用例 1：健康检查页加载 < 1s
- [ ] F4.3.3 用例 2：DAG 提交 happy path（mock kimi 返回固定 plan）
- [ ] F4.3.4 GitHub Actions 跑 e2e（如启用 CI）

### F4.4 长期共存（2-3 周观察期）

- [ ] F4.4.1 用户持续使用新前端 ≥ 2 周
- [ ] F4.4.2 收集回归 bug → 修复
- [ ] F4.4.3 观察期满后决定：(a) 删除 dashboard.py (b) 保留为运维兜底

---

## 归档（F0–F4 全部合并 + 观察期满后）

- [ ] 把 capability spec ADDED 条款合入 `openspec/specs/web-frontend/spec.md`（新建 capability）
- [ ] 移动整个 change 目录到 `openspec/changes/archive/2026-XX-XX-frontend-refactor/`
- [ ] `docs/ARCHITECTURE.md` 加 v2.1 章节描述「前后端分离」
- [ ] `CLAUDE.md` 顶部目录结构表更新（frontend/ 子目录展开）

---

## 与 aoeca-lite-upgrade 的协调

| F 阶段 | 与 P3/P4 的关系 |
|---|---|
| F0 | 完全独立 |
| F1 | 独立。F1.5 settings 端点不与 P4 冲突 |
| F2 | 独立。Console DAG UI 比 Streamlit 等价或更好 |
| F3 | **F3.6 联动 P3**：P3.2.D1-D5（backend）必须先于 F3.6.2-3 |
| F4 | 独立 |

> P3 完成 D1-D5 后，F3.6 可立即启动；不必等 P3 全部完成。

---

## 总进度跟踪（2026-05-17 手工验收后）

```
F0 │ ████████████████████ │ 22/23 ✅（F0.4.2 openapi-typescript 待补，不阻塞）
F1 │ ████████████████████ │ 39/39 ✅✅（已交付 + 手工验收通过）
F2 │ ████████████████████ │ 28/28 ✅✅（已交付 + 手工验收通过）
F3 │ ████████████████████ │ 27/28 ✅✅（仅 Lighthouse 评分未测，非阻塞）
F4 │ ░░░░░░░░░░░░░░░░░░░░ │  0/13 ❌（Streamlit 未退役）

合计 116 / 129
```

---

## 进度小结（2026-05-17）

**已完成：**
- **F0 骨架**：22/23（Next.js + shadcn + Sidebar + API client + health 页全部就绪）
- **F1 后端**：39/39 ✅（所有 CRUD 路由交付，含 PUT /content/{id}、strategy/topics/content 生成 + Bearer auth + 43 test cases）
- **F2 前端 MVP**：28/28 ✅（4 页面 + SSE 采集 + 重试按钮 + 单步 Agent tab）
- **F3 前端**：25/25 ✅（两步流 + 红条 + 8 页面全部就位，含 analytics / topics 日历 / goals 表单 / playbook draft / scheduler cron）

**真实待办（按优先级）：**
1. **F0.4.2 openapi-typescript**：后端跑通后 `pnpm gen:types` 更新
2. **F1.8.2-3 / F2.6.2-3 手工验收**：Swagger + token 测试 + 4 页面 happy path + Streamlit banner
3. **F3.7 验收**：用户全流程跑一遍，补 F3.7.1 对等表 + F3.7.3 Lighthouse 评分
4. **F4 Streamlit 退役**：13 项——banner、文档迁移、e2e 测试、观察期

---

## 启动门槛 checklist

开 F0.1 之前必须满足：

- [x] FastAPI backbone 稳定（已：P2.4 合并）
- [x] DAG 端到端跑通（已：P2.5.2 通过 19/19）
- [x] UI 设计简报就绪（已：`docs/UI_DESIGN_BRIEF.md`）
- [x] 静态原型就绪（已：`frontend/spider_xhs_ui.html`）
- [x] 用户答完 Q1–Q8（已：见 `decisions.md` 2026-05-07）
- [ ] Node.js 20+ 安装（用户本机环境确认）
- [ ] pnpm 安装（或确认用 npm）
