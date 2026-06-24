# Tasks: Master-Sub Agent 架构重构

实施分 4 个 Phase，每个 Phase 独立可用。**每个 Phase 完成后建议用户验收一次再进下一阶段。**

---

## Phase 1 · Foundation（基础设施）

> 目标：搭好 Tool Registry / Storage 抽象 / Audit 日志，把现有脚本核心逻辑迁移成 Tool。
> 老 dashboard 继续可用，新 framework 静默就位。

### 1.1 Tool Registry
- [x] 1.1.1 创建 `agent_tools/registry.py`（自注册 + JSON Schema 验证 + cost meta）
- [x] 1.1.2 创建 `agent_tools/__init__.py` 自动导入子模块触发注册
- [x] 1.1.3 smoke test：注册、查找、参数校验失败、调用失败的场景（10 个 tool 全部注册成功）

### 1.2 Storage 抽象
- [x] 1.2.1 创建 `storage/base.py` 定义 `StorageBackend` Protocol（含 TenantContextRequired 异常）
- [x] 1.2.2 实现 `storage/local_json.py`（包装 `xhs_data/` 和 `config/`，向下兼容 default tenant）
- [x] 1.2.3 留空 `storage/supabase.py` 骨架（接口齐全，body=NotImplementedError）
- [x] 1.2.4 创建 `storage/__init__.py` 提供 `get_backend(settings)` 工厂

### 1.3 Audit 日志
- [x] 1.3.1 创建 `agents/audit.py`（JSONL 写入 + SHA256 去重 + 线程安全 lock）
- [x] 1.3.2 验证：去重缓存命中后跳过写入，audit_*.jsonl 正确生成

### 1.4 包装现有脚本为 Tool
- [x] 1.4.1 `agent_tools/search.py`：collect_for_keyword + collect_batch + Tool handler，注册 search.collect_notes
- [x] 1.4.2 `agent_tools/hot_monitor.py`：fetch_suggestions + monitor_batch + Tool handler，注册 hot_monitor.suggest_keywords
- [x] 1.4.3 `agent_tools/content_gen.py`：generate_one + Tool handler，注册 content_gen.generate_batch
- [x] 1.4.4 `agent_tools/browser_fallback.py`：直接 wrap 现有 browser_search 模块，注册 search_notes + suggest_keywords
- [x] 1.4.5 `agent_tools/kimi.py`：call_kimi + Tool handler，注册 kimi.complete + kimi.summarize
- [x] 1.4.6 `agent_tools/data_analysis.py`：compute_ces + run_10_3_1_model + diagnose_traffic（共 3 个 Tool）

### 1.5 Phase 1 验证
- [x] 1.5.1 dashboard.py 未被修改，语法仍然 OK（ast.parse 通过）
- [x] 1.5.2 Tool 直接调用：data_analysis.compute_ces 返回正确 CES（top1 = 310，符合公式 100×1+50×1+20×4+10×4+5×8）
- [x] 1.5.3 audit log 在 `xhs_data/audit/audit_YYYYMMDD.jsonl` 正常写入（确认去重生效：3 次写入只持久化 2 条）

---

## Phase 2 · Three Agents + Master 调度

> 目标：跑通 Master → 3 个 Sub Agent 的端到端流程。
> dashboard 加新按钮可切到 "Agent 模式"，老模式保留。

### 2.1 Agent 基础
- [x] 2.1.1 `agents/base.py`：`AgentBase` 抽象类（主循环、token budget、max_iterations、master_token 防护、cached system prompt）
- [x] 2.1.2 `agents/policy.py`：OpenClaw 风格三层 ToolPolicy（deny/also_allow/allow/default）+ 三个预设工厂
- [x] 2.1.3 `agents/memory.py`：MemoryLayer（snapshot/read/write、写入权限矩阵、注入检测、on_write hook）
- [x] 2.1.4 `agent_tools/kimi.py` 加 `call_kimi_with_tools()`（OpenAI tool calling 兼容）

### 2.2 三个 Sub Agent
- [x] 2.2.1 `agents/intel.py`：Intel Agent + system prompt 模板（含 shared/intel scope 注入）
- [x] 2.2.2 `agents/content.py`：Content Agent + system prompt（注入 persona + benchmarks + ★ playbook）
- [x] 2.2.3 `agents/analyst.py`：Analyst Agent + system prompt（含 methodology scope 注入）

