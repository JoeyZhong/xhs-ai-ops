# Proposal: 前端重构（Next.js + Tailwind + shadcn/ui）

> **依据文档**：`docs/UI_DESIGN_BRIEF.md`（2026-05-04）+ `frontend/spider_xhs_ui.html`（静态原型）
> **创建日期**：2026-05-07
> **预计周期**：4 个阶段（F0→F4），每阶段独立可合并 PR
> **触发**：AOECA-Lite P0+P1+P2 已完成，FastAPI backbone 稳定，可启动前端迁移

---

## Why

### 现状问题

1. **Streamlit 原型局限**：响应式差、UI 自定义有限、长任务期间页面"卡住"、状态管理混乱（rerun 模型）、移动端体验差
2. **运营人员体验**：内容运营是主要用户群（非技术），Streamlit 风格偏数据看板，不适合做真正的"创作工具"
3. **多页面协作**：8 个工作流页面互相跳转 + 全局状态（Cookie / 当前 goal）需要更现代的 SPA 路由模型
4. **未来扩展**：Phase 4 多租户后，前后端分离是必然路径

### 启动时机的依据

- ✅ **后端能力完整**：Multi-Agent / DAG / 采集 / Cookie 管理全部可用
- ✅ **FastAPI 骨架已建**：`/health` + `POST/GET /dag` + `POST /collect/stream` 3 个端点（P2.4 已合并）
- ✅ **设计简报已就绪**：`docs/UI_DESIGN_BRIEF.md` 完整描述 8 页 + API 契约 + 数据模型
- ✅ **静态原型可参考**：`frontend/spider_xhs_ui.html` 已有视觉与排版基线
- 🔄 **与 P3/P4 解耦**：P3（scheduler）只新增页面，P4（subprocess sandbox）后端无感

---

## What

### 范围

**包括**：
- 新建独立前端项目（推荐 Next.js 14 App Router + TypeScript + Tailwind + shadcn/ui）
- 后端补齐 9 个 REST 端点（Goals / Personas / Notes / Content / Settings）
- 8 页面逐页迁移（保持 Streamlit 共存，渐进替换）
- Streamlit dashboard.py 最终退役（仅保留为运维兜底）

**不包括**：
- 移动端 native（仅响应式 web）
- 用户系统 / 多租户认证（v1 仅 localhost 单用户，多租户随 P4 一起做）
- i18n（中文界面优先）
- WebSocket（用 SSE，已统一）

### 阶段划分

```
F0 决策骨架 ──┬──▶ F1 后端 REST ──┐
              │                    ├──▶ F2 前端 MVP ──▶ F3 剩余页面 ──▶ F4 Streamlit 退役
              └────────────────────┘
                  （F0+F1 可并行）           （并行迁移）
```

| 阶段 | 范围 | 预计工时 | 依赖 |
|---|---|---|---|
| F0 | 选型决策 + 项目骨架 + 设计 token | 0.5–1 天 | 无 |
| F1 | 9 个 REST 端点（5 个 PR 拆分） | 1.5–2 天 | F0 决策（确定数据契约） |
| F2 | 前端 MVP（侧边栏 + 4 个易接入页面） | 2–3 天 | F0 + 部分 F1 |
| F3 | 剩余 4 页迁移 + Draft Review UI（与 P3 联调） | 3–4 天 | F1 全部 + F2 |
| F4 | Streamlit 标 Deprecated + docs 更新 | 0.5 天 | F3 全部 |

**总计**：约 7–11 天工作量。

---

## ✅ 决策已锁定（2026-05-07 用户拍板）

