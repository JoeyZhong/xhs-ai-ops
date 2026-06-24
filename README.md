<div align="center">

中文 | [English](README.en.md)

# XHS AI Ops · 小红书全链路 AI 内容运营平台

**一个从真实业务痛点出发、亲手从 0 到 1 搭起来的 Agent Harness —— 把 LLM 包装成能在真实场景里持续帮到人的产品。**

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-chat--first-000000?logo=nextdotjs&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-multi--tenant-4169E1?logo=postgresql&logoColor=white)
![LLM](https://img.shields.io/badge/LLM-OpenAI--compatible-412991)

</div>

---

## 一句话

为了解决自己每天"刷小红书找客户、手动选题、逐条发布"的真实痛点，我从 0 到 1 搭了这个系统：**底层**封装平台读写，**中层**用多 Agent 协作完成「情报采集 / 内容生成 / 数据分析」，**上层**用一个 chat-first 前端，把复杂的 Agent 编排收进一句自然语言。

它的内核，本质上就是一套 **Agent Harness**——模型之外，让 Agent 在真实场景里真正可用、可信、可观测的全部工程：Agent Loop、Tool Use、Multi-Agent 协作、Context Engineering、Memory、Skills、失败兜底。这个项目是我对 **"Model + Harness = Agent"** 的第一手实践。

> 它的价值不在"能跑通"，而在：**从一个模糊的真实需求出发，把它拆成可编排、可治理、可观测的产品系统，并对每一处异常分支、边界条件、失败场景负责。**

---

## 它解决的真实问题

不是"造一个 Agent 玩具"，而是回答一个产品问题：**怎么让一个 Agent，在真实、长期、会出错的场景里，持续地、更深入地帮到人？**

- **真实任务，不是 demo**：跑的是我自己生意里的真任务——找潜在客户、定选题、写内容、复盘数据。每一个 Agent 的好坏，都直接由"这周有没有帮我省下时间、带来线索"来检验。
- **对失败场景的嗅觉**：平台风控、Cookie 失效、LLM 返回空、软限流（成功响应但空数据）、工具重复触发……这些"异常分支"我都单独识别并兜底，而不是只跑 happy path。
- **用数据驱动迭代**：内容效果用 CES 加权模型量化，线索质量用意图匹配分打分，采集→合格→转化做成可观测的漏斗——让"产品有没有变好"是可度量的，而不是凭感觉。

---

## 核心：这是一套 Agent Harness

用这个团队的语言来描述它的组成（每条都标了"做了什么 + 难点"）：

- **Multi-Agent / Subagent** — `HermesMaster` 主调度兼安全网关：Sub-Agent 必须经它实例化（token 校验防越权直调），按角色用 `ToolPolicy` 控制工具白名单，每阶段写审计。*难点：让多个 Agent 协作而不失控。*
- **Agent Loop + Planning** — Agent 主循环基于 GOAP（目标导向行动规划）+ scratch_pad；复杂意图先拆成 DAG 再编排执行。*难点：让 Agent 有"先规划再行动"的结构，而不是一步到底。*
- **Context Engineering** — 上下文超长时用"状态感知压缩"，对关键状态设**免疫区**不被压掉。*难点：长任务里既省 token 又不丢关键状态——这是 Harness 最核心的课题之一。*
- **Tool Use（工具治理）** — 统一工具注册中心（JSON Schema 校验 + LLM-safe 命名）+ 幂等中间件（防重复副作用）+ `LLMProvider` 抽象（多模型 / Mock / Failover）。*难点：把"调工具"做成可治理、可测试、可替换的基础设施。*
- **Memory** — 分命名空间的记忆层（shared / 各 Agent 私有），Analyst 写、Content 读的 playbook 闭环。*难点：让 Agent 跨会话积累并复用经验。*
- **Skills** — 单文件 YAML frontmatter 的 skill 格式，对齐 Claude Code / Agent SDK 的跨生态标准，可被上层工具链引入。
- **失败兜底** — Cookie 失效自动转浏览器自动化；子进程沙箱（超时 + 资源限制）；软限流识别后主动降级、不再加压。

---

## 关键产品决策

> 这一节是给会深挖的人看的——比"用了什么"更重要的是"为什么这么取舍"。

- **为什么反封号靠节奏而非签名**：平台风控的根因是请求的量/频率/行为，不是签名对错。所以在采集层做请求间随机抖动、调度层做频控（≤3 次/天、≥2h），并识别"成功但空数据"的软限流信号——对平台保持低扰动，是产品能长期跑下去的前提。
- **为什么线索定义比扫描量更重要**：用户最初以为"扫到的帖子太少"，我逐层量化漏斗后发现：采集量充足，瓶颈在意图判定。真正的杠杆是**重新定义"什么算合格线索"**，而不是盲目加量——这是"理解最真实需求"的一次实践。
- **为什么用 Strangler-Fig 渐进迁移**：已有运营台在用，重写风险高。选择新功能进 FastAPI/Next.js、旧页按路由逐个搬、可秒级回滚——在不停机的前提下演进。
- **为什么工具调用要幂等**：前端重渲染 / 用户误触会重复触发带副作用的工具。幂等中间件按 `(tool, args, role)` 去重，让"重复调用"安全——这是对边界条件的防御。

---

## 架构

```
              ┌─────────────────────────────────────────────────────────┐
   前端       │  Next.js chat-first 门面（自然语言入口 + SSE 真 token 流式）│
              └───────────────────────────┬─────────────────────────────┘
              ┌───────────────────────────▼─────────────────────────────┐
   API        │  FastAPI（Strangler-Fig 渐进迁移，与 Streamlit 运营台共存）│
              └───────────────────────────┬─────────────────────────────┘
              ┌───────────────────────────▼─────────────────────────────┐
   Harness    │  HermesMaster 编排 + 安全网关 + 审计 + DAG                │
              │    ├ IntelAgent / ContentAgent / AnalystAgent（Subagent）│
              │    └ GOAP · Context 压缩 · Tool Registry · Memory · Skills│
              └───────────────────────────┬─────────────────────────────┘
              ┌───────────────────────────▼─────────────────────────────┐
   数据       │  平台 API 封装 · LLM Provider 抽象 · PG 多租户 · 本地兜底 │
              └─────────────────────────────────────────────────────────┘
```

| 层 | 选型 |
|---|---|
| 前端 | Next.js（chat-first） · TypeScript · SSE 流式 |
| API | FastAPI（async） · JWT 鉴权 · SSE |
| Harness | 自研多 Agent 框架（GOAP + 上下文压缩 + DAG + 工具注册中心 + Memory + Skills） |
| LLM | OpenAI 兼容接口（多 Provider / Mock / Failover） |
| 存储 | PostgreSQL（多租户 RLS + pgcrypto） · 本地 JSON/Excel 兜底 |
| 兜底 | Playwright 浏览器自动化 · 子进程沙箱 |

---

## 快速开始

```bash
pip install -r requirements.txt && npm install
cp .env.example .env                          # 填平台 Cookie
cp config/settings.example.json config/settings.json   # 填 LLM API Key
python -m uvicorn server.main:app --reload --port 8000  # 后端 :8000
cd frontend && npm run dev                    # 前端 :3000
```

---

## 项目状态（诚实版）

这是一个持续迭代的个人工程项目，按真实生产标准设计，但各模块成熟度不同——对异常分支和失败场景的处理是认真的，但不夸大成熟度：

| 模块 | 状态 |
|---|---|
| 多 Agent Harness（编排 / GOAP / 压缩 / 工具治理 / Memory / Skills） | 已实现，有分阶段验收脚本覆盖 |
| FastAPI 后端 + JWT + SSE 流式 | 已实现 |
| Next.js chat-first 前端 + 真 token 流式 | 已实现 |
| PostgreSQL 多租户（RLS + pgcrypto） | 已实现，生产 cutover 前仍在打磨 |
| 线索雷达（获客自动化） | 核心闭环已通，多信源 / 一键触达迭代中 |

---

## 致谢与声明

- 数据层的小红书 API 与签名封装，基于开源项目 [**cv-cat/Spider_XHS**](https://github.com/cv-cat/Spider_XHS)（MIT）。本仓库在其之上构建了完整的多 Agent Harness 与产品系统（Agent 框架 / API / 前端 / 多租户存储均为本人实现），原始 LICENSE 已保留。
- **本项目仅供学习与技术研究**，请遵守目标平台服务条款，**请勿用于商业用途**，后果自负。
- 不含任何真实密钥或业务数据，配置均以 `*.example` 形式提供。

---

## 截图

| 主助手（门面） | Agent Console |
|---|---|
| ![chat](docs/screenshots/chat-home.png) | ![console](docs/screenshots/agent-console.png) |

| 线索雷达收件箱 |
|---|
| ![radar](docs/screenshots/lead-radar.png) |
