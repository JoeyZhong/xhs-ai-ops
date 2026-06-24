# Design: content-lifecycle-v2

> 本文档只记录非显而易见的设计决策。CRUD / schema / endpoint 形态见 `tasks.md` 和 `specs/web-api/spec.md`。

---

## 1. 为什么 `packaging_rules.md` 是 source of truth，不是 PG 表？

**选择**：保持 P0 引入的 file-based source（`memory/_universal/packaging_rules.md`），不迁 PG。

**理由**：
- 包装公式是**全租户共享的运营 know-how**，不是租户私有数据；PG RLS 的多租户隔离价值不存在。
- 编辑频次低（运营人每月可能改 1-2 次），文件 IO + mtime 缓存（P0 已实现）性能完全够用。
- markdown 直接成为 prompt 字符串，无需 ORM 序列化层。
- 调试时直接 `cat` 文件可见，比 SQL 查询友好。
- 若未来出现多租户私有公式（不太可能），再为该租户添加 `memory/<tenant>/packaging_rules.md` override，loader 优先读私有 fallback 通用。

**反对方案**（PG 表 `packaging_rules`）：
- 增加 migration 和 RLS policy 维护成本，无对应收益。

---

## 2. `content_evidence` 为什么不复用 `agent_memory` 表？

**选择**：新建 `content_evidence` 表。

**理由**：
- `agent_memory` 是 markdown 文本 + entry_id 模型，适合"分析师写的方法论笔记"等长文本；evidence 是结构化 `{angle, hook, key_insight, ces_score, source_note_id}` 五字段元组，需按 `(tenant, angle)` 和 `(tenant, funnel_stage)` 索引快速检索 top-3。
- 强行复用 agent_memory 会把检索逻辑塞到 jsonb 字段里，扫表代价大。
- evidence 的生命周期跟 `collected_notes` 强绑定（note 删了 evidence 应级联或独立），关系数据库表更合适。

**字段约束**：
- `angle` 必须是 5 公式之一（反直觉/数字清单/本地汇总/工具/焦虑共鸣），后端枚举校验
- `funnel_stage` ∈ {traffic, trust, conversion}
- `hook` 限 100 字，`key_insight` 限 300 字（控制 prompt token 增量）
- `ces_score` 冗余存（来自 source_note 时刻的快照，避免 note 后续 CES 变动影响 evidence ranking）

---

## 3. evidence 提取的 Kimi 调用策略

**选择**：batch 模式（每次 10 条 notes 拼成一个 prompt），不是逐条调用。

**理由**：
- 单次 Kimi 调用固定 latency ~1-2s + JSON 解析 overhead；逐条 50 条 → 100s+；批 10 条 → 5 次 → ~15s。
- Kimi 上下文容量 32k，10 条 notes（每条 ~500 字）= 5k tokens，远低于上限。
- prompt 模板要求 Kimi 输出严格 JSON 数组（与 `/content/generate` 一样的解析路径）。

**降级**：
- 单批失败（JSON 解析炸）→ fallback 到 1×10 逐条调用（不阻断整个提取流程）
- Kimi 全部失败 → 整个 extraction 任务标记 `partial_failure`，已成功的 evidence 留库，前端展示错误数

**幂等性**：
- `upsert_evidence` 以 `(tenant_id, source_note_id)` 为 ON CONFLICT 键；重跑提取不会产生重复 evidence。
- 默认查询 `WHERE note_id NOT IN evidence` 跳过已提取的 note；运营人需手动「重新提取」按钮才会 force 覆盖。

---

## 4. evidence 注入 prompt 的 ranking 和数量

**选择**：每次 prompt 最多注入 3 条 evidence，按"同 funnel_stage 优先 + 同 angle 次之 + ces_score DESC"排序。

**理由**：
- token 预算：3 条 × (100 + 300) = ~1200 字 ≈ 1200 tokens 增量，可控。
- 同 funnel_stage 是最强信号（漏斗位决定写法），同 angle 次之（标题公式偏好），ces_score 是质量信号。
- 拼装位置：`packaging_rules` 之后、用户意图 / 关键词之前，避免 Kimi 把 evidence 当成历史样本而非"参考爆款"。

**ranking SQL**：
```sql
SELECT * FROM content_evidence
WHERE tenant_id = %s
  AND (funnel_stage = %s OR angle = %s)
ORDER BY (funnel_stage = %s)::int DESC,    -- 同 funnel 优先
         (angle = %s)::int DESC,             -- 同 angle 次之
         ces_score DESC NULLS LAST
LIMIT 3;
```

---

## 5. `used_angles` 三态结构 schema

**老结构**（v1）：
```json
{"used_angles": ["反直觉型", "工具型"]}
```

