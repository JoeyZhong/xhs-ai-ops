# Decisions: 前端重构

> 用户拍板日期：2026-05-07
> 决策依据：用户回复 + 商业/工程逻辑论证

---

## Q1 · 框架选型 ✅ Next.js 14 App Router

**理由**：
- Vercel / Zeabur 一键部署支持，对一人公司省去 Nginx 配置成本
- App Router 的 layout 模式天然适合"侧边栏 + 多工作流"后台产品
- React 生态最成熟，shadcn/ui / TanStack Query 等周边库齐全

**否决项**：
- Vite + React：缺 SSR/部署模板，需自己拼装
- SvelteKit：生态较窄，shadcn/ui 移植版不如官方

---

## Q2 · UI 库 ✅ shadcn/ui + Tailwind CSS

**理由**：
- 代码复制模式（不是 npm 安装的封闭组件），高度可定制
- 视觉风格符合"现代 SaaS"调性，避开 Ant Design 的"传统后台"审美
- 与 Tailwind 原子类共生，定制成本低

**否决项**：
- Ant Design：组件重，主题切换繁琐，调性偏企业内部系统
- Mantine：选项可，但生态没 shadcn 活跃

---

## Q3 · Repo 结构 ✅ Monorepo `frontend/`

**理由**：
- 一人维护双 repo 心智成本过高
- 前后端协同改动可在同一 commit 完成（原子性）
- 当前 `frontend/spider_xhs_ui.html` 已在主 repo 内

**操作**：
- 在仓库根目录新建 `frontend/`（Next.js 项目根）
- 现有 `frontend/spider_xhs_ui.html` 移到 `frontend/_legacy_prototype/` 仅作设计参考

---

## Q4 · v1 认证 ✅ 简单 Bearer Token（用户调整推荐方案）

> ⚠️ 这是用户**调整了原推荐**的决策。原推荐"无认证"，用户改为"简单 token"，理由如下：

**用户给的理由**：
- Kimi API 端点 + 爬虫端点一旦暴露公网非常危险
- 不需要登录页，但请求头加 `Authorization: Bearer <SECRET>` 即可
- FastAPI 加一行 Depends 校验，10 分钟工作量
- 避免公网裸奔

**实施细节**：
1. 后端：
   - 在 `config/settings.json` 加 `api_secret_token: <随机生成的 32 字节 hex>`
   - `server/main.py` 加 `Depends(verify_token)` 函数：检查 `Authorization` 头
   - `/api/v1/health` **不强制** token（监控用）
   - 其他 `/api/v1/*` 端点全部要求 token
2. 前端：
   - `frontend/.env.local` 写 `NEXT_PUBLIC_API_TOKEN=<同 secret>`
   - `lib/api.ts` 默认 fetch 自动加 `Authorization: Bearer ${token}` 头
3. token 生成：首次启动 dashboard 自动生成（写入 settings.json），用户复制粘贴到 frontend `.env.local`

**对 tasks.md 的修改**：
- F0.4.1 API client 必须默认带 Authorization 头
- F1.6.1 `app.include_router` 之外，新增 `auth.py` 中间件
- F1.7 验证用例需覆盖：无 token 返 401 / 错 token 返 403 / 正确 token 通过

**长期演进**：
- v1：单租户单 token（够用）
- P4 多租户时升级为 per-tenant token（与 persona_id 绑定）

---

## Q5 · 状态管理 ✅ Zustand

**理由**：
- API 极简（一个 hook 搞定）
- 无 boilerplate（vs Redux 的 reducer / action / dispatch 三件套）
- 适合本项目"全局当前 goal" / "侧边栏折叠态" / "active persona" 等少量全局变量

**否决项**：
- Jotai：原子化模型对当前业务有点大材小用
- Redux Toolkit：心智负担太重，本项目用不到 middleware / devtools time travel

**实施约束**：
- store 文件按域拆分：`stores/goals.ts` / `stores/personas.ts` / `stores/ui.ts`
- 服务端数据走 TanStack Query，**不放 Zustand**（避免双源真理）

---

## Q6 · 数据请求 ✅ TanStack Query (React Query)

**理由**：
- 内置 polling（DAG 状态轮询每 2s 一次，正适用）
- 内置缓存 + stale-while-revalidate
- 自动重试 + 乐观更新
- TS 类型友好（与 `openapi-typescript` 生成的 types 配合好）

**否决项**：
- SWR：选项可，但 polling 配置不如 TanStack Query 灵活
- 手写 fetch：会重复造几百行轮子

**配置**：
- `staleTime: 30s` 默认
- `retry: 1` 默认
- `refetchOnWindowFocus: false`（运营工具内部用，避免每次切窗口重新请求）

---

## Q7 · F1 vs F2 顺序 ✅ 契约驱动并行

**理由**：
- 不能等后端 9 个端点全做完才动前端
- F0 阶段先冻结 pydantic Models（API 契约）
- 前端用 `openapi-typescript` 生成 TS types，先 mock 数据画界面
- F1 后端端点 ready 后，前端切真实端点（mock 替换为 fetch）

**实施约束**：
- F0.4.2 必须先跑 `openapi-typescript` 生成 `types/api.gen.ts`，否则前端 mock 无类型保障
- F1 每完成一个端点 PR 后，前端立即 `pnpm gen:types` 更新

---

## Q8 · Streamlit 退役节奏 ✅ F4 标 Deprecated + 2-3 周观察期

**理由**：
- 前端重构最容易"功能遗漏"
- Streamlit 移到 :8502 作"逃生舱"：客户跑单卡死时立即切回老界面
- 商业信誉不能因代码重构受损

**实施细节**：
- F4.1.1 Streamlit 启动横幅改为「⚠️ 已迁移到 :3000，本端口仅作降级备份」
- F4.1.1 同时调整启动命令文档：`streamlit run dashboard.py --server.port 8502`
- 观察期满后用户决策：删除 vs 永久保留（建议保留为运维兜底）

**对 docs 的影响**：
- `CLAUDE.md` 启动命令需更新两个端口（:3000 主 + :8502 备份）
- `docs/USER_GUIDE.md` 在显著位置说明"双端口共存模式"
