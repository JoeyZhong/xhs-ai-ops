# Spec Delta: multi-tenant-storage

> 新建 capability。所有条款 `## ADDED`。

## ADDED Requirement: StorageBackend 抽象接口

系统 SHALL 在 `storage/base.py` 定义 `StorageBackend` Protocol，最小方法集：

```python
class StorageBackend(Protocol):
    def save_task_result(self, tenant_id: str, task_id: str, result: dict) -> None: ...
    def load_memory(self, tenant_id: str, scope: str, file: str) -> str | None: ...
    def save_memory(self, tenant_id: str, scope: str, file: str, content: str) -> None: ...
    def list_collected_data(self, tenant_id: str, since: datetime) -> pd.DataFrame: ...
    def save_collected_data(self, tenant_id: str, source: str, df: pd.DataFrame) -> None: ...
    def save_audit_log(self, tenant_id: str, entry: dict) -> None: ...
    def load_goal(self, tenant_id: str, goal_id: str) -> dict | None: ...
    def save_goal(self, tenant_id: str, goal: dict) -> None: ...
```

### Scenario: 通过 factory 选择 backend
- **WHEN** 配置 `storage_backend: "local"`
- **THEN** `get_backend()` 返回 `LocalJsonBackend` 实例
- **WHEN** 配置 `storage_backend: "supabase"`
- **THEN** 返回 `SupabaseBackend` 实例

---

## ADDED Requirement: 本地 backend 向下兼容

`LocalJsonBackend` SHALL：
- 把数据存到 `xhs_data/{tenant_id}/...`（默认 tenant_id="default"）
- 兼容现有 Excel 文件结构（继续读写 `spider_xhs_采集结果_*.xlsx` 等）
- 配置文件继续用 `config/goals.json` 和 `config/persona.json`，但路径改为 `config/{tenant_id}/`

### Scenario: 老用户首次升级
- **GIVEN** 现有用户没有 tenant_id 概念，文件在 `xhs_data/` 和 `config/`
- **WHEN** 升级后启动
- **THEN** 自动将现有文件视为 tenant_id="default"
- **AND** 新数据写入 `xhs_data/default/...`
- **AND** 老路径继续可读（兼容期）

---

## ADDED Requirement: Supabase 表结构与 RLS

`SupabaseBackend` SHALL 使用 architecture_spec.md §6.2 中定义的 schema：
- `tenants` / `goals` / `collected_notes` / `hot_keywords` / `generated_posts` / `agent_memory` / `audit_log`
- 所有表 MUST 启用 Row Level Security
- RLS 策略基于 `app.tenant_id` session 变量

### Scenario: 跨租户查询被 RLS 拦截
- **GIVEN** session 变量 `app.tenant_id = 'tenant-A'`
- **WHEN** 查询 `SELECT * FROM goals WHERE id = 'goal_001'` 但该 goal 属于 tenant-B
- **THEN** 返回空结果（不是错误，是 RLS 静默过滤）

### Scenario: 写入时自动带 tenant_id
- **WHEN** Backend 调用 `save_collected_data(tenant_id='tenant-A', ...)`
- **THEN** INSERT 语句中 tenant_id 字段自动设为 'tenant-A'
- **AND** 不需要业务代码显式传

---

## ADDED Requirement: 多租户隔离测试

系统 SHALL 提供端到端测试验证租户隔离：
- 创建 2 个 tenant
- 在 tenant-A 写入数据
- 用 tenant-B 的 session 查询，必须读不到 tenant-A 数据
- 直接 SQL 注入也不能绕过 RLS

### Scenario: tenant-B 看不到 tenant-A 数据
- **GIVEN** tenant-A 有 100 篇 collected_notes
- **WHEN** 切换到 tenant-B 的 session 查询
- **THEN** 返回 0 条

### Scenario: 不传 tenant_id 时拒绝写入
- **WHEN** 调用 backend 方法但未提供 tenant_id 或为空
- **THEN** 抛 `TenantContextRequired`

---

## ADDED Requirement: 数据迁移工具

系统 SHALL 提供 `scripts/migrate_local_to_supabase.py`：
- 从 LocalJsonBackend 读取所有数据
- 写入到 Supabase（指定目标 tenant_id）
- 支持 dry-run 模式（预览将迁移的数据量）
- 写入前后做条数核对

### Scenario: 迁移流程
- **WHEN** 运行 `python scripts/migrate_local_to_supabase.py --tenant-id=xxx --dry-run`
- **THEN** 输出"将迁移 N 条 collected_notes / M 条 generated_posts / ..."
- **AND** 不实际写入

### Scenario: 迁移失败回滚
- **WHEN** 迁移过程中某张表写入失败
- **THEN** 已写入的数据用事务回滚（每张表一个事务）
- **AND** 留下完整错误日志便于排查

---

## ADDED Requirement: Backend 切换的可观测性

Dashboard SHALL 显式展示当前 backend：
- ⚙️ API 配置页加 "数据源" 选择器
- 顶部状态栏显示当前 backend 名（"📁 本地" / "☁️ Supabase"）
- 切换 backend 后必须重启 dashboard 生效（不支持热切换）

### Scenario: 配置错误时不静默
- **WHEN** 配置 backend=supabase 但 SUPABASE_URL 未设
- **THEN** dashboard 启动时显示醒目错误，引导用户去 ⚙️ API 配置页填写
- **AND** 不会静默退回本地 backend