**新结构**（v2）：
```json
{
  "used_angles": [
    {"angle": "反直觉型", "status": "validated_hit", "evidence_count": 5, "last_ces": 320},
    {"angle": "工具型", "status": "sunk", "evidence_count": 2, "last_ces": 45},
    {"angle": "焦虑共鸣型", "status": "unknown", "evidence_count": 0, "last_ces": null}
  ]
}
```

**三态判定（AnalystEvaluator 自动写）**：
- `validated_hit`：最近 30 天该 angle 至少 3 篇，平均 CES > 200
- `sunk`：最近 30 天该 angle 至少 3 篇，平均 CES < 80
- `unknown`：样本不足 3 篇，或介于两阈值之间

**阈值在 `agents/evaluators.py` 顶部定义**（不是 magic number 散落）：
```python
TRISTATE_THRESHOLDS = {
    "min_samples": 3,
    "validated_hit_ces": 200,
    "sunk_ces": 80,
    "window_days": 30,
}
```

**前端展示**：
- `validated_hit` → 绿色 ✅ "已验证爆款（CES {n}）"
- `sunk` → 红色 ❌ "沉底（CES {n}）"
- `unknown` → 灰色 / 不展示（避免噪音）

**migration 兼容**：
- 老 `["反直觉型"]` → `[{angle: "反直觉型", status: "unknown", evidence_count: 0, last_ces: null}]`
- 老前端读不识别新 schema 时回退到 `used_angles_legacy: ["反直觉型"]`（保留 1 个 minor 版本）

---

## 6. AnalystEvaluator 写 playbook 的防污染机制

**问题**：AnalystEvaluator 自动写 `memory/<tenant>/content/playbook.md` 时，**不能覆盖运营人手写内容**。

**方案**：playbook.md 用 HTML 注释块分隔自动区和手写区。

**模板**：
```markdown
# Playbook · <tenant>

<!-- manual:start -->
[运营人手写区，AnalystEvaluator 永不覆盖]
<!-- manual:end -->

<!-- analyst-auto: v2 -->
[AnalystEvaluator 自动写区，每次 evaluator 跑都重写]
最后更新：2026-05-28T10:00:00Z

## Top 3 已验证爆款角度
- 反直觉型（平均 CES=320，样本 8 篇）
- 工具型（平均 CES=285，样本 5 篇）
- 数字清单型（平均 CES=240，样本 4 篇）

## 已沉底角度（避免使用）
- 焦虑共鸣型（平均 CES=45，样本 6 篇）
<!-- /analyst-auto -->
```

**写入逻辑**：
1. 读现有文件 → 用正则找 `<!-- analyst-auto: v2 -->...<!-- /analyst-auto -->` 块
2. backup 整文件到 `playbook.md.bak`
3. 替换该块内容（若不存在则追加到文件末尾）
4. 写回 → 触发 ContentAgent 下次读到新 playbook

**ContentAgent 读取**：
- `prompt_context.build_strategy_prompt_context` 新增 `playbook_summary` 字段
- 只读 `<!-- analyst-auto: v2 -->` 块（避免运营人手写区过长污染 prompt 上下文）
- 限 500 字符（约 500 tokens），超长截断并加 "..."

---

## 7. CES 阈值参数化

**避免 hardcode**：所有 CES 相关阈值（提取门槛、三态判定）放 `config/settings.json` 的 `ces_thresholds` 段：

```json
{
  "ces_thresholds": {
    "evidence_extraction_min": 250,
    "validated_hit_min": 200,
    "sunk_max": 80,
    "tristate_min_samples": 3,
    "tristate_window_days": 30
  }
}
```

**理由**：不同租户 / 不同行业基线差异大；运营人可在 settings 页调整。

---

## 8. 与 Orchestrator（V1.3）的接口契约

V1.3 Orchestrator 启动时**只读**本 change 沉淀的 4 个数据源：

| 数据源 | 接口 | Orchestrator 用法 |
|---|---|---|
| `content_evidence` | `backend.list_evidence(funnel_stage=..., angle=...)` | 「最近这类内容什么 hook 表现好？」 |
| `memory/<tenant>/content/playbook.md` | 读 `<!-- analyst-auto: v2 -->` 块 | 「下周写什么角度的内容更稳？」 |
| `goals.used_angles` 三态 | `backend.load_goals()` → 解析 | 「这个角度该不该再写？」 |
| `goals.overall_strategy.content_funnel` | 同上，v1 已建 | 「漏斗哪层缺内容？」 |

**Orchestrator 不写**：本 change 的数据源都是 IntelAgent / AnalystEvaluator 产出，Orchestrator 只消费 + 推荐，不直接 mutate。

**这条契约不变**是 V1.3 可以独立启动的前提；本 change 在交付时必须经过"Orchestrator 视角"的契约 review（验收门 G.3）。
