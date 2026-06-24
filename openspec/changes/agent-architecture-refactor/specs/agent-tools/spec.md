# Spec Delta: agent-tools

> 新建 capability。所有条款 `## ADDED`。

## ADDED Requirement: 统一的 Tool Registry

系统 SHALL 提供 `agent_tools.registry`，所有 Tool 通过 `register(name, schema, handler, **meta)` 自注册。
注册项 MUST 包含：
- `name`：唯一标识（kebab-case 或 dot-notation）
- `schema`：JSON Schema（OpenAI tool calling 兼容格式）
- `handler`：可调用对象，签名 `(args: dict, ctx: ToolContext) -> dict`
- `requires_env`：必需的环境变量列表
- `cost_estimate`：估计 token / 时间成本（用于 budget）

### Scenario: 注册重名 Tool
- **WHEN** 用同一 name 注册两次
- **THEN** 抛出 `ToolAlreadyRegistered`

### Scenario: handler 返回非字典
- **WHEN** Tool 返回非 dict 或缺少 `ok` 字段
- **THEN** registry 包装为 `{ok: False, error: "invalid_handler_return"}`

---

## ADDED Requirement: Tool 调用必须经过参数校验

`registry.invoke(name, args, ctx)` 在调用 handler 前 SHALL：
1. 用 `jsonschema.validate(args, tool.schema.parameters)` 校验输入
2. 失败时抛 `ToolInputError`（仿 OpenClaw）
3. 检查 `requires_env` 全部已设
4. 失败时抛 `ToolEnvironmentError`

### Scenario: 缺少必需参数
- **WHEN** invoke 时缺少 schema 标记 required 的字段
- **THEN** ToolInputError 抛出，含字段名

### Scenario: 类型错误
- **WHEN** 参数类型不匹配 schema
- **THEN** ToolInputError 抛出，含详细路径和期望类型

---

## ADDED Requirement: Tool 输入输出约定

所有 Tool 的返回值 SHALL 是字典：
```python
{
    "ok": bool,
    "data": Any,        # ok=True 时的业务数据
    "error": str | None, # ok=False 时的错误信息
    "meta": dict,        # 耗时、token、调用源等
}
```

### Scenario: 失败 Tool 返回标准格式
- **WHEN** Tool 内部 raise Exception
- **THEN** registry 捕获并返回 `{ok: False, error: <message>, meta: {trace: ...}}`
- **AND** 写审计日志

---

## ADDED Requirement: 现有脚本的 Tool 化映射

系统 SHALL 提供以下 Tool（包装现有脚本，保留 CLI 入口）：

| Tool name | 包装的现有函数 | 调用方角色 |
|-----------|--------------|----------|
| `search.collect_notes` | `run_search.search_and_collect` | intel |
| `hot_monitor.suggest_keywords` | `hot_trend_monitor.fetch_suggest_words` | intel |
| `browser_fallback.search_notes` | `browser_search.search_notes` | intel（自动 fallback） |
| `browser_fallback.suggest_keywords` | `browser_search.get_keyword_suggestions` | intel（自动 fallback） |
| `content_gen.generate_batch` | `content_generator.main` | content |
| `kimi.complete` | `dashboard.kimi_call` | content / analyst |
| `kimi.summarize` | （新增）封装 kimi.complete + 摘要 prompt | analyst |
| `data_analysis.compute_ces` | （新增）按 CES 公式计算 | analyst |
| `data_analysis.run_10_3_1_model` | （新增）筛选 Top3，识别共性 | analyst |
| `data_analysis.diagnose_traffic` | （新增）流量诊断检查清单 | analyst |

### Scenario: 现有 CLI 命令仍可用
- **WHEN** 用户运行 `python run_search.py`
- **THEN** 老脚本仍正常工作（内部已改成调用 Tool 但 stdout 输出格式不变）

### Scenario: Tool 调用记录关联到现有 Excel 输出
- **WHEN** 通过 Tool 调用 `search.collect_notes`
- **THEN** 仍生成 `xhs_data/spider_xhs_采集结果_*.xlsx`（兼容性）
- **AND** 同时通过 Storage 接口写入数据库（如启用 Supabase）

---

## ADDED Requirement: ToolPolicy 的三层结构（OpenClaw 风格）

`agents.policy.ToolPolicy` SHALL 支持：
- `default_action`：未匹配时的默认（"allow" | "deny"）
- `allow_patterns`：允许的 glob 模式列表
- `deny_patterns`：禁止的 glob 模式（**优先级最高**）
- `also_allow`：`{agent_name: [patterns]}`，agent 级额外许可

检查顺序：deny_patterns → also_allow → allow_patterns → default_action

### Scenario: deny 优先级最高
- **GIVEN** policy.allow_patterns = ["*"]，policy.deny_patterns = ["search.delete*"]
- **WHEN** 调用 `search.delete_notes`
- **THEN** 返回 deny（即使全局 allow 也无效）

### Scenario: agent 级额外许可
- **GIVEN** policy.allow_patterns = []，policy.also_allow = {"analyst": ["kimi.*"]}
- **WHEN** AnalystAgent 调用 `kimi.complete`
- **THEN** 返回 allow
- **WHEN** ContentAgent 调用 `kimi.complete`
- **THEN** 返回 deny
