# Architecture Spec: Master-Sub Agent 协作架构

> 本文是该 change 的设计蓝图。OpenSpec 标准里它是 `design.md`，按用户要求命名为 `architecture_spec.md`。

## 0. 设计原则

| 原则 | 来源 | 体现 |
|------|------|------|
| **可控性优先** | OpenClaw | Master 是唯一调度入口；所有 Tool 调用经过 policy 检查；强制审计日志 |
| **自主进化** | Hermes | Sub Agent 通过 Memory 系统跨 session 累积经验，无需 fine-tune |
| **小步快跑** | OpenSpec | 4 阶段，每阶段独立可用；不破坏现有功能 |
| **可观测** | 自定义 | 所有 Agent 行为产出结构化日志，便于排查和分析 |

---

## 1. 模块布局

```
Spider_XHS/
├── agents/                          # ← 新增
│   ├── __init__.py
│   ├── base.py                      # AgentBase 抽象类
│   ├── master.py                    # Hermes Master Agent
│   ├── intel.py                     # 情报 Agent
│   ├── content.py                   # 内容 Agent
│   ├── analyst.py                   # 分析 Agent
│   ├── memory.py                    # Memory 层（冻结快照模式）
│   ├── policy.py                    # Tool 权限策略（OpenClaw 风格）
│   └── audit.py                     # 审计日志（JSONL）
├── agent_tools/                     # ← 新增（注意区分 apis/）
│   ├── __init__.py
│   ├── registry.py                  # Tool 自注册中心
│   ├── search.py                    # 包装 run_search.py
│   ├── hot_monitor.py               # 包装 hot_trend_monitor.py
│   ├── content_gen.py               # 包装 content_generator.py
│   ├── browser_fallback.py          # 包装 browser_search.py
│   ├── data_analysis.py             # 新增：CES/10-3-1 计算
│   └── kimi.py                      # 包装 kimi_call
├── storage/                         # ← 新增
│   ├── __init__.py
│   ├── base.py                      # StorageBackend 接口
│   ├── local_json.py                # 本地 JSON/Excel backend
│   └── supabase.py                  # Supabase backend
├── apis/                            # ← 不动（XHS API 封装）
├── dashboard.py                     # ← 改造：通过 Master 提交任务
├── run_search.py                    # ← 保留 CLI 入口，核心逻辑迁出
├── hot_trend_monitor.py             # ← 同上
├── content_generator.py             # ← 同上
├── browser_search.py                # ← 不动（已经是模块）
└── config/                          # ← schema 不变
```

---

## 2. Master Agent（Hermes）设计

### 2.1 核心职责

1. **任务分发**：接收外部请求 → 选择合适的 Sub Agent → 委托执行 → 收集结果
2. **安全网关**：所有 Tool 调用必须经过 Master 的 policy 检查
3. **审计**：每个任务产出 JSONL 日志（task_id / agent / tools_called / status / cost）
4. **失败兜底**：Sub Agent 失败 → Master 决策（重试 / 降级 / 放弃）

### 2.2 Master 主循环（伪代码）

```python
class MasterAgent:
    def submit(self, task: Task) -> TaskResult:
        # 1. 鉴权 + 注册任务
        task_id = self._register(task)
        self._audit("submit", task_id, task)
        
        # 2. 路由：根据 task.type 选择 Sub Agent
        agent = self._route(task)  # IntelAgent | ContentAgent | AnalystAgent
        
        # 3. 委托执行（带 token budget + max_iterations）
        try:
            result = agent.run(
                task,
                tool_policy=self._build_policy_for(agent, task),
                budget=task.budget or DEFAULT_BUDGET,
                max_iterations=20,
            )
        except ToolPolicyViolation as e:
            self._audit("policy_violation", task_id, e)
            return TaskResult.denied(reason=str(e))
        except SubAgentTimeout:
            return self._handle_timeout(task, task_id)
        
        # 4. 持久化结果 + 审计
        self._storage.save_task_result(task_id, result)
        self._audit("complete", task_id, result.summary)
        return result
```

