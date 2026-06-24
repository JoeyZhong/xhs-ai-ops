# 项目背景

## 是什么
**小红书内容制作平台** — 服务深圳本土自助售卖机运营商「示例品牌」的全链路内容运营工具。
目标客群：深圳工厂、写字楼、学校等场地方（B端），通过小红书内容吸引点位合作。

## 技术栈
- **前端**：Next.js 14（App Router，Tailwind，Zustand），逐步替换 Streamlit
- **后端**：FastAPI（`server/main.py`），Strangler Fig 迁移中，与 Streamlit 共存
- **采集**：自研 XHS 爬虫（apis/xhs_pc_apis.py + JS 签名）+ Playwright 浏览器兜底
- **AI**：Kimi / Moonshot API（moonshot-v1-32k），LLMProvider 抽象支持 Mock/Failover
- **Agent**：HermesMaster + Intel/Content/Analyst Sub Agent + GOAP scratch_pad + 免疫压缩
- **数据**：Excel（pandas）本地存储 + SQLite WAL（Cookie）+ 规划 Supabase（Phase 4）
- **配置**：JSON（config/goals.json / personas.json / settings.json）

## 关键模块
| 文件 | 职责 |
|------|------|
| `dashboard.py` | Streamlit 主程序（遗留，逐步迁移中） |
| `server/main.py` | FastAPI 后端（health + DAG + SSE + Playbook API） |
| `agents/master.py` | HermesMaster（Agent 调度 + DAG + 安全网关） |
| `agents/base.py` | AgentBase（GOAP 主循环 + 免疫压缩 + spotlighting） |
| `agents/compression.py` | 状态感知上下文压缩引擎 |
| `agents/task_ledger.py` | DAG 任务编排（拓扑排序 + 死锁检测 + 状态机） |
| `agents/scheduler.py` | BackgroundScheduler（APScheduler 包装） |
| `agents/evaluators.py` | AnalystEvaluator（自动周报生成） |
| `agents/memory.py` | MemoryLayer + OCC（乐观并发控制） |
| `agent_tools/registry.py` | Tool 注册中心（JSON Schema + Idempotency 中间件） |
| `agent_tools/llm_provider.py` | LLMProvider 抽象（Kimi / Mock / Failover） |
| `agent_tools/idempotency.py` | Tool 幂等缓存（24h SHA256） |
| `storage/cookie_manager.py` | Cookie 集中管理（SQLite WAL，多账号隔离） |
| `xhs_utils/safe_run.py` | Subprocess 沙箱（timeout + kill_tree + rlimit） |
| `run_search.py` | 爬虫，按关键词采集笔记（API + 浏览器兜底） |
| `hot_trend_monitor.py` | 热词监控（API + 浏览器兜底） |
| `content_generator.py` | Kimi 批量生成笔记内容 |
| `browser_search.py` | Playwright 浏览器兜底模块 |
| `apis/xhs_pc_apis.py` | XHS API 封装（外部库代码，不动） |

## 关键约束（不可违反）

### 反爬保护
- 单次采集 ≤ 50 条（5 关键词 × 10 条）
- 关键词间随机延迟 3-6 秒
- 监控/采集间隔 ≥ 30 分钟（< 30 硬阻断），< 2h 需勾选风险确认
- 浏览器兜底成功后**自动回写新 Cookie**到脚本文件

### 数据生命周期
- 采集结果、热词、生成内容**保留 7 天**
- 启动时自动清理超期文件
- 周数据聚合用于 AI 决策（去重 + 多次合并）

### 内容生成
- 必须使用 `goals.json` 中的人设和已用角度
- 已用角度永远回避（防止重复选题）
- 每次生成前先生成投放策略，用户确认后再批量生成

## 数据流
```
采集（API/浏览器） → xhs_data/spider_*.xlsx
        ↓
热词监控 → xhs_data/hot_trends_*.xlsx
        ↓
周数据聚合 → AI 选题/策略 prompt 上下文
        ↓
Kimi 生成内容 → xhs_data/generated_content_*.xlsx
        ↓
人工发布 → 数据回流到 goals.json["performance"]["posts"]
        ↓
（10-3-1 模型迭代下次选题）
```

## 历史决策摘要
- **不用 kimi-for-coding 模型**：仅 coding agent 可用，standalone 脚本会 403
- **Cookie 写在脚本顶部而非 .env**：方便用户手动更新（浏览器F12复制粘贴）
- **多目标隔离用 env var 而非 CLI 参数**：dashboard 启动子进程时传 GOAL_ID/KEYWORDS_JSON
- **XHS 字段名修正**：`display_title`（不是 title）、`shared_count`（不是 share_count）、`sug_items`（不是 suggest_words）
