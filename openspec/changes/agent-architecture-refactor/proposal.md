# Proposal: 重构为 Master-Sub Agent 协作架构

## Why

当前平台是「单文件 Streamlit + 多个 Python 脚本通过 subprocess 调起」的形态：
- 所有业务逻辑写死在 `dashboard.py`（1300+ 行）
- 脚本之间通过环境变量和 Excel 文件传递数据，无统一调度层
- AI 选题/生成等能力散落在 Streamlit 回调里，无法被复用、无法自动循环
- **没有反馈机制**：分析师的发现无法回流到生成端，下次生成依然犯同样错误
- 数据存储是本地 Excel，无法支持多用户/多账号隔离

继续往单文件加功能，复杂度指数上升，且天花板很低（不能自演进、不能多租户）。

## What

引入 **Master-Sub Agent** 架构，把现有脚本封装成 Tool，由 Agent 协作调用。
**架构概览：**

```
                ┌──────────────────────────────┐
                │   Master Agent: Hermes       │  ← OpenClaw 风格
                │   职责：安全调度、任务分发、    │     （安全/稳定/可控）
                │         审计、失败兜底         │
                └───────┬──────────────────────┘
                        │ delegate
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
  ┌───────────┐   ┌───────────┐   ┌───────────┐
  │ 情报 Agent │   │ 内容 Agent │   │ 分析 Agent │  ← Hermes 风格
  │ (Intel)   │   │ (Content) │   │ (Analyst) │     （自主进化）
  ├───────────┤   ├───────────┤   ├───────────┤
  │ tools:    │   │ tools:    │   │ tools:    │
  │  搜索爬虫  │   │  Kimi 生成 │   │  数据分析 │
  │  热词监控  │   │  标题公式 │   │  10-3-1  │
  │  竞品采集  │   │  人设管理 │   │  CES 计算 │
  └─────┬─────┘   └─────▲─────┘   └─────┬─────┘
        │ writes        │ reads          │ writes
        └───────────────┴────────────────┘
                        │
                ┌───────▼────────┐
                │  Memory Layer   │  ← Hermes 的冻结快照模式
                │  (markdown +    │     分析师写发现 → 内容官下次自动读
                │   Supabase)     │
                └─────────────────┘
```

## 范围（What's in / What's out）

**In scope：**
1. **Master Agent (Hermes)** — Python 实现，参考 OpenClaw 的安全模式（tool policy、参数校验、审计日志、子任务清理）
2. **3 个 Sub Agent** — 情报 / 内容 / 分析，每个都有独立的 perceive→think→act 循环
3. **工具注册中心** — `tools/registry.py` 模式，把现有脚本（run_search / hot_trend_monitor / content_generator / browser_search）封装为 Tool
4. **Feedback Loop** — 分析师 Agent 的结论通过 Memory 系统回流到内容官 Agent 的 system prompt（下次 session 生效）
5. **Supabase 适配层** — 抽象存储接口，本地 JSON / Supabase 双 backend，schema 支持多租户
6. **Streamlit dashboard 改造** — 从「直接调脚本」变成「通过 Master 提交任务」

**Out of scope（后续 change 处理）：**
- Web 前端取代 Streamlit
- 多用户登录/权限系统（先做数据层隔离，UI 暂不动）
- Agent 间的实时事件总线（先用同步调用 + 消息队列文件，后续可换 Redis/NATS）
- Fine-tune 模型（自演进通过 prompt+memory 实现，不动模型权重）

## Impact

**新增能力（capability spec）：**
- `agent-architecture` — Master/Sub Agent 设计
- `agent-tools` — 工具注册、调度、安全约束
- `feedback-loop` — Memory 系统与自演进
- `multi-tenant-storage` — Supabase 适配与租户隔离

**改造现有代码：**
- `dashboard.py` — 从直接 subprocess 改为通过 Master 提交任务
- `run_search.py` / `hot_trend_monitor.py` / `content_generator.py` — 拆出核心函数，包装为 Tool（保留 CLI 入口向后兼容）
- `config/goals.json` / `persona.json` — schema 不变，存储后端可切换
- `xhs_data/` — 仍作为本地 fallback，新主流是 Supabase

**不影响：**
- 已有 Cookie 反爬保护、频率限制、浏览器兜底逻辑（这些都封装到 Tool 里）
- Kimi API 调用方式（仍走现有 `kimi_call` 函数）

## Risk

| 风险 | 缓解 |
|------|------|
| 重构期间 dashboard 不可用 | 分阶段：先建 Agent 框架，老 dashboard 继续用；Tool 包装好后并行测试；最后切换 |
| Agent 之间的循环调用/失控 | 借鉴 OpenClaw 的 max_iterations + token budget；Master 是唯一调度入口 |
| Memory 系统污染（注入攻击） | 借鉴 Hermes 的 entry pattern + injection detection |
| Supabase 集成复杂度 | 设计期就抽象出 Storage interface，先 ship 本地 backend |
| 任务粒度太大无法 ship | 拆成 4 个 phase，每个 phase 独立可用 |

## 参考实现

- OpenClaw — `D:\【AIcode】\openclaw-main`（TS，借鉴模式不抄代码）
- Hermes Agent — `D:\【AIcode】\hermes-agent-main`（Python，可直接借代码）

详细架构在 `architecture_spec.md`。
