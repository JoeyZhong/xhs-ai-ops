# Spec Delta: multi-tenant-storage

> 本 delta **MODIFIES** `agent-architecture-refactor` 中尚未归档的 multi-tenant-storage 草稿，把后端从 Supabase 切到阿里云 RDS PostgreSQL，并新增 RLS / 加密 / 物理分层等具体要求。

---

## MODIFIED Requirement: StorageBackend factory 选择 backend

系统 SHALL 通过环境变量 `STORAGE_BACKEND` 在 `LocalJsonBackend` 和 `PgBackend` 之间选择。

### Scenario: 通过 env var 选择 backend
- **WHEN** `STORAGE_BACKEND=local`
- **THEN** `storage.get_backend()` 返回 `LocalJsonBackend` 实例
- **WHEN** `STORAGE_BACKEND=postgres`
- **THEN** 返回 `PgBackend` 实例
- **WHEN** 未设或为其他值
- **THEN** raise `ValueError`，server 启动 fail fast

> **替换原 Supabase 引用**：原 spec 写的 `storage_backend: "supabase"` 改为 `STORAGE_BACKEND=postgres`，且配置源从 `settings.json` 改为 env var（避免敏感配置进 git）。

---

## ADDED Requirement: PostgreSQL 后端实现

系统 SHALL 在 `storage/pg_backend.py` 实现 `StorageBackend` Protocol 全部方法，每方法 MUST 通过 `db.session.get_rls_cursor(tenant_id)` 上下文管理器获取 cursor。

### Scenario: 业务方法走 RLS cursor
- **WHEN** 调用 `pg_backend.list_collected_data(tenant_id, since)`
- **THEN** 内部用 `with get_rls_cursor(tenant_id) as cur` 包裹查询
- **AND** SQL 语句仍带 `WHERE tenant_id = %s` 作为 app 层双保险

### Scenario: 跨租户读取被 RLS 拦截
- **WHEN** tenant A 写入一条 goal
- **AND** tenant B 用 PgBackend 调 `list_goals(tenant_id_B)`
- **THEN** 返回不包含 tenant A 的 goal

---

## ADDED Requirement: PostgreSQL 行级安全（RLS）强制启用

每个业务表 MUST 启用 RLS，policy 用 `current_setting('app.tenant_id')::uuid` 从 PG session 变量取 tenant_id。

### Scenario: RLS policy 通过 session var 隔离
- **WHEN** 业务表（goals / collected_notes / generated_posts / agent_memory / cookies / audit_log）创建
- **THEN** SQL 必须包含 `ALTER TABLE ... ENABLE ROW LEVEL SECURITY`
- **AND** `CREATE POLICY tenant_isolation USING (tenant_id = current_setting('app.tenant_id')::uuid)`

### Scenario: RLS session var 不跨事务残留
- **WHEN** `get_rls_cursor` 设置 `app.tenant_id`
- **THEN** 必须用 `SET LOCAL`（事务级），禁止用 `SET`（session 级）
- **AND** 事务结束后 conn 归还到 pool，下次借出时 `current_setting('app.tenant_id')` 必须为空

---

## ADDED Requirement: 数据库连接池

系统 SHALL 在 `db/session.py` 用 `psycopg2.pool.ThreadedConnectionPool`，禁止使用 `asyncpg` 等异步驱动。

### Scenario: 连接池在启动时初始化
- **WHEN** FastAPI lifespan startup
- **THEN** 调 `init_pool(DATABASE_URL, minconn=2, maxconn=20)`
- **WHEN** lifespan shutdown
- **THEN** 调 `pool.closeall()`

### Scenario: 同步 DB 调用包在 run_in_threadpool
- **WHEN** FastAPI async handler 需要调 PgBackend
- **THEN** MUST `await run_in_threadpool(pg_backend.method, ...)` 而非直接 `pg_backend.method(...)`

> **理由**：同步驱动 + run_in_threadpool 模式，避免对现有同步 Agent / LLM / Playwright 主循环的"异步传染"。

---

## ADDED Requirement: 数据库 Schema Migration 工具