### 2.3 Policy 系统（OpenClaw 风格三层）

```python
# agents/policy.py
@dataclass
class ToolPolicy:
    default_action: Literal["allow", "deny"] = "deny"
    allow_patterns: list[str]   # glob: "search.*", "data_analysis.*"
    deny_patterns: list[str]    # glob: "*.delete_*"
    also_allow: dict[str, list[str]]  # agent → extra patterns

def check_tool_call(policy: ToolPolicy, agent_name: str, tool_name: str) -> bool:
    # 1. deny 优先（黑名单一票否决）
    if any(fnmatch(tool_name, p) for p in policy.deny_patterns):
        return False
    # 2. agent-level also_allow
    if tool_name in policy.also_allow.get(agent_name, []):
        return True
    # 3. 全局 allow
    if any(fnmatch(tool_name, p) for p in policy.allow_patterns):
        return True
    return policy.default_action == "allow"
```

每个 Sub Agent 启动时由 Master 注入合适的 policy，Sub Agent 调用任何 Tool 前，registry 会查 policy。

---

## 3. Sub Agent 设计（Hermes 风格）

### 3.1 通用基类

```python
# agents/base.py
class AgentBase:
    role: str                    # "intel" | "content" | "analyst"
    enabled_tools: list[str]     # tool name patterns
    system_prompt_builder: Callable[[MemoryContext], str]
    
    def run(self, task, tool_policy, budget, max_iterations) -> Result:
        # 1. 构造 system prompt（注入冻结的 memory 快照）
        memory_ctx = self._memory.snapshot(scope=self.role)
        system = self.system_prompt_builder(memory_ctx)
        
        # 2. 主循环（参考 Hermes run_conversation）
        messages = [{"role": "system", "content": system},
                    {"role": "user",   "content": task.prompt}]
        for i in range(max_iterations):
            if budget.exhausted(): break
            resp = kimi_call_with_tools(messages, self._tool_schemas())
            if resp.tool_calls:
                for call in resp.tool_calls:
                    if not self._registry.check(call.name, tool_policy):
                        raise ToolPolicyViolation(call.name)
                    result = self._registry.invoke(call.name, call.args)
                    messages.append(tool_result_msg(call, result))
                    self._on_tool_call(call, result)  # ← memory hook
            else:
                return Result(content=resp.content, messages=messages)
        return Result.timeout()
```

### 3.2 三个 Sub Agent 的差异

| Agent | 主要 Tool | 输出 | Memory 写入策略 |
|-------|----------|------|---------------|
| **Intel（情报）** | `search.*` / `hot_monitor.*` / `browser_fallback.*` | 采集结果 + 热词报告 | 写 `intel/findings.md`：发现的新关键词、爆款规律 |
| **Content（内容）** | `kimi.generate` / `content_gen.*` | 笔记标题/正文/标签 | 只读 memory；不主动写 |
| **Analyst（分析）** | `data_analysis.*` / `kimi.summarize` | 性能报告 + 改进建议 | **写 `content/playbook.md`**（关键反馈机制） |

---

## 4. Tool Registry 设计

### 4.1 自注册模式（Hermes 风格）

```python
# agent_tools/registry.py
_REGISTRY: dict[str, ToolDef] = {}

@dataclass
class ToolDef:
    name: str
    schema: dict           # JSON Schema for LLM tool calling
    handler: Callable
    requires_env: list[str] = field(default_factory=list)
    cost_estimate: float = 0.0  # 用于 budget 控制

def register(name, schema, handler, **meta):
    _REGISTRY[name] = ToolDef(name=name, schema=schema, handler=handler, **meta)
    return handler

def invoke(name, args, ctx) -> dict:
    tool = _REGISTRY[name]
    validate_args(tool.schema, args)        # OpenClaw 风格参数校验
    try:
        result = tool.handler(args, ctx=ctx)
    except Exception as e:
        log_failure(name, e)
        raise ToolExecutionError(name, e)
    return result
```

