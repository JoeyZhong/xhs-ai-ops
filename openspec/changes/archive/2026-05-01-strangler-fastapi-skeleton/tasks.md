# Tasks: Strangler Fig FastAPI 骨架

按章节顺序执行，每节完成后建议验证再进下一节。

---

## 1 · 依赖

- [ ] 1.1 `requirements.txt` 加 `fastapi>=0.110`、`uvicorn[standard]>=0.27`、`httpx>=0.27`
- [ ] 1.2 `pip install -r requirements.txt` 验证安装无冲突

---

## 2 · server/ 骨架

- [ ] 2.1 新建 `server/__init__.py`（空文件，标记 package）
- [ ] 2.2 新建 `server/main.py`：
  - FastAPI 实例：`title`、`version="1.0.0"`、`description`
  - 配置 CORS 中间件：白名单 `["http://localhost:3000", "http://localhost:5173", "http://localhost:4321"]`，不允许 `*`
  - 注册 `GET /api/v1/health` 端点，返回 `{"status": "ok", "version": "1.0.0"}`
  - 模块顶部 docstring 强调：禁止添加业务端点、阻塞调用须 run_in_threadpool

---

## 3 · 验收脚本

- [ ] 3.1 新建 `verify_web_skeleton.py` 使用 `fastapi.testclient.TestClient`：
  - `/api/v1/health` 返回 200
  - 响应体 JSON 格式 `{status, version}`，status 为 `"ok"`
  - CORS 预检（OPTIONS）允许 `localhost:3000`
  - CORS 预检拒绝 `https://evil.example.com`
  - app 实例可正常 import
  - 未注册业务端点（grep 检查 server/main.py 不出现 `/goals` `/personas` 等）
  - 红线检查：`server/main.py` 不含 `subprocess` `requests.get` 等阻塞调用

---

## 4 · 验证（自动化）

- [ ] 4.1 跑 `verify_web_skeleton.py` 全部通过
- [ ] 4.2 跑 `verify_cookie_manager.py` 确保 36/36 不回归
- [ ] 4.3 跑 `verify_phase1_2.py` 确保 150/150 不回归
- [ ] 4.4 跑 `verify_phase3.py` 确保 95/95 不回归
- [ ] 4.5 grep `dashboard.py` 确认 0 行被改动（`git diff` 空）

---

## 5 · 用户手工 e2e（验证生产路径）

- [ ] 5.1 终端 1：`python -m streamlit run dashboard.py`（应正常）
- [ ] 5.2 终端 2：`python -m uvicorn server.main:app --reload --port 8000`
  - 启动日志含 `Application startup complete`
- [ ] 5.3 终端 3：`curl http://localhost:8000/api/v1/health`
  - 返回 `{"status":"ok","version":"1.0.0"}`
- [ ] 5.4 浏览器开 `http://localhost:8000/docs`
  - Swagger UI 显示 1 个端点 `/api/v1/health`
- [ ] 5.5 双进程同时跑无端口冲突、cookie_manager 读写不冲突

---

## 6 · 文档同步

- [ ] 6.1 `CLAUDE.md`：
  - 目录结构加 `server/main.py`
  - 「核心运行命令」加 `python -m uvicorn server.main:app --reload`
- [ ] 6.2 `docs/ARCHITECTURE.md`：
  - 章节 2「系统上下文」加进程拓扑：Streamlit (:8501) + FastAPI (:8000) 共存
  - 章节 8 已知架构债 G「dashboard.py 1700 行单文件」标注：Strangler Fig 第一步已落地
  - 章节 10 演进路径：Phase 4 调整为「Web 前端工程 + 业务端点暴露」
- [ ] 6.3 `requirements.txt` 注释段落说明：fastapi/uvicorn/httpx 用途

---

## 7 · 归档

- [ ] 7.1 spec 合入 `openspec/specs/web-api/spec.md`
- [ ] 7.2 整个 change 目录移到 `openspec/changes/archive/2026-05-XX-strangler-fastapi-skeleton/`
- [ ] 7.3 在 `docs/ARCHITECTURE.md` 添加「已交付架构里程碑」一节，记录本次落地
