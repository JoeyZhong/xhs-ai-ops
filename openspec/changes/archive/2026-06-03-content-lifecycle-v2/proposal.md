# Proposal: content-lifecycle-v2 内容闭环深化（P1+P2+P3）

> **创建日期**：2026-05-28
> **触发**：`content-lifecycle-v1` 已归档 + P0 hotfix（commit `226a052`）已让 `goal.overall_strategy` 接入 `/content/strategy`/`/content/generate` prompt 主链路；下一步深化闭环
> **依据**：`docs/PRD_V1_1_SELF_LEARNING_XHS_AGENT_PLATFORM.md` §R3、R4、R7、R10；`docs/superpowers/plans/2026-05-27-content-loop-p0.md` §"分阶段落地建议" P1/P2/P3
> **版本链**：`content-lifecycle-v1`（已归档）→ **content-lifecycle-v2（本 change）** → V1.2 知识库 → V1.3 Orchestrator MVP（R12，本 change 是其数据基础）

---

## Problem Statement

`content-lifecycle-v1` 把 Topic/Strategy/Draft/Calendar 的状态机和 API 建好了，P0 hotfix 把 `goal.overall_strategy` + 包装公式接进了 prompt，让目标对齐里配置的内容真的影响产出。但运营人 5 个工作模块的"闭环"还有 3 个明显断点：

1. **包装公式是只读硬编码**——`memory/_universal/packaging_rules.md` 已经是 source of truth（P0 引入），但前端 `/packaging` 页仍是只展示 `frontend/app/(main)/packaging/page.tsx` 里的常量；运营人改了 markdown 文件 prompt 立刻生效，但**无法在 UI 上编辑**，只能改文件。
2. **市场洞察 → 选题/策略 是单向死信**——`/insight` 采集的 `collected_notes` 只供运营人浏览，没有"提取爆款样本 → 写入 topic.evidence_refs → 喂进内容创作 prompt"的回流路径；高 CES 的爆款规律没法自动喂给 ContentAgent。
3. **CES → playbook 学习闭环未通**——`AnalystEvaluator` 周报基础设施在 `agent-architecture-refactor` Phase 3 已完工，但只写运营报告，**未输出到 `memory/content/playbook.md`**；`ContentAgent` prompt 没读 playbook；`used_angles` 字段是负向标记（"⚠️ 此角度已使用过"），缺正向"✅ 已验证爆款" / "❌ 沉底"三态。

后果：Orchestrator（V1.3）一旦启动，它"主动建议"的数据源（evidence_refs / playbook / 三态 used_angles）都不存在，建议会沦为模板套话。

## Solution

按 PRD R3/R4/R7/R10 的边界，新增 3 个紧耦合子能力，全部归一到本 change：

- **P1 · Packaging Rules Editor**：`/packaging` 改成 `memory/_universal/packaging_rules.md` 的编辑器；新增 `/api/v1/packaging/rules` GET/PUT；编辑后 `agent_tools/packaging_rules.py` 的 mtime 缓存自动失效（已具备）。
- **P2 · Insight Evidence Pool**：`/insight` 加「提取爆款样本」按钮 → 新增 `/api/v1/intel/evidence/extract` POST（对 CES > 阈值的 notes 调 Kimi 抽 `{angle, hook, key_insight}`）→ 写入 PG 新表 `content_evidence`；`/content/strategy` 和 `/content/generate` 的 prompt 拼装从 `prompt_context` 增取同 funnel / 同 angle 的 top-3 evidence。
- **P3 · CES → Playbook 学习闭环**：扩 `AnalystEvaluator` 写 `memory/content/playbook.md`（top-3 角度 + 沉底角度）；`ContentAgent` prompt 读 playbook；`goal.used_angles` 升级为 `{angle, status: "validated_hit"|"sunk"|"unknown", evidence_count, last_ces}` 三态结构；`/api/v1/analytics/performance` POST 端点支持把发布数据写回。

## 涵盖需求项

| PRD 需求 | 范围 | 优先级 | 本 change 落点 |
|---|---|---|---|
| R10 策略与角度模板库 | 包装公式可视化编辑 | P1 | `/api/v1/packaging/rules` + 重写 `/packaging` 页 |
| R3 市场洞察 → 选题/策略 memory | 爆款样本提取 + evidence_refs 注入 prompt | P1 | `/api/v1/intel/evidence/*` + 新表 `content_evidence` + `prompt_context` 扩展 |
| R9 IntelAgent 增强 | evidence 提取作为 IntelAgent 新 tool | P2 | `agent_tools/intel_evidence.py` 注册 `intel.extract_evidence` |
| R4 performance 回流 memory | 发布数据回填驱动 playbook 更新 | P1 | `/api/v1/analytics/performance` POST + AnalystEvaluator 扩 playbook 写入 |
| R7 学习闭环 | playbook 喂回 ContentAgent + used_angles 三态 | P0 | `ContentAgent.prompt` 读 playbook + `goal.used_angles` schema 升级 |

