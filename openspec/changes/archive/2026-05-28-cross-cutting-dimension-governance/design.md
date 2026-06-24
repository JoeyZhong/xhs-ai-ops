# Design: cross-cutting-dimension-governance

> 本文档记录 5 个非显而易见的设计决策。落地清单见 `tasks.md`，spec 形态见 `specs/`。

---

## 1. 为什么不直接修 goal_id，而是先立 spec？

**选择**：先立 `data-dimensions/spec.md`，再把 goal_id 4 层修复当作"首个 conformance"实施。

**理由**：
- 如果只修 goal_id 不立 spec，下一个横切维度（persona_id / knowledge_id / session_id）接入时，仍然会逐个出现"声明了字段但没全链路传透"的同类 bug。
- spec 一旦立起来，新维度接入有现成模板（写入入口 / 存储 schema / 读取过滤 / 消费方 scope 四问），不依赖工程师的记忆力。
- spec 本身可以驱动 `verify_web_skeleton.py` S7 守护断言（spec 是 invariant 源，守护断言是 invariant 检查器），形成正反馈。

**反对方案**（先修 goal_id，spec 之后补）：风险是修 goal_id 时引入隐式假设，后立 spec 时被迫迁就实现细节，spec 失去规范性。

---

## 2. LocalJsonBackend 的 goal_id 修法：文件名带 goal_id（用户已拍）

**选择**：文件名改为 `spider_xhs_采集结果_{goal_id}_{ts}.xlsx`，glob 时按 prefix 过滤。

**理由**（用户 2026-05-28 决策）：
- 改动小、隔离快
- 不需要 read 所有 xlsx 后内存过滤（行内冗余存方案的性能缺点）
- 缺点（同一 note 被多 goal 采集时多份存储）可接受：本质是不同 goal 的"立场视角"不同，分别存反而更清晰

**实施细节**：
- 文件名 sanitize：`goal_id` 中如有 `/`、`\`、空格 → 替换为 `_`（防 path injection）
- 老格式文件名 `spider_xhs_采集结果_{ts}.xlsx`（不带 goal_id）保留兼容读取：当 list 不带 goal_id 时全读；带 goal_id 时跳过老格式
- glob pattern：
  - 不带 goal_id：`spider_xhs_采集结果_*.xlsx`（含老格式）
  - 带 goal_id：`spider_xhs_采集结果_{goal_id}_*.xlsx`

---

## 3. `verify_web_skeleton.py` S7 守护断言形态

**选择**：用 spec 驱动（读 `data-dimensions/spec.md` 解析维度清单）+ ast 扫 + routes 反射混合。

**断言形态**（最小集合）：

```python
# S7 · 横切数据维度守护
import ast

# 从 spec 读维度清单（不 hardcode）
dimensions_spec = Path("openspec/specs/data-dimensions/spec.md").read_text("utf-8")
# 简单解析（spec 用 markdown，约定每个维度是 ## Requirement: <dim_name>）
covered_dims = re.findall(r"^## Requirement: (\w+)\s+全链路", dimensions_spec, re.M)

# 7.1 每个 backend.list_* 方法签名必须支持已立维度的过滤参数
backend_methods = inspect_backend_signatures()  # 扫 storage.base.StorageBackend protocol
for dim in covered_dims:
    methods_for_dim = SPEC_METHOD_MAP[dim]  # spec 内列出每个维度涵盖的方法
    for m in methods_for_dim:
        sig = backend_methods.get(m)
        check(f"{m} signature has {dim} parameter", dim in sig.parameters)

# 7.2 每个声明在 spec 中的 router endpoint 必须暴露过滤参数
for endpoint, dim in SPEC_ENDPOINT_DIM_MAP.items():
    route = find_route_by_path(srv_main.app.routes, endpoint)
    check(f"{endpoint} exposes {dim} query param", dim in route.dependant.query_params)
```

**理由**：
- 不 hardcode "goal_id" —— 否则未来加 persona_id / knowledge_id 都要改 verify 脚本，违反开闭原则
- ast 扫签名比 runtime 反射更稳（不依赖 import 成功 + RLS 初始化）
- routes 反射用 FastAPI dependant 而不是字符串匹配，更精确

**简化策略**：S7 节先实现"7.1 backend 签名守护"+"7.2 routes 参数守护"两条，不实现"7.3 prompt 内容守护"（prompt 拼装的守护成本高，留 P2/P3 后再加）。

---

## 4. 老数据 `goal_id=NULL` 的兼容策略

**选择**：API 层兼容、存储层强制。

| 场景 | 行为 |
|---|---|
| API list 不带 goal_id 参数 | 返回全部 tenant 数据（含老 NULL 数据） |
| API list 带 `goal_id=goal_001` | 严格过滤，老 NULL 数据不返回 |
| API list 带 `goal_id=` 空串 | 等同不带参数，返回全部（兼容老前端） |
| 新写入未带 goal_id | 后端返回 400 `ErrorCode.MISSING_GOAL_ID`（强制治理） |

**理由**：
- 读路径宽松，写路径严格——存量数据生命周期自然衰减，新数据强制规范
- 前端可以基于 `goal_id=null` 显示 "X 条历史数据未分配"，引导用户清理
- 不删除 / 不回填老数据——回填需要主观判断（哪条 note 是哪个 goal 的？），机器无法做

**新错误码**：`ErrorCode.MISSING_GOAL_ID = "missing_goal_id"`，加到 `server/errors.py`。

---

## 5. `data-dimensions/spec.md` 的写作粒度

**选择**：每个维度一个 `## Requirement`，4 个 `### Scenario`（对应"写入/存储/读取/消费"四问），不展开到字段名级别。

**理由**：
- 太粗（只说"必须保留"）→ 实施者无法验证合规
- 太细（每个 endpoint / 每个字段都列）→ 维护负担大，且字段名变更频繁
- 折中：每个维度的"应用面"列出来（如：goal_id 应用面 = 采集 + 选题 + 内容 + 策略 + 草稿），具体字段由 backend protocol 和 router 实现承载

**模板**（写在 spec 里供后续维度复用）：

```markdown
## Requirement: <dim_name> 全链路保留

系统 SHALL 保证 <dim_name> 维度在数据生命周期中不丢失，从写入入口到消费方全程可追踪。

**应用面**：列出涉及的能力域（采集 / 内容生成 / ...）

### Scenario: 写入入口必须带 <dim_name>
- WHEN 调用 <method_pattern>(...)
- THEN 参数 / body / row 必须包含 <dim_name>
- AND 缺失时返回 4xx 错误

### Scenario: 存储 schema 必须列 <dim_name>
- WHEN 数据落库（PG / 本地文件）
- THEN <dim_name> 必须作为独立列或文件名组成部分存在
- AND 老数据 NULL 兼容但不参与按 <dim_name> 的过滤

### Scenario: 读取入口必须暴露 <dim_name> 过滤参数
- WHEN GET /api/v1/<resource>
- THEN query 参数必须接受 <dim_name>
- AND 不传时返回全部（含 NULL），传具体值时严格过滤

### Scenario: 消费方必须按 <dim_name> scope
- WHEN prompt 拼装 / evidence 注入 / 决策生成
- THEN 只读取与当前 <dim_name> 匹配的数据
- AND 跨 <dim_name> 数据混读视为 bug
```