| # | 决策点 | 锁定答案 | 关键理由 |
|---|---|---|---|
| Q1 | 框架选型 | **(a) Next.js 14 App Router** | Vercel/Zeabur 一键部署 + App Router 布局模式适合「侧边栏+多工作流」 |
| Q2 | UI 库 | **(a) shadcn/ui + Tailwind** | 代码复制模式高度可定制；运营工具风格优于 AntD 的"传统后台"审美 |
| Q3 | Repo 结构 | **(a) monorepo `frontend/`** | 一人公司不维护双仓；前后端改动同 commit 原子提交 |
| Q4 | v1 认证 | **(b) 简单 Bearer Token**（用户调整推荐）| Kimi/爬虫端点不能裸奔；Authorization 头硬编码 secret，后端 1 行依赖校验 |
| Q5 | 状态管理 | **(a) Zustand** | 极简，适合 activeGoalId / sidebar 折叠等全局态 |
| Q6 | 数据请求 | **(a) TanStack Query** | DAG 轮询 + 缓存 + 自动重试，省手写 useEffect |
| Q7 | F1 端点优先级 | **F1+F2 并行（契约驱动）** | F0 先冻结 pydantic schema，前端用 mock 同步开工 |
| Q8 | Streamlit 何时退役 | **F4 标 Deprecated，保留 2-3 周观察**（端口移至 :8502 作"逃生舱"）| 客户跑单时若新 UI 卡死，立即切老界面，商业信誉不能受损 |

详细决策记录与理由：见同目录 `decisions.md`。

---

## 与已有 change 的协调

| 已有 change | 协调点 |
|---|---|
| `aoeca-lite-upgrade` P3（scheduler） | F3 阶段需为 P3.2.D6-D8（Draft Review UI）+ P3.3（Cookie 健康提示条）贡献页面，二者**互为前置**：F3 提供 UI 容器，P3 提供 backend draft review 端点 |
| `aoeca-lite-upgrade` P4（subprocess sandbox） | 后端内部，前端无感，无依赖 |
| `aoeca-lite-upgrade` Phase 4（多租户 Supabase） | 本次 v1 仍单租户。F1 REST 端点设计预留 `tenant_id` query param，但默认 `default` |
| `add-agent-skills`（待启动） | 解耦推进。Skills 落地时只需在 Console 内增加 `<actions>` 编辑器，复用 F2 已建 DAG 编辑组件 |

---

## 风险 / 已知问题

| 风险 | 缓解 |
|---|---|
| Streamlit 与新前端共存期 UI 不一致 | F2 阶段同步在 dashboard 顶部加 banner「正在迁移到新前端，访问 :3000」 |
| F1 后端端点契约不稳定，前端反复改 | F0 阶段先冻结 OpenAPI schema（pydantic models 落 PR-1），前端按 schema 生成 TS types |
| Kimi 工具调用被输出成文本（已知问题）影响 DAG 演示效果 | 与 frontend 无关，记入 `project_pending_issues.md`，不阻塞前端启动 |
| Cookie 失效检测前端误判 | F2 sidebar 直接读 `/api/v1/settings/cookie/status`，不前端缓存判断 |
| 进度回归依赖人手测，无 e2e | F4 阶段加 Playwright e2e（健康路径 + 一个 DAG 提交 happy path） |

---

## Out of Scope（明确不做）

- ❌ React Native / Flutter 移动 App
- ❌ 用户注册 / 多租户认证（随 P4 一起做）
- ❌ 国际化（i18n）
- ❌ WebSocket 双向通信（已选 SSE）
- ❌ 服务端渲染 SEO 优化（运营工具内部使用，无需 SEO）
- ❌ 实时协作 / 多人编辑（单用户场景）

---

## 验收

完成下列条件视为本 change 可归档：

- [ ] F0–F4 全部子任务勾选
- [ ] 8 个页面在新前端跑通核心交互（迁移功能对等表 100% 覆盖）
- [ ] Playwright e2e 至少 2 条用例（健康检查 + DAG 提交）通过
- [ ] `docs/UI_DESIGN_BRIEF.md` 标注「已实现」
- [ ] `docs/USER_GUIDE.md` 用户操作截图替换为新前端截图
- [ ] CLAUDE.md 顶部"主入口"更新到新前端 URL
- [ ] Streamlit dashboard 顶部 banner 提示已迁移