### 4.2 现有脚本到 Tool 的映射

| 现有文件/函数 | 新 Tool 名 | 调用方 |
|-------------|-----------|--------|
| `run_search.search_and_collect()` | `search.collect_notes` | Intel |
| `hot_trend_monitor.fetch_suggest_words()` | `hot_monitor.suggest_keywords` | Intel |
| `browser_search.search_notes()` | `browser_fallback.search_notes` | Intel（隐式 fallback） |
| `browser_search.get_keyword_suggestions()` | `browser_fallback.suggest_keywords` | Intel（隐式 fallback） |
| `content_generator.main()` | `content_gen.generate_batch` | Content |
| `dashboard.kimi_call()` | `kimi.complete` | Content / Analyst |
| 新增 | `data_analysis.compute_ces` | Analyst |
| 新增 | `data_analysis.run_10_3_1_model` | Analyst |
| 新增 | `data_analysis.diagnose_traffic` | Analyst |

### 4.3 Tool 输入输出契约

所有 Tool 必须返回 `{ok: bool, data: any, meta: dict}` 结构，Master 据此判断是否成功并审计。

---

## 5. Feedback Loop 设计（核心创新）

### 5.1 数据流

```
┌──────────┐  发布笔记数据  ┌──────────────┐
│  用户/    │ ────────────▶ │ Analyst Agent │
│ Streamlit│                └───────┬──────┘
└──────────┘                        │ 计算 CES、找 Top 3 角度
                                    │ 总结成 playbook 条目
                                    ▼
                          ┌────────────────────┐
                          │ Memory: content/   │
                          │ playbook.md        │
                          │  - 角度A 平均CES高 │
                          │  - 标题钩子B 转化好│
                          │  - 时段C 最佳      │
                          └────────┬───────────┘
                                   │ next session 启动时注入
                                   ▼
                          ┌────────────────────┐
                          │ Content Agent      │
                          │ system prompt =    │
                          │  人设 + 公式库 +   │
                          │  ★playbook.md★    │
                          └────────────────────┘
```

### 5.2 关键实现要点

1. **冻结快照**（Hermes 模式）：Content Agent 启动 session 时一次性读取 playbook → 注入 system prompt → session 内不变（保护 cache）；Analyst 在 session 内的写入要等下次启动才生效
2. **Entry 模式**：playbook.md 用 `§` 分隔条目，Analyst 用 `add/replace/remove` 操作
3. **on_memory_write hook**：Analyst 写入时触发 hook，可同步到 Supabase
4. **冲突避免**：Analyst 永远不直接写 Content Agent 的 system prompt，只写 memory；prompt 是 builder 函数从 memory 读出来的

### 5.3 Memory 文件结构

```
memory/
├── shared/
│   ├── persona.md           # 当前人设（来自 config/persona.json）
│   └── benchmarks.md        # 周数据爆款标题库
├── intel/
│   └── findings.md          # 情报 Agent 写入：新关键词、爆款规律
├── content/
│   └── playbook.md          # ★ Analyst 写、Content 读
└── analyst/
    └── methodology.md       # Analyst 自身的分析框架（可演进）
```

---

## 6. 多租户存储（Supabase）

### 6.1 抽象层

```python
# storage/base.py
class StorageBackend(Protocol):
    def save_task_result(self, tenant_id, task_id, result): ...
    def load_memory(self, tenant_id, scope, file): ...
    def save_memory(self, tenant_id, scope, file, content): ...
    def list_collected_data(self, tenant_id, since: datetime): ...
    def save_collected_data(self, tenant_id, source, df): ...
    def save_audit_log(self, tenant_id, entry): ...
```

两个实现：`LocalJsonBackend`（向下兼容现有 xhs_data + config）和 `SupabaseBackend`。

### 6.2 Supabase Schema（设计稿）

