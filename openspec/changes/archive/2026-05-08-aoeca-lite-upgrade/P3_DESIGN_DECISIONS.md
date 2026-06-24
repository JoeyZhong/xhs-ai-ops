# P3 设计决策（Pre-Implementation Lock-in）

**Date**: 2026-05-07
**Status**: Locked, ready for implementation
**Author**: 用户拍板（Q1-Q4），承接 P0/P1/P2 已交付状态

---

## 上下文

P3 实施前需敲定 4 个跨进程/跨模块边界设计点。开工前用户已逐项决策。后续模型按此文档执行 `tasks.md` P3.1 ~ P3.5，**不再追问这 4 个点**。

---

## D1 · Scheduler 进程归属 → FastAPI lifespan

**决策**：BackgroundScheduler 挂载在 `server/main.py` FastAPI lifespan，**不**挂 Streamlit。

**理由**：
- Streamlit Top-down Rerun 模型 → 会话级重建，挂常驻 cron 极度不稳
- FastAPI 是 daemon-like 常驻进程，与 Agent/DAG 同内存空间，状态互通自然
- F4 Streamlit 退役后 scheduler 不需要迁移

**实施落地**：
- `agents/scheduler.py`：包装 `apscheduler.schedulers.background.BackgroundScheduler`
- `server/main.py` 用 `@asynccontextmanager` lifespan 启动/停机
- File lock：`xhs_data/.scheduler.lock`（防 `uvicorn --workers >1` 重入）
- `tasks.md::P3.1.2` 原文「dashboard.py 首次加载触发」**作废**，改为 FastAPI lifespan

**影响的子任务**：P3.1.1 / P3.1.2 / P3.1.3

---

## D2 · Draft Review UI → API-First，跳过 Streamlit

**决策**：P3.2.D6-D8（dashboard 待审阅 draft UI）**作废**。改为暴露 RESTful API，UI 留给 frontend-refactor F3.6 (Next.js)。

**理由**：
- Streamlit 即将退役（F4），在废弃栈上做 Review 流是沉没成本
- API-first 强制先理清数据契约，前后端彻底解耦

**API 契约**（替代 D6-D8）：

```
GET    /api/v1/playbook/drafts                    → list of drafts
POST   /api/v1/playbook/drafts/{id}/accept        → status=active, rev+1
POST   /api/v1/playbook/drafts/{id}/reject        → status=rejected, rev+1
PUT    /api/v1/playbook/drafts/{id}               → edit body, status=active, rev+1
GET    /api/v1/playbook/drafts/count              → unread count（顶部红条数据源）
```

**实施落地**：
- 新文件 `server/routers/playbook.py`
- 复用 `agents/memory.py` 的 OCC（read rev → write expected_rev）
- D9 单元测试改为 pytest（`tests/test_p3_playbook_api.py`）

**影响的子任务**：P3.2.D6 / D7 / D8 → 全部转成 API 任务；D9 单元测试目标不变

---

## D3 · cookie_health_check 数据隔离 → 独立目录

**决策**：探活产物写 `xhs_data/_health/`，**不**给 `search.collect_notes` 加 `dry_run` 参数。

**理由**：
- dry_run 需穿透爬虫底层多层调用，改动面大、Bug 风险高
- 独立目录方案改动极小，且天然提供"健康探针日志"用于后续故障分析

**实施落地**：
- 新 cron handler 在 `agents/scheduler.py` 中：
  ```
  res = search.collect_notes(keyword="测试", limit=1, output_dir="xhs_data/_health/")
  ```
- `agent_tools/search.py::collect_notes` 加可选 `output_dir` 参数（不破坏旧调用）
- 失败 → 写 `xhs_data/_health/cookie_alert.json`（schema：`{ts, error, last_success}`）
- **保留最近 3 份健康检查快照**，更早的自动清理（防磁盘碎片）

**影响的子任务**：P3.3.1 / P3.3.2

---

## D4 · Weekly Evaluator 数据缺失 → Graceful Degradation

**决策**：性能数据缺失时 fallback 到 audit-only，draft entry 标 `confidence: "low"`。

**理由**：
- 用户每周手填 performance 不现实，数据缺失是常态
- 系统在数据不全时依然产出，UX 上反向催促用户补数据（克制的产品设计）

**实施落地**：
- `agents/evaluators.py::AnalystEvaluator.assemble_prompt()`：
  ```python
  has_perf = bool(goal.performance.posts)
  if has_perf:
      prompt = "<full prompt with audit + performance + playbook>"
      confidence = "high"
  else:
      prompt = "<audit + playbook only, 提示用户补充 performance 数据>"
      confidence = "low"
  ```
- Draft entry 元字段扩展：`§confidence: high|low`（沿用 P3.2.D2 行头扩展模式）
- D6-D8 替代 API 在 list 接口返回 `confidence` 字段，前端 F3.6 可视化区分

**影响的子任务**：P3.2.1（prompt 组装逻辑）/ P3.2.D1-D3（元字段加 confidence）

---

## 跨决策影响汇总

| 子任务 | 状态 |
|---|---|
| P3.1.1-3 | ✅ 按 D1 调整 — FastAPI lifespan + .scheduler.lock |
| P3.2.1 | ✅ 按 D4 调整 — has_perf 分支 + confidence |
| P3.2.D1-D3 | ✅ 按 D4 加 confidence 元字段 |
| P3.2.D4-D5 | ✅ 不变 — Content 跳过 draft，Analyst 全读 |
| P3.2.D6-D8 | 🔄 重写 — 改为 API（playbook.py），UI 留 F3.6 |
| P3.2.D9 | ✅ 改 pytest 验证（不再依赖 Streamlit） |
| P3.3.1-2 | ✅ 按 D3 改 output_dir + 保留 3 份快照 |
| P3.4 (Console 集成) | ⚠️ 重新评估 — Streamlit 部分跳过，留 Next.js F3.6 |
| P3.5 验证 | ✅ verify_phase5_p3.py 仍按原计划，新增 API 端到端 case |

---

## 实施顺序（建议）

1. **P3.1**（scheduler + lifespan）— 无业务依赖，先打地基
2. **P3.2.D1-D5**（memory 元字段 + Content/Analyst prompt 分流）— 数据模型先动
3. **P3.2.1-4**（AnalystEvaluator + Master.submit 触发）— 业务逻辑
4. **P3.2.D6-D8** API（playbook router）— 暴露给 F3.6
5. **P3.3**（cookie_health_check）— 独立 cron
6. **P3.5** 验证脚本 + 手工 cases

---

## 红线（不可越过）

- ❌ 不再在 Streamlit 上加新功能（Q2 决策）
- ❌ 不破坏 `search.collect_notes` 旧调用方（Q3：output_dir 必须可选）
- ❌ Content Agent system prompt 严禁注入 `status=draft` entry（D4 安全边界）
- ❌ Scheduler 启动失败 → FastAPI 必须仍能启动（lifespan 内 try/except，不阻塞主服务）