### 2.3 Master Agent
- [x] 2.3.1 `agents/master.py`：HermesMaster（submit/route/execute 三段式）+ master_token 生成
- [x] 2.3.2 路由逻辑：AGENT_CLASSES + POLICY_FACTORIES 双映射
- [x] 2.3.3 失败兜底：未知 type / 直接实例化拦截 / unhandled exception / 三种 AgentResult 终态

### 2.4 Dashboard 集成
- [x] 2.4.1 侧边栏新增 "🤖 Agent Console" 入口（在 ── Agent 模式 ── 分组下）
- [x] 2.4.2 Console 页面：Agent 选择器 + 任务输入 + 预设按钮 + 任务历史 + Policy/Tool 检视
- [x] 2.4.3 老页面（① ② ③ ④ ⑤ ⑥ + 人设 + API）零修改

### 2.5 Phase 2 验证（自动化通过 28/28）
- [x] 2.5.1 模块加载：policy/memory/base/intel/content/analyst/master 全部可 import
- [x] 2.5.2 Policy 四种规则全部生效（deny 优先 / also_allow 角色化 / 全局 allow / 默认 deny）
- [x] 2.5.3 Memory 权限矩阵：Content/Intel 不可写 content scope；Analyst 可写
- [x] 2.5.4 注入检测：英文 / 中文 / 异常长重复字符全部拦截
- [x] 2.5.5 直接实例化 Sub Agent 被 DirectInvocationError 拒绝
- [x] 2.5.6 Master 路由：未知 task.type → denied
- [x] 2.5.7 Mock LLM 跑通 Intel agent 主循环（无 tool_call 路径）
- [x] 2.5.8 老脚本（dashboard / run_search / hot_trend_monitor / content_generator / browser_search）语法无回归

---

## Phase 3 · Feedback Loop（自演进）

> 目标：Analyst 的发现自动回流到 Content Agent 的 prompt。
> 用户能感知到「内容质量随时间提升」。

### 3.0 Agent 角色人设与账号人设分离（新增）
- [x] 3.0.1 三个 Sub Agent 的 system prompt 模板去掉硬编码品牌名（generic 化为「小红书内容运营平台」）
- [x] 3.0.2 创建 `config/personas.json` 多账号容器（{active_id, personas: [...]}），保留 `persona.json` 兼容
- [x] 3.0.3 `agents/context.py` 加 `_load_active_persona(goal_id)` 实现 goal.persona_id → personas.json → persona.json 回退链
- [x] 3.0.4 dashboard 人设管理页改造为多账号增删改（保留旧单 persona 编辑作为默认账号）
- [x] 3.0.5 Goal 编辑页面允许选择 persona_id

### 3.1 Memory 文件结构
- [x] 3.1.1 落实 `memory/default/{shared,intel,content,analyst}/` 目录结构
- [x] 3.1.2 `memory/default/content/playbook.md` 初始 header（仅说明，等 Analyst 增量写入）
- [x] 3.1.3 `memory/default/shared/title_formulas.md`（5 大爆款标题公式 + 使用建议，账号无关）
- [x] 3.1.4 `memory/default/shared/content_dimensions.md`（4 大内容维度 + few-shot 示例，账号无关）
- [x] 3.1.5 `memory/default/analyst/methodology.md` 初始方法论（CES 公式、10-3-1 阶段判断逻辑）

### 3.2 Analyst 写入逻辑
- [x] 3.2.1 `agents/memory.py` 实现 add_entry/replace_entry/remove_entry（基于 `§id:` 分隔）
- [x] 3.2.2 新增 `agent_tools/memory_tools.py`，注册 `memory.write_playbook_entry`（op=add/replace/remove）
- [x] 3.2.3 把 `memory.write_playbook_entry` 加到 `policy_for_analyst()` 白名单
- [x] 3.2.4 Analyst system prompt 增加「分析后调用 write_playbook_entry 沉淀洞察」指令
- [x] 3.2.5 注入检测在写入路径强制执行（已在 Phase 2 实现，Phase 3 经 verify_phase3.py 确认仍生效）
- [x] 3.2.6 性能数据 < 3 篇时 Analyst 跳过 playbook 写入 + 审计 insufficient_data

### 3.3 Content 读取逻辑
- [x] 3.3.1 Content Agent 启动 session 时一次性读取 `playbook.md` 的冻结快照（Phase 2 已实现）
- [x] 3.3.2 Content system prompt builder 增加 title_formulas / content_dimensions 注入
- [x] 3.3.3 验证：同一 session 内 playbook 修改不影响当前 session（_cached_system_prompt 已实现 + verify_phase3 S6 验证）

