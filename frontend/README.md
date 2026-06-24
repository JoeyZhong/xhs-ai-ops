# Spider_XHS · 前端（Next.js SPA）

小红书全链路 AI 运营平台的现代前端，经 `frontend-refactor` 从 Streamlit (`dashboard.py`)
迁移而来。通过 FastAPI 后端（`server/main.py`）的 REST + SSE 接口驱动，与后端进程分离部署。

> ⚠️ 这不是你熟悉的 Next.js —— 本仓库使用 Next 16，API / 约定 / 文件结构可能与训练数据不同。
> 写代码前请读 `node_modules/next/dist/docs/` 对应指南（见 `AGENTS.md`）。

---

## 技术栈

| 层 | 选型 |
|---|---|
| 框架 | Next.js 16（App Router）+ React 19 |
| 语言 | TypeScript 5 |
| 数据获取 | TanStack Query 5（轮询 / 缓存 / 失效） |
| 实时流 | 原生 `EventSource` 封装（`lib/useSSE.ts`，采集进度） |
| 状态 | Zustand 5（`stores/goals.ts` 活跃目标、`stores/ui.ts`） |
| 样式 | Tailwind 4（CSS 变量主题）+ tw-animate-css |
| 组件 | shadcn / base-ui + lucide-react 图标 |
| 图表 | recharts |
| 类型契约 | `openapi-typescript` 从后端 `/openapi.json` 生成 `types/api.gen.ts` |
| 包管理 | pnpm |

---

## 启动

前端依赖后端在 `:8000` 提供 API。

```bash
# 1) 起后端（仓库根目录）
python -m uvicorn server.main:app --reload --port 8000

# 2) 起前端（本目录）
pnpm install      # 首次
pnpm dev          # → http://localhost:3000

# 其他
pnpm build        # 生产构建
pnpm lint         # eslint
pnpm gen:types    # 后端跑着时，重新生成 types/api.gen.ts
```

鉴权：登录页（`/login`）拿 JWT，存本地后由 `lib/api.ts::apiFetch` 注入
`Authorization: Bearer` 头。后端用 JWT HS256（`security/jwt.py`）校验。

---

## 目录结构

```
frontend/
├── app/
│   ├── login/                 # 登录（JWT 获取）
│   └── (main)/                # 主应用（带侧边栏布局）
│       ├── layout.tsx         # 侧边栏 + 顶栏 + 路由守卫
│       ├── page.tsx           # 首页
│       ├── goals/             # ① 目标对齐（含 [id] / new）
│       ├── insight/           # ② 市场洞察（采集 SSE / 热词 / 关键词库）
│       ├── topics/            # ③ 选题策划
│       ├── content/           # ④ 内容创作
│       ├── drafts/            # 内容草稿（生命周期）
│       ├── packaging/         # ⑤ 包装设计（规则编辑器）
│       ├── analytics/         # ⑥ 数据追踪
│       ├── console/           # 🤖 Agent Console（DAG 多步 / 单步）
│       ├── personas/          # 🎭 人设管理
│       ├── playbook/          # Playbook 审阅
│       ├── scheduler/         # 调度器状态
│       ├── skills/            # Skills Hub
│       ├── settings/          # ⚙️ API 配置
│       └── health/            # 健康检查
├── components/                # 复用组件（Sidebar、ui/*）
├── lib/
│   ├── api.ts                 # apiFetch（JWT 注入 + 错误处理）
│   ├── api/                   # 按域拆分的 API 封装
│   ├── useSSE.ts              # SSE 封装（采集进度流）
│   ├── providers.tsx          # TanStack Query Provider
│   └── utils.ts
├── stores/                    # Zustand（goals / ui）
├── types/api.gen.ts           # 后端 OpenAPI 生成的类型
└── _legacy_prototype/         # 迁移期参考，勿引用
```

---

## 与后端的契约

- **REST**：`/api/v1/*`（goals / notes / content / topics / strategies /
  calendar / packaging / dag / agent / scheduler …）。类型见 `types/api.gen.ts`。
- **SSE**：`POST /api/v1/collect/stream` 推送采集进度（`useSSE`）。
- **多维隔离**：列表类请求按 `goal_id` 过滤（如 `/api/v1/notes?goal_id=`）；
  `tenant_id` 由 JWT 携带，前端不显式传。详见根 `CLAUDE.md` 与
  `openspec/specs/data-dimensions/spec.md`。

---

## 状态

`frontend-refactor` 进行中（F0–F3 已交付，F4 收尾：Streamlit 退役横幅 / 文档 /
e2e / 2 周观察期）。进度见 `openspec/changes/frontend-refactor/tasks.md`。