R12 Orchestrator MVP **不在本 change**——V1.3 启动时直接读本 change 沉淀的 evidence + playbook + 三态 used_angles 作为决策依据。

## Out of Scope（明确不做）

- Orchestrator 主助手本体（V1.3）
- 知识库索引（V1.2）
- 自动发布（PRD R13，P3，风控原因近期不做）
- 浏览器自动化替代方案调研（V1.4 ADR-0002 范畴）
- Streamlit 端的对应改造（继续保持只读，新链路只在 Next.js）
- evidence 提取的 vector 检索 / 多 provider；本 change 用关键词匹配 + 简单 ranking

## Impact

**新增**：
- 1 个 PG 迁移：`db/migrations/008_content_evidence.sql`（表 `content_evidence`）
- 1 个 PG 迁移：`db/migrations/009_used_angles_tristate.sql`（`goals.used_angles` 从字符串数组改为 JSONB 数组）
- 3 个新 API 端点组：`/api/v1/packaging/rules`、`/api/v1/intel/evidence/*`、`/api/v1/analytics/performance`
- 1 个新 storage 方法：`backend.list_evidence(...)`、`upsert_evidence(...)`、`record_performance(...)`
- `agent_tools/prompt_context.py` 扩展 `evidence_refs` 字段
- `agent_tools/intel_evidence.py` 新 tool 注册到 Tool Registry
- `AnalystEvaluator` 扩 playbook 写入路径
- `ContentAgent` system prompt 模板新增 playbook 段
- 前端 3 个页面改造：`/packaging`（重写为编辑器）、`/insight`（加按钮 + 列表）、`/content`（消费 evidence 不需 UI 改）；`/goals` 视图 `used_angles` 三态展示

**修改**：
- `goals.used_angles` schema 从 `list[str]` → `list[dict]`（需 migration 兼容老数据）
- `prompt_context.build_strategy_prompt_context` 增 `evidence_refs` 输出字段
- `/content/strategy` 和 `/content/generate` 后端 prompt 拼装新增 evidence 段

**Strangler 原则**：
- 不动 Streamlit 老 dashboard.py（继续只读）
- 不动 v1 已交付的 Topic/Strategy/Draft/Calendar 模型，只扩
- 老 `used_angles: list[str]` 字段保留兼容 read 路径，migration 把字符串数组 wrap 成 `[{angle, status: "unknown"}]`

## Risk

| 风险 | 缓解 |
|---|---|
| evidence 抽取的 Kimi 成本不可控（每条 note 一次调用）| 引入 batch 模式（10 条 notes 1 次调用）+ 后端缓存 evidence by `(tenant, note_id)` |
| `used_angles` schema 改动可能让旧前端炸 | migration 加兼容字段 `used_angles_legacy: list[str]`；前端读时优先新字段，回退老字段；保留 2 个 minor 版本 |
| AnalystEvaluator playbook 写入污染历史 playbook | 写入前 backup `playbook.md.bak`；新内容用 ` <!-- analyst-auto: v2 -->` 块标记；运营人手写区域不被覆盖 |
| 包装公式 UI 编辑器允许运营人破坏 prompt 结构（Kimi 解析炸）| 编辑器前端做 markdown 语法预览 + 必含字段（5 公式名 / CES 公式）校验 |
| evidence 注入 prompt 后 Kimi 上下文超长 | 现有 `max_tokens=3000` 是输出预算；输入预算 ~2k tokens；evidence 限 3 条 × 200 字 = 600 字（~600 tokens 增量），仍安全 |

## 与 V1.3 Orchestrator 的契约

本 change 完成后，Orchestrator MVP（V1.3）启动时直接读：
- `content_evidence` 表（按 tenant + funnel + angle 检索）
- `memory/content/playbook.md`（已验证爆款规律）
- `goals.used_angles` 三态（决定推荐 angle 时避开"sunk"，优先"validated_hit"）
- `goals.overall_strategy.content_funnel`（继续作为漏斗策略锚点，不变）

Orchestrator 不需要回头补这些数据源；本 change 是它的运行时数据契约。