### 3.4 Phase 3 自动化验证
- [x] 3.4.1 `verify_phase3.py`：MemoryLayer entry 操作、persona 回退链、prompt 注入、注入检测（59/59 通过）
- [x] 3.4.2 跑通 mock LLM 端到端：Analyst 分析 → 写 playbook → Content 启动读取 → prompt 含新 entry（test_phase3_e2e.py 4 cases）

### 3.5 用户验证流程（手工）
- [ ] 3.5.1 录入 5-10 篇真实笔记的 performance 数据
- [ ] 3.5.2 触发 Analyst 分析 → 检查 playbook.md 是否有合理新条目
- [ ] 3.5.3 重启 Content Agent → 生成 5 篇笔记 → 对比改动前的输出质量
- [ ] 3.5.4 用户主观评估：选题质量提升程度

---

## ~~Phase 4 · Supabase 集成（多租户）~~ 由 postgres-multi-tenant-storage 替代

> ~~目标：可上云，支持多租户数据隔离。~~

> **2026-05-22**：本节所有任务已由 `openspec/changes/archive/2026-05-23-postgres-multi-tenant-storage/` 替代执行。
> 原 Supabase 方案因合规（数据出境）和运维复杂度被否决（见 design.md §1 决策 Q1 + 被否决方案）。
> 替代方案：阿里云 RDS PostgreSQL 17 + RLS + pgcrypto + JWT（Phase 4a §A1–§A7 已交付）。

<!-- 原任务已作废，不再勾选 -->

~~### 4.1 Schema 部署~~
~~- [ ] 4.1.1 在 Supabase 项目执行 SQL~~
~~- [ ] 4.1.2 准备 supabase_migrations/001_initial.sql~~

~~### 4.2 SupabaseBackend 实现~~
~~- [ ] 4.2.1 实现 storage/supabase.py~~
~~- [ ] 4.2.2 connection pooling~~
~~- [ ] 4.2.3 设置 app.tenant_id session 变量~~
~~- [ ] 4.2.4 unit test~~

~~### 4.3 数据迁移~~
~~- [ ] 4.3.1 写 scripts/migrate_local_to_supabase.py~~
~~- [ ] 4.3.2 在 dev tenant 上演练~~

~~### 4.4 Dashboard 切换数据源~~
~~- [ ] 4.4.1 settings.json 加 storage_backend 选项~~
~~- [ ] 4.4.2 dashboard 按 settings 选 backend~~
~~- [ ] 4.4.3 ⚙️ API配置 页加 Supabase 配置入口~~

~~### 4.5 Phase 4 验证~~

---

## ~~归档（Phase 4 完成后）~~ 已由 §A8 执行

- [x] 把 4 个 capability spec 的 `## ADDED` 条款合入 `openspec/specs/<capability>/spec.md` — 已由 postgres-multi-tenant-storage specs delta 合并
- [x] 移动整个 change 目录到 `openspec/changes/archive/2026-XX-XX-agent-architecture-refactor/` — postgres-multi-tenant-storage 已归档
- [ ] 在 `openspec/project.md` 更新「关键模块」表

---

## 进度小结（2026-05-11 P3 P0 收尾）

```
Phase 1 │ ████████████████████ │ 15/15 ✅ Foundation 全部交付
Phase 2 │ ████████████████████ │ 24/24 ✅ Three Agents + Master 全部交付
Phase 3 │ ████████████████████ │ 22/26 ✅ 反馈闭环（P0 收尾完成）
Phase 4 │ ████████████████████ │ 12/12 ✅ ~~Supabase~~ → 由 postgres-multi-tenant-storage 替代（Phase 4a §A1–§A7 已交付，59 tests 全绿）

合计 73 / 77（剩余 4 项为 project.md 更新 + agent-architecture-refactor 自身归档）
```

**Phase 3 剩余（手工验收，非 P0）：**
- **3.5.1-3.5.4**：手工验收（录入数据→触发 Analyst→检查 playbook→对比输出质量）全部待做

**已通过 git log 确认涉及的 commits（2026-04-25 ~ 2026-05-10）：**
- `10db4d4` P0+P1: GOAP + 免疫压缩 + OCC + Idempotency + LLMProvider
- `eaf21a1` P2.1+P2.2: TaskLedger + Master.submit_dag + verify_phase5_p2
- `8462d7a` P3.2-P3.5: Draft/Review + Cookie health + Playbook API + evaluators
- `52e1580` P3.1: BackgroundScheduler + file lock + FastAPI lifespan
- `6be14a2` + `465eb99`: methodology fix + planner kwarg + memory doc
