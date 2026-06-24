# OpenSpec 工作流（AI 协作约定）

本项目从 2026-04-29 起采用 OpenSpec 方式开发。**任何新功能或重要改动**，必须先写 proposal、经用户确认后再动代码。

## 三阶段流程

### Stage 1 · 提案（Propose）
在 `openspec/changes/<kebab-case-name>/` 下创建：
- **`proposal.md`** — 必需。Why（动机）、What（范围）、Impact（影响范围）、Risk（风险）
- **`tasks.md`** — 必需。可勾选的任务列表，按实现顺序排列
- **`design.md`** — 可选。涉及架构决策、新依赖、跨模块协作时必填
- **`specs/<capability>/spec.md`** — 必需。该改动对哪些 capability 的 delta（新增/修改/删除条款，用 `## ADDED`、`## MODIFIED`、`## REMOVED` 标记）

提案完成后停下，等用户审阅。**禁止跳过提案直接改代码。**

### Stage 2 · 实现（Implement）
- 按 `tasks.md` 顺序执行，**完成一个就在文件里勾掉一个**（`- [x]`）
- 实现过程中如果发现 spec 写漏了，回头补 spec，再继续写代码
- 每次改动只动 proposal 范围内的文件，不要顺手清理无关代码

### Stage 3 · 归档（Archive）
全部任务完成、用户验收通过后：
- 把 `openspec/changes/<name>/specs/` 下的 delta 合入 `openspec/specs/<capability>/spec.md`（apply ADDED/MODIFIED/REMOVED）
- 把 `openspec/changes/<name>/` 整个目录移动到 `openspec/changes/archive/<YYYY-MM-DD>-<name>/`
- 更新 `openspec/specs/` 视为项目当前真相

## 横切维度影响审查（proposal 必填）

每个 `proposal.md` 必须含此段，对所有已立维度（见 `openspec/specs/data-dimensions/spec.md`）勾选：

- [ ] tenant_id: 已保留 / 不涉及 / 新增依赖
- [ ] goal_id: 已保留 / 不涉及 / 新增依赖
- [ ] persona_id: 已保留 / 不涉及 / 新增依赖
- [ ] funnel_stage: 已保留 / 不涉及 / 新增依赖

新增数据维度时，必须在 `data-dimensions/spec.md` 中新增 `## ADDED Requirement`，并把字段加进本清单。

## Spec 写法约定

每个 capability 一个文件：`openspec/specs/<capability>/spec.md`

格式：
```markdown
# <capability> 能力规格

## Requirement: <需求标题>
系统 SHALL/MUST <可验证的行为>

### Scenario: <场景名>
- **WHEN** <触发条件>
- **THEN** <预期结果>
- **AND** <附加结果>
```

`SHALL` 强制；`SHOULD` 推荐；`MAY` 可选。每个 Requirement 至少一个 Scenario。

## 约束

- **不打破现有功能**：proposal 影响到已有 spec 时必须用 MODIFIED/REMOVED 显式标记
- **小步快跑**：单个 change 控制在能在一天内实现完的范围；大功能拆成多个 change
- **可追溯**：归档目录永远不删，未来追查决策用
- **不补历史规格**：2026-04-29 之前的功能不追溯写 spec，只在被新 change 修改时按需补充

---

## 文档职责地图（避免遗漏的根本约定）

计划散在多处时，回答「还有什么没做」只看一处必漏。本节固定每份文档的**单一职责**和**读写规则**，所有人/AI 按此操作。

### 谁管什么（owns）

| 文档 | 唯一职责 | **不放什么** |
|------|----------|-------------|
| `CLAUDE.md` | 项目恒定上下文：定位/技术栈/目录/内容规则/命令 | 进度、路线图 |
| `docs/PRD_*.md` | 想做什么 + 为什么 + **版本路线图（V1.x）** | 实现状态、架构细节 |
| `docs/ARCHITECTURE.md` | 系统怎么搭 + 设计决策 + **已知架构债** | 路线图、进度百分比（会过期误导） |
| `openspec/changes/<name>/` | 单个**在飞**改动：proposal/tasks/design/spec delta | 跨改动的全局状态 |
| `openspec/specs/<cap>/` | **已交付能力的当前真相**（契约/红线） | 未来计划 |
| **`docs/STATUS.md`** | **全局一行式索引**：路线图/在飞/已交付/债/backlog 各一行 + 状态 + 指针 | 详情（详情在上面各文档） |
| memory backlog | 未成熟点子暂存 | 已立项的正式工作 |
| `docs/USER_GUIDE.md` | 运营者怎么用 | 实现/架构 |

### 一个想法的生命周期

```
灵感 → memory backlog
  → 值得做 → 进 PRD 路线图（大功能，定版本）/ 直接立 change（小改动）
  → 启动 → openspec/changes/<name>/（proposal→tasks→spec delta）
  → 实现 → 勾 tasks
  → 验收归档 → spec 合入 specs/ + 目录移 archive/
  ↳ 每个状态变更，同步改 docs/STATUS.md 对应那一行
架构决策/新债 → 随时落 ARCHITECTURE.md（并在 STATUS 加债行）
```

### 读写规则（按场景）

| 场景 | 读 | 写 |
|------|-----|-----|
| 问「还有什么没做 / 进度」 | **只读 `docs/STATUS.md`** | — |
| 启动新功能 | PRD 路线图 + 相关 spec | 立 change + STATUS 加行 |
| 实现中 | change/tasks + spec | 勾 tasks；spec 漏了补 spec |
| 归档 | 本文件三阶段流程 | 合 spec + 移 archive + **STATUS 标完成** |
| 架构决策 / 发现债 | ARCHITECTURE | ARCHITECTURE 记 + STATUS 加债行 |
| 冒小点子 | — | memory backlog |
| 想知道系统现在保证什么 | `openspec/specs/` | — |

### STATUS.md 维护铁律

- 它是**索引不是详情**：只放一行式状态 + 指向详情文档的指针，不复制内容。
- **状态来源以实证为准**：归档目录 `openspec/changes/archive/` 是「已交付」的唯一铁证；任何文档里的状态描述若与 archive 冲突，以 archive 为准（历史上 ARCHITECTURE 状态表曾过期误导）。
- 任一 change 状态变化（立项/归档）、任一架构债新增/还清，**当次就改 STATUS.md**，不留到以后。