系统 SHALL 用纯 SQL 文件 + 自写 runner 管理 schema 变更，禁止引入 Alembic 或 ORM 迁移工具。

### Scenario: migration 按版本号顺序执行
- **WHEN** 运行 `python -m db.migration_runner up`
- **THEN** 扫 `db/migrations/*.sql` 按文件名排序
- **AND** 与 `schema_migrations(version)` 表比对
- **AND** 未应用的按序执行，每文件单事务

### Scenario: migration 重跑 idempotent
- **WHEN** 同一 version 已经在 `schema_migrations` 表
- **THEN** runner 跳过该文件不重复执行

### Scenario: migration SQL 错误不部分提交
- **WHEN** 某个 migration SQL 中途报错
- **THEN** 该文件事务回滚
- **AND** `schema_migrations` 表不记录该 version
- **AND** runner 退出码 1

---

## ADDED Requirement: agent_memory 按 entry 拆行存储

`PgBackend` 的 memory 方法 MUST 按 entry_id 拆行存到 `agent_memory` 表，主键 `(tenant_id, scope, file, entry_id)`。

### Scenario: add_entry 通过 ON CONFLICT 实现幂等
- **WHEN** `pg_backend.write_memory_entry(tenant_id, scope='content', file='playbook.md', entry_id='e1', body='...')`
- **THEN** SQL `INSERT ... ON CONFLICT (tenant_id, scope, file, entry_id) DO UPDATE SET body = EXCLUDED.body, rev = agent_memory.rev + 1`

### Scenario: LocalJsonBackend 仍按 markdown 整存
- **WHEN** `STORAGE_BACKEND=local`
- **THEN** memory 仍存为 `memory/{tenant}/{scope}/{file}.md` 整文件
- **AND** entry 拆分由 LocalJsonBackend 在 markdown 解析层实现（保持人类可读）

---

## ADDED Requirement: 一次性数据迁移脚本

系统 SHALL 提供 `scripts/migrate_to_pg.py`，把现有 LocalJsonBackend 数据一次性灌入 PgBackend，支持 dry-run 和 verify 模式。

### Scenario: dry-run 不写库
- **WHEN** `python scripts/migrate_to_pg.py --tenant-id X --dry-run`
- **THEN** 只统计待迁移行数，不执行 INSERT
- **AND** 输出每张表的 source_count

### Scenario: verify 模式比对源与目标
- **WHEN** `python scripts/migrate_to_pg.py --tenant-id X --verify`
- **THEN** 迁移完成后对每张表做 count 比对 + 抽样字段比对
- **AND** 任何 mismatch 退出码 1 并 stdout 列出差异

---

## ADDED Requirement: Safe Big Bang Cutover

系统 SHALL 通过 env var `STORAGE_BACKEND` 支持秒级回滚，cutover 前的本地源文件必须保留为 `.bak` 至少 1 个月。

### Scenario: cutover 后回滚到 LocalJsonBackend
- **WHEN** PG cutover 后发现致命 bug
- **AND** 执行：停服 → `STORAGE_BACKEND=local` → `.bak` 文件改名回原 → 重启
- **THEN** server 重新读本地文件，数据 0 损失

### Scenario: cutover 切流窗口必须停服
- **WHEN** 跑 `migrate_to_pg.py` 之前
- **THEN** uvicorn 和 BackgroundScheduler MUST 停止
- **AND** 切流窗口期间 0 写入

---

## ADDED Requirement: 物理分层结构

存储相关代码 MUST 按 `security/` `db/` `storage/` `scripts/` 四层组织，禁止把连接池管理 / RLS / 业务 CRUD / 加密 全部塞到单个 storage/postgres.py 文件。

### Scenario: 模块职责清晰
- **WHEN** review 加密算法变更
- **THEN** 仅需改动 `security/kms.py` 和 cookie 写入语句
- **WHEN** review 业务方法新增
- **THEN** 仅需改动 `storage/pg_backend.py`
- **WHEN** review driver 切换
- **THEN** 仅需改动 `db/session.py`
