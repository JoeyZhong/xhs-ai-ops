# Proposal: FastAPI 后端骨架（Strangler Fig 第一步）

## Why

当前前端是 Streamlit（`dashboard.py` 1700+ 行单文件），存在 4 个长期问题：
1. 单用户 session 模型 — 多人协作天然死锁
2. 子进程 `subprocess.Popen` 跑 CLI 脚本 — 状态难追踪、日志难聚合
3. 不能被 Web 前端（Next.js 等）消费 — UI 演进受限
4. 不能被外部系统集成（Webhook / 定时任务 / 移动端） — 平台化能力归零

我们采用 **Strangler Fig 模式** 逐步剥离 Streamlit：
- 第一步（本 change）：建立 FastAPI 进程骨架，与 Streamlit 共存
- 第二步：把读类操作（采集结果展示、Goal 列表、Persona 列表）暴露为 GET 端点
- 第三步：把写类操作（保存 Cookie、提交 Agent 任务）暴露为 POST 端点
- 第四步：上 Next.js / SvelteKit 前端，消费上述 API
- 第五步：Streamlit 退役，dashboard.py 归档

本 change 仅交付**第一步**：一个能跑起来的 `/health` 接口 + CORS + 目录结构 + 红线规则。

## What

### 范围（明确锁死）
- 新建 `server/` 目录（**不是** `api/`，避免与现有 `apis/` XHS 封装目录混淆；
  也**不是** `web/`，那个目录留给未来真正的 SPA 前端工程）
- 新建 `server/main.py`：FastAPI 应用入口
- 注册 1 个端点：`GET /api/v1/health` → `{"status": "ok", "version": "..."}`
- 配置 CORS 中间件，白名单本地开发常见前端端口
- 新增依赖 `fastapi` + `uvicorn[standard]` + `httpx`（用于 TestClient）
- 新增 `verify_web_skeleton.py` 验收脚本

### 进程拓扑
```
   Streamlit (:8501)        FastAPI (:8000)
        │                         │
        ├── config/personas.json ─┤
        ├── config/goals.json    ─┤
        ├── config/cookies.db (SQLite WAL)  ← 多进程已就位
        ├── memory/default/      ─┤
        └── xhs_data/            ─┤
              共用文件层
```

cookie_manager 的 WAL 模式 + busy_timeout 已支撑多进程并发，
所以 Streamlit 和 FastAPI 可同时运行不冲突（cookie-manager-refactor 已埋好）。

### 红线（Spec 强制条款）
1. **v1 仅含 `/health`**，禁止添加任何业务端点 — 业务端点需独立 change
2. **任何阻塞调用** SHALL 用 `fastapi.concurrency.run_in_threadpool` 包装 —
   裸调 `subprocess.Popen` / `requests.get` / `sqlite3.connect` 等会阻塞事件循环
3. **CORS 不允许 `["*"]`**，必须显式白名单 — 防止 cookie 凭证泄漏
4. **不引入认证体系** — auth 留给下个 change（业务端点上线时一并做）
5. **不修改 dashboard.py 任何一行** — Streamlit 路径完整保留作为参照与回退

## Impact

### 新增文件
- `server/__init__.py`
- `server/main.py`（约 50 行）
- `verify_web_skeleton.py`（约 80 行）
- `openspec/changes/strangler-fastapi-skeleton/` 三件套
- `openspec/specs/web-api/spec.md`（归档时合入）

### 修改文件
- `requirements.txt` — 加 `fastapi` / `uvicorn[standard]` / `httpx`
- `CLAUDE.md` — 目录结构加 `server/`，运行命令加 uvicorn
- `docs/ARCHITECTURE.md` — 标注 Strangler Fig 启动；进程拓扑图

### 不影响
- `dashboard.py`、`run_search.py`、`hot_trend_monitor.py`、`xhs_collector.py`、
  所有 `agents/`、`agent_tools/`、`storage/`、`apis/`、`xhs_utils/` — 一行不动
- 现有测试套件（`verify_phase1_2.py` / `verify_phase3.py` / `verify_cookie_manager.py`）—
  应继续 281/281 通过

## Risk

| 风险 | 缓解 |
|------|------|
| 端口冲突（8000 被占） | uvicorn 默认 8000，文档提示 `--port` 自定义 |
| Python 3.14 + FastAPI 兼容性 | FastAPI 0.110+ / uvicorn 0.27+ 已支持；TestClient 用 httpx 而非 starlette.testclient |
| 同步代码裸调进 async handler 死锁 | 红线 + spec 明文禁止；本 change 没业务端点不会触发 |
| 后续 PR 偷偷加业务端点违规 | spec 立红线，PR review 时可一眼识别违规 |
| 依赖膨胀（FastAPI + pydantic 2 + uvicorn） | 这些是后端必需；不可省 |
| Streamlit 用户被迫切到 FastAPI | 不会发生 — Streamlit 完整保留，FastAPI 是新增不是替换 |

## 设计决策记录

- **目录名 `server/` 而非 `api/`**：避免与现有 `apis/`（XHS API 封装）目录混淆，且不占用 `web/` 命名（留给未来 SPA 工程）
- **端点路径 `/api/v1/health` 而非 `/health`**：URL 含版本号，未来 v2 平滑共存；`/api` 前缀让反向代理（Nginx / Cloudflare）易于路由
- **CORS 显式白名单 而非 `*`**：业界推荐 + 未来带 cookie 凭证时 `*` 失效，提前对齐
- **TestClient 验证而非 live server**：CI / 本地都能跑，无端口依赖
- **不做 OpenAPI 文档定制**：FastAPI 默认 `/docs` 已经够用，省一份代码

## 不在范围（明确拒绝）

- ❌ 任何业务端点（采集 / Goal / Persona / Agent 任务）— 下一 change
- ❌ 认证 / 鉴权 / API key — 下一 change
- ❌ 数据库连接池 / Redis 缓存 — 用到时再加
- ❌ 异步迁移现有 sync 代码（cookie_manager / requests / subprocess）— 接业务端点时再处理
- ❌ Web 前端工程（Next.js / SvelteKit）— 那是未来 `web/` 目录的事
- ❌ 部署配置（Docker / systemd / k8s）— 等业务端点稳定后再做

## 实施顺序

按 `tasks.md` 的章节执行：
1. 加依赖
2. 写 server/ 骨架
3. 写 verify 脚本
4. 跑测试
5. 文档同步
6. 用户手工 e2e（启动 uvicorn + curl /health）
7. 归档
