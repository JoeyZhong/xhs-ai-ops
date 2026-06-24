# Spec Delta: web-api

> 新建 capability。所有条款 `## ADDED`。
> 这是 Strangler Fig 迁移的第一份 spec — 立基线 + 立红线。

---

## ADDED Requirement: HTTP API 进程独立

系统 SHALL 提供一个独立的 FastAPI 进程作为 Web/HTTP 接入层，与 Streamlit
（`dashboard.py`）共存。两个进程通过共享文件系统（`config/` / `xhs_data/` /
`memory/` / `config/cookies.db`）协调，互不阻塞。

### Scenario: 双进程共存
- **GIVEN** 用户运行 `python -m streamlit run dashboard.py`（端口 8501）
- **AND** 用户运行 `python -m uvicorn server.main:app --port 8000`
- **THEN** 两个进程同时正常运行
- **AND** 都能正常读 `config/personas.json` / `config/cookies.db`
- **AND** SQLite WAL 模式保证 cookie 写并发不损坏

### Scenario: server/ 目录命名约束
- **WHEN** 在仓库根目录扫描后端代码目录
- **THEN** FastAPI 后端代码 MUST 位于 `server/`
- **AND** 不得使用 `api/`（与现有 `apis/` 即 XHS API 封装目录混淆）
- **AND** 不得使用 `web/`（保留给未来 SPA 前端工程）

---

## ADDED Requirement: /health 健康检查端点

系统 SHALL 提供 `GET /api/v1/health` 端点用于服务可用性探测。

### Scenario: 健康检查返回标准结构
- **WHEN** 客户端发起 `GET /api/v1/health`
- **THEN** 返回 HTTP 200
- **AND** 响应体为 JSON 对象 `{"status": "ok", "version": "..."}`
- **AND** 端点无副作用、不读 DB、不调外部 API

### Scenario: URL 含版本号
- **WHEN** 接口 URL 被规划
- **THEN** 路径 SHALL 以 `/api/v1/` 开头
- **AND** 后续 v2 版本可 `/api/v2/...` 平滑共存
- **AND** 反向代理（Nginx / Cloudflare）可按 `/api/*` 一条规则路由

---

## ADDED Requirement: CORS 严格白名单

系统 SHALL 配置 CORS 中间件，**不允许** `allow_origins=["*"]`。
v1 白名单：

```
http://localhost:3000   # Next.js 默认
http://localhost:5173   # Vite 默认
http://localhost:4321   # Astro 默认
```

### Scenario: 白名单内的 Origin 被允许
- **WHEN** 浏览器从 `http://localhost:5173` 发起预检请求
- **THEN** 响应 `Access-Control-Allow-Origin: http://localhost:5173`
- **AND** 业务请求被允许

### Scenario: 白名单外的 Origin 被拒绝
- **WHEN** 浏览器从 `https://evil.example.com` 发起请求
- **THEN** 响应不包含 `Access-Control-Allow-Origin: https://evil.example.com`
- **AND** 浏览器同源策略阻断业务请求

### Scenario: 禁止使用通配符
- **WHEN** grep 仓库 `allow_origins\s*=\s*\[\s*"\*"`
- **THEN** 0 个匹配
- **AND** 即使带 `allow_credentials=False`，仍禁止使用 `*`

---

## ADDED Requirement: 阻塞调用必须 run_in_threadpool（红线）

业务代码（含未来添加的端点 handler）调用任何**同步阻塞**操作时
SHALL 使用 `fastapi.concurrency.run_in_threadpool` 包装。

裸调以下阻塞 API 在 async handler 中是**严重错误**：
- `subprocess.Popen` / `subprocess.run`
- `requests.get` / `requests.post`
- `sqlite3.connect`（含 `cookie_manager.get_cookie` 等）
- `pandas.read_excel` / `pd.DataFrame.to_excel`
- `playwright.sync_api.sync_playwright`
- `PyExecJS` (XHS 签名)

### Scenario: 正确做法
- **GIVEN** 一个 async handler 需要调用 `cookie_manager.get_cookie`
- **WHEN** 编写代码
- **THEN** MUST 写为：
  ```python
  from fastapi.concurrency import run_in_threadpool
  cookie = await run_in_threadpool(get_cookie, account_id)
  ```

### Scenario: 错误做法（禁止）
- **GIVEN** 一个 async handler
- **WHEN** 直接 `cookie = get_cookie(account_id)` 或裸调任何阻塞函数
- **THEN** 这是 spec 违规
- **AND** code review MUST 拒绝

### Scenario: v1 不会触发（暂时安全）
- **GIVEN** v1 仅含 /health（无任何阻塞调用）
- **WHEN** 接口被压测
- **THEN** 事件循环不阻塞
- **AND** 后续 PR 加业务端点时本红线立即生效

---

## ADDED Requirement: v1 仅含 /health（红线）

本 capability v1 SHALL 仅包含 `GET /api/v1/health` 一个端点。
任何业务端点（Goal / Persona / 采集 / Agent 任务等） MUST 在独立 change 中提案。

### Scenario: v1 端点白名单
- **WHEN** 列出 FastAPI app 的所有路由
- **THEN** 业务端点数量 = 0
- **AND** 仅有 `/api/v1/health` + FastAPI 自带的 `/docs` `/redoc` `/openapi.json`

### Scenario: 反向防偷加业务端点
- **WHEN** grep `server/main.py` 匹配 `@app.(get|post|put|delete|patch)`
- **THEN** 仅出现 1 次（health 端点）
- **AND** 不出现 `/goals` `/personas` `/collect` `/cookie` `/agent` 等业务路径

---

## ADDED Requirement: 不引入认证（v1 暂缺）

v1 SHALL 不实现任何认证 / 鉴权机制。
/health 是无认证公开端点（监控用）。

业务端点上线时，MUST 在独立 change 中加入：
- 认证方案（推荐 OAuth2 / Bearer Token）
- 速率限制
- 多租户隔离（Phase 4 Supabase 配套）

### Scenario: v1 端点免认证
- **WHEN** 客户端不带任何凭证 `curl http://localhost:8000/api/v1/health`
- **THEN** 返回 200

### Scenario: 业务端点引入认证前不得 ship
- **WHEN** 任何 PR 添加非 /health 端点
- **THEN** SHALL 同时引入认证机制
- **AND** 否则 review 不通过

---

## ADDED Requirement: Streamlit 完整保留

本 change SHALL 不修改 `dashboard.py` 任何一行。
Streamlit 路径作为对照基准与回退方案保留。

### Scenario: dashboard.py 零改动
- **WHEN** 本 change 完成
- **THEN** `git diff dashboard.py` 输出为空
- **AND** Streamlit 启动 `python -m streamlit run dashboard.py` 完整可用

---

## ADDED Requirement: 验收脚本

系统 SHALL 提供 `verify_web_skeleton.py` 验收脚本，使用
`fastapi.testclient.TestClient` 验证：

- /health 端点正常响应
- CORS 白名单生效
- 红线规则被遵守（grep 检查）
- 不存在阻塞调用 import

### Scenario: 验收通过
- **WHEN** 运行 `python verify_web_skeleton.py`
- **THEN** 全部 case 通过
- **AND** 退出码为 0

### Scenario: 全量回归
- **WHEN** 同时跑 `verify_phase1_2 / verify_phase3 / verify_cookie_manager`
- **THEN** 281 个旧测试 + 新增的 web_skeleton 测试全部通过
- **AND** 0 回归