```sql
-- 租户表
CREATE TABLE tenants (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 目标（对应现 goals.json 中每个 goal）
CREATE TABLE goals (
    id TEXT PRIMARY KEY,           -- "goal_001"
    tenant_id UUID REFERENCES tenants(id),
    name TEXT,
    config JSONB,                  -- 完整 goal 对象
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 采集结果
CREATE TABLE collected_notes (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID REFERENCES tenants(id),
    goal_id TEXT REFERENCES goals(id),
    note_id TEXT,
    title TEXT,
    likes INT, collects INT, comments INT, shares INT,
    raw JSONB,
    collected_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, note_id)
);

-- 热词（按日聚合）
CREATE TABLE hot_keywords (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID REFERENCES tenants(id),
    goal_id TEXT,
    keyword TEXT, suggested_word TEXT, heat TEXT,
    date DATE,
    UNIQUE(tenant_id, goal_id, suggested_word, date)
);

-- 生成内容
CREATE TABLE generated_posts (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID REFERENCES tenants(id),
    goal_id TEXT,
    title TEXT, body TEXT, tags TEXT[],
    angle TEXT,
    generated_at TIMESTAMPTZ DEFAULT NOW(),
    published_at TIMESTAMPTZ,
    performance JSONB     -- {likes, collects, comments, ces}
);

-- Memory（关键反馈数据）
CREATE TABLE agent_memory (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID REFERENCES tenants(id),
    scope TEXT,           -- "shared" | "intel" | "content" | "analyst"
    file TEXT,            -- "playbook.md"
    entry_id TEXT,        -- entry §-id
    content TEXT,
    written_by TEXT,      -- agent name
    written_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, scope, file, entry_id)
);

-- 审计日志
CREATE TABLE audit_log (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID REFERENCES tenants(id),
    task_id TEXT, agent TEXT, action TEXT,
    payload JSONB,
    ts TIMESTAMPTZ DEFAULT NOW()
);

-- RLS 策略：每个表都启用，按 tenant_id 隔离
ALTER TABLE goals ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON goals
    USING (tenant_id = current_setting('app.tenant_id')::UUID);
-- (其他表同理)
```

### 6.3 多租户隔离

- 每次请求设 `app.tenant_id` session 变量
- StorageBackend 内部所有查询自动带 tenant_id
- 本地 backend：用文件系统目录隔离 `xhs_data/{tenant_id}/...`

---

## 7. 分阶段实施

| Phase | 内容 | 用户可见效果 |
|-------|------|------------|
| **P1 Foundation** | agents/base.py + tool registry + storage interface（仅 LocalJsonBackend）+ 包装现有脚本为 Tool | 老 dashboard 继续用，新 framework 静默就位 |
| **P2 Three Agents** | Intel/Content/Analyst 三个 Sub Agent + Master 调度循环 + memory 冻结快照 | dashboard 加按钮"用 Agent 模式"切换 |
| **P3 Feedback Loop** | Analyst → playbook 写入 + Content 读取 playbook 注入 prompt | 内容质量随历史投放数据自动改进 |
| **P4 Supabase** | SupabaseBackend 实现 + RLS schema 部署 + dashboard 切换数据源 | 多租户就绪，可上线 |

---

## 8. 与现有代码的兼容性

- **CLI 入口保留**：`python run_search.py` 仍然能跑（Tool wrapper 调用同一份核心逻辑）
- **Excel 文件继续生成**：用作 LocalJsonBackend 的存储格式之一
- **dashboard.py 渐进改造**：P2 阶段先加新页面"Agent Console"，老页面不动；P4 完成后把老页面切到新数据源
- **Cookie 反爬保护、频率限制**：作为 Tool 内部约束保留，对 Agent 透明

## 9. 不在本次范围

- Web 取代 Streamlit
- Agent 间真正异步事件总线（先用同步调用 + 文件队列）
- LLM fine-tuning（自演进通过 prompt + memory 实现）
- 多用户登录界面（仅做数据层 tenant_id 隔离）
