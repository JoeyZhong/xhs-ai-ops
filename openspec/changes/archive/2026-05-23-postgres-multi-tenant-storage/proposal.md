# Proposal: 多租户存储迁移到阿里云 RDS PostgreSQL（中国大陆部署）

> **创建日期**：2026-05-11
> **触发**：业务要 SaaS 化对外，单租户单机的本地文件存储已是天花板
> **代号**：Phase 4（与 agent-architecture-refactor §4 对齐，但**改技术栈**为阿里云 RDS PG，否决 Supabase）
> **依据决策**：Q1–Q9 共 9 条已对齐（见 `design.md §1`）

---

## Problem Statement

当前 Spider_XHS 是**单租户·单机·多文件**形态：所有数据散落在 `config/goals.json`、`xhs_data/*.xlsx`、`config/cookies.db`（SQLite）、`memory/default/**/*.md`、`audit_*.jsonl`。这种形态有三个致命缺陷阻止平台对外提供服务：

1. **不可水平扩展**：所有写入依赖本地文件锁。开两个 worker pod 立刻陷入 SQLite WAL 冲突
2. **不可多租户隔离**：`memory/default/` 的 `default` 是写死的，不存在第二个客户的概念
3. **不可灾备**：本地磁盘损坏 = 数据全丢。`xhs_data/` 7 天自清理 = 历史回溯不可能

且原计划在 `agent-architecture-refactor §4` 用 Supabase，**在中国大陆不可行**——Supabase Cloud 数据中心境外，跨境数据传输违反《数据出境安全评估办法》（2022.9）和 PIPL；自托管 Supabase 又强行运维 10+ Docker 微服务，对单人团队是负担。

## Solution

把所有数据搬到**阿里云 RDS PostgreSQL 17**（境内、合规、托管），用 **PostgreSQL 原生 RLS** 做多租户隔离，**JWT** 替代静态 Bearer token 携带 `tenant_id`，**pgcrypto** 列加密保护 Cookie 凭据，**纯 SQL migrations** 保留未来无缝平移到 Supabase 的可能。

物理分四层：

```
security/   鉴权与密钥（jwt / middleware / kms）
db/         基建（psycopg2 ThreadedConnectionPool + RLS cursor + migrations）
storage/    业务仓储（pg_backend / cookie_manager）
scripts/    一次性脚本（migrate_to_pg.py）
```

cutover 走 **Safe Big Bang**：停服 → 跑迁移脚本 → `.env` 翻 `STORAGE_BACKEND=postgres` → 重启。本地旧文件 `.bak` 保留 ≥ 1 个月做秒级回滚兜底。

零文件方案——**Phase 4 不引入对象存储**。图片直接引用小红书 CDN URL，xlsx 用 `BytesIO + StreamingResponse` 实时生成，**容器纯无状态**（Stateless）。

---

## Why（启动时机的依据）

- ✅ **业务驱动**：要给第二个客户开通服务，单租户架构挡路
- ✅ **抽象就绪**：`storage/base.py` 的 `StorageBackend` Protocol 已在 P4 之前合并（agent-architecture-refactor §6.1），切换 backend 不动业务代码
- ✅ **决策已收口**：Q1–Q9 共 9 条决策已对齐（见 `design.md §1`），架构师审过
- ✅ **法规明确**：PIPL + 数据出境办法明确要求境内部署，路径清晰
- ✅ **回滚有底**：本地文件 `.bak` 保留是秒级回滚的天然安全网

## What

### 范围（in-scope）

- 新建 `security/`（jwt + middleware + kms）和 `db/`（session + migrations + runner）两个目录
- 改造 `storage/`：新增 `pg_backend.py`，改造 `cookie_manager.py` 后端从 SQLite 切到 PG（保接口不变）
- 用 `migrations/NNN_*.sql` 建表 + 启用 RLS + pgcrypto 扩展
- `scripts/migrate_to_pg.py` 一次性倒库，包含 dry-run + verify 模式
- `.env` 加 `DATABASE_URL` / `MASTER_ENCRYPTION_KEY` / `JWT_SECRET` / `STORAGE_BACKEND`
- `docker-compose.dev.yml`：本地开发用 `postgres:17-alpine`
- 切换 `verify_token` 实现：从静态 Bearer 升级到 JWT HS256
- 前端 `lib/api.ts` 改用 JWT；`/settings` 加 token 输入入口

### 不范围（out-of-scope）

- **对象存储**：OSS 推到 Phase 5。当前业务采集图片只用作前端预览，引用小红书 CDN URL 即可
- **永久素材库**：账号自上传图片/视频功能未来 PR
- **跨账号/跨租户的全局看板**：超管视图未来 PR
- **JWT 撤销 / 黑名单**：v1 用短期 token（默认 24h）+ 重签发，不引入黑名单基础设施
- **KMS 主密钥托管**：v1 用 env var；v2 升阿里云 KMS 信封加密
- **租户注册自助流程**：v1 由超管 CLI 手工建租户和签 token
- **Schema-per-tenant**：选 RLS 方案后明确否决
- **DualWriteBackend / 双写双读**：架构师否决，理由见 `design.md §3`
- **asyncpg / 异步驱动**：与现有同步 Agent 主循环不兼容，已否决
- **历史 xlsx 永久存档**：旧文件 `.bak` 保留 1 个月即退役
- **i18n / 多语言**：中文界面优先

### 阶段划分

```
P4.0 准备（docker compose + .env scheme + dry run dataset）
  ↓
P4.1 db/ 基建（session / migrations / runner）  ┐
                                                ├─ 可并行
P4.2 security/ 边界（jwt / middleware / kms）   ┘
  ↓
P4.3 storage/ 业务（pg_backend + cookie_manager 改造）
  ↓
P4.4 scripts/migrate_to_pg.py + dry-run 验证
  ↓
P4.5 Cutover 窗口（停服 → 倒库 → .env → 重启）
  ↓
P4.6 验收 + 归档（spec 合入 + 改动目录归档）
```

| 阶段 | 范围 | 预计工时 |
|---|---|---|
| P4.0 | docker compose + 新增 .env 字段 | 0.5 天 |
| P4.1 | db 基建 + 6 个 migration sql | 1 天 |
| P4.2 | security 三件套 + 切换 verify_token | 1 天 |
| P4.3 | pg_backend 14 方法 + cookie 切换 | 2 天 |
| P4.4 | migrate_to_pg.py + 一致性验证 | 1 天 |
| P4.5 | Cutover 窗口（含回滚演练） | 0.5 天 |
| P4.6 | 验收 + 归档 | 0.5 天 |
| **合计** | | **~6.5 天** |

---

## User Stories

### 平台运营者（你 / 超管）

1. 作为超管，我希望开通新客户时只需运行 `python scripts/issue_token.py --tenant-name X`，立刻拿到一个 JWT 给客户用，不必手动建文件夹
2. 作为超管，我希望客户 A 的 Cookie 被攻击者拿到后，不会泄漏客户 B 的 Cookie，用 RLS + pgcrypto 双层保险
3. 作为超管，我希望 Phase 4 上线后云数据库 1 周内出问题可以一键回滚到本地文件模式（.env 翻 + 重启），数据 0 损失
4. 作为超管，我希望本地开发用 `docker compose up postgres` 一键起 Postgres，不必连云 RDS 浪费费用
5. 作为超管，我希望所有 SQL migration 是版本化的纯文件（`migrations/NNN_*.sql`），可以 git diff，不依赖 ORM
6. 作为超管，我希望 Cookie 失效或异常时 audit_log 表里有记录，能事后追溯
7. 作为超管，我希望 cutover 窗口可控在 10 分钟内（停服 → 跑脚本 → 重启）

### 客户租户（业务用户）

8. 作为客户租户，我希望我看不到其他租户的 goals / notes / personas / playbook 任何数据，连 SQL 注入都串不到（RLS 兜底）
9. 作为客户租户，我希望我的 Cookie 在数据库里是加密的，DBA 直接 SELECT 看到的是密文
10. 作为客户租户，我希望前端发起请求时只需在 header 带一个 JWT，不必每次输 token
11. 作为客户租户，我希望 token 过期后前端自动跳到登录页（401），而不是在我编辑到一半时静默失败
12. 作为客户租户，我希望可以导出 Excel 给老板看（保留 `GET /notes/export.xlsx` 入口），文件名带租户标识

### 开发者（你 / 未来贡献者）

13. 作为开发者，我希望 `pg_backend.py` 里的每个 CRUD 方法都用 `with get_rls_cursor(tenant_id) as cur` 包起来，RLS 注入对调用方透明
14. 作为开发者，我希望 SQL migration 用 raw 文件而非 Alembic ORM，保留未来切到 Supabase 原生 migration 工具的可能
15. 作为开发者，我希望 master encryption key 缺失时**启动时立即 fail fast**，而不是某次 cookie 写入时才崩
16. 作为开发者，我希望 `db/session.py` 用 `SET LOCAL` 而非 `SET`，连接归还时 RLS 上下文自动清空，不会跨租户泄漏
17. 作为开发者，我希望同步 `psycopg2` + `ThreadedConnectionPool` 而不是 `asyncpg`，避免和现有 Agent 主循环 / Playwright 的"异步传染"
18. 作为开发者，我希望 PR 里的测试覆盖 RLS 隔离 + JWT 流转 + 迁移一致性 + Cookie 加密，四条主线都不破

### 安全审计员

19. 作为安全审计员，我希望 RLS 是 PG 层强制的（不是仅 app 层 WHERE），就算 app 代码出 bug 也不会跨租户读
20. 作为安全审计员，我希望 Cookie 列是 `pgcrypto.pgp_sym_encrypt` 加密的，攻击者拿到 DB 备份也读不出明文
21. 作为安全审计员，我希望 JWT 用 HS256 + 密钥轮换支持，且过期时间可配置
22. 作为安全审计员，我希望审计日志（audit_log 表）也按 tenant_id 隔离，租户互相看不到对方的审计

---

## Implementation Decisions

> 完整列表见 `design.md §1 决策矩阵`。摘要：

- **数据库**：阿里云 RDS PostgreSQL 17（境内、合规、托管）
- **隔离**：行级安全（RLS）+ app 层 `WHERE tenant_id=$1` 双保险，1 tenant 起步
- **驱动**：`psycopg2 + ThreadedConnectionPool`，配合 FastAPI `run_in_threadpool`
- **Auth**：JWT HS256，claims = `{sub: tenant_id, exp, iat}`
- **Migration**：`migrations/NNN_*.sql` + 30 行 `db/migration_runner.py`，不引入 Alembic
- **Cookie**：合入 PG `cookies` 表，列加密 `pgcrypto.pgp_sym_encrypt(cookie, $MASTER_KEY)`
- **Memory**：按 entry 拆行（`agent_memory(tenant_id, scope, file, entry_id, body, status, rev)`），LocalJsonBackend 仍按 markdown 存
- **媒体/Excel**：零文件——图片引用小红书 CDN URL，xlsx 用 `BytesIO + StreamingResponse` 实时生成
- **Cutover**：Safe Big Bang——停服 → 倒库 → `.env` 翻 → 重启；本地文件 `.bak` 保留 ≥ 1 月
- **本地开发**：`docker-compose.dev.yml` 起 `postgres:17-alpine`
- **物理分层**：`security/` + `db/` + `storage/` + `scripts/` 四层

### 模块清单（10 个新增/改造）

| 层 | 模块 | 类型 | 职责 |
|---|---|---|---|
| security | `jwt.py` | deep | HS256 编解码 + sub claim 抽取 + CLI 签发 |
| security | `middleware.py` | deep | FastAPI dependency：JWT verify + tenant_id 注入请求上下文 |
| security | `kms.py` | shallow | 启动时读 `MASTER_ENCRYPTION_KEY`，缺失 fail fast |
| db | `session.py` | deep | psycopg2 ThreadedConnectionPool + `get_rls_cursor(tenant_id)` 上下文管理器 |
| db | `migration_runner.py` | shallow | 比对 schema_migrations + 按序执行 |
| db | `migrations/NNN_*.sql` | shallow | 6 个文件：init / RLS / pgcrypto / cookies / indexes / seed |
| storage | `pg_backend.py` | deep | 14 个 StorageBackend 方法的 PG 实现 |
| storage | `cookie_manager.py` | 改造 | SQLite → PG + pgcrypto，接口不变 |
| scripts | `migrate_to_pg.py` | shallow | 一次性倒库 + dry-run + verify |
| 配置 | `docker-compose.dev.yml` | 新增 | 本地开发起 postgres:17-alpine |

> 详细 API 契约 / SQL schema / RLS 策略见 `design.md §2-§5`。

---

## Testing Decisions

### 测试原则

- **只测外部行为，不测实现细节**：测的是"双租户穿不过 RLS"，不是"内部用什么 cursor"
- **关键路径用 testcontainers 起真 PG**：RLS / pgcrypto / SET LOCAL 在 SQLite 测不出来
- **JWT / 加密 / 迁移**用 unit test 覆盖（不需要 PG）
- **优先级**：RLS 隔离 > Cookie 加密 > 迁移一致性 > JWT 流转

### 测试矩阵

| 模块 | 测试类型 | 关键场景 | Prior art |
|---|---|---|---|
| `db/session.py` | testcontainers PG | 双租户穿不过 RLS；同事务内 `SET LOCAL` 生效；事务回滚后下一次 cursor 不残留 tenant_id | 无（新建） |
| `security/jwt.py` | unit | 缺 token 401 / 伪造签名 403 / 过期 401 / sub 缺失 422 | `tests/test_f1_api.py::test_auth_*` |
| `scripts/migrate_to_pg.py` | integration | LocalJson → PG 后 count 一致；sample rows 字段一致；dry-run 不写库 | `tests/test_storage_migration.py`（待新建） |
| `storage/cookie_manager.py` | testcontainers PG | 写入后 SELECT 还原；改 `MASTER_KEY` 后读不出（解密失败 raise）；DBA 直 SELECT 列拿到密文 | `tests/test_cookie_manager.py` |
| `storage/pg_backend.py` | testcontainers PG | 14 方法 happy path；跨 tenant 读返回空（RLS 拦） | 无（新建） |
| `db/migration_runner.py` | integration | 重跑同一文件不双写；SQL 错误不部分提交；schema_migrations 表正确累积 | 无（新建） |

### 验收用例（端到端）

1. `cd ops && docker compose up -d postgres` → DB 起来
2. `python scripts/migrate.py up` → 4 个 migration 都跑完，schema_migrations 表 4 行
3. `python scripts/issue_token.py --tenant-name shenzhen-pj` → 拿到 JWT
4. 用 JWT 调 `GET /api/v1/goals` → 200 + 空列表（新租户）
5. `POST /api/v1/goals` 建一个 goal → 写库成功
6. 用**另一个**租户的 JWT 再 `GET /api/v1/goals` → 看不到上面那个 goal（RLS 兜底）
7. 在 PG 里 `SELECT cookie FROM cookies` → 看到密文
8. 改 `MASTER_ENCRYPTION_KEY` 后调 cookie 读取 → raise（解密失败）

---

## Out of Scope

| 项 | 推后到 | 原因 |
|---|---|---|
| 阿里云 OSS 对象存储 | Phase 5 | 当前业务图片只做预览，引用 XHS CDN URL 够用 |
| 账号自上传素材库 | 未来 PR | 当前没该功能 |
| 阿里云 KMS 信封加密 | v2 | 单租户起步 env var 够用，门槛比读文件高一档 |
| JWT 黑名单 / 撤销 | 未来 PR | 短 token + 重签发成本最低 |
| 租户注册自助 | 未来 PR | 超管 CLI 手工开通即可 |
| Schema-per-tenant | 永远不做 | 已选 RLS 方案 |
| DualWriteBackend | 永远不做 | 架构师否决，理由见 `design.md §3` |
| asyncpg | 永远不做 | 与同步 Agent 主循环冲突 |
| 历史 xlsx 永久存档 | 永远不做 | 旧文件 .bak 1 月后退役 |
| i18n | 未来 PR | 中文界面优先 |
| 跨租户全局看板 | 未来 PR | 超管视图独立 |
| `agent-architecture-refactor §4` 的 Supabase 路径 | **作废** | 本 change 替代之 |

---

## Further Notes

### 与 agent-architecture-refactor 的关系

本 change **替代** agent-architecture-refactor 的 §4 Supabase 实施任务。归档时：

- agent-architecture-refactor §4.x 全部任务标 `~~strikethrough~~ 由 postgres-multi-tenant-storage 替代`，不再 check
- 但 §6.1 `StorageBackend` Protocol 抽象**保留**——是本 change 的前置依赖
- agent-architecture-refactor 自身可继续走完归档流程（除了 §4），不被本 change 阻塞

### 关于"JWT 起步只 1 tenant 是否过度"

不是过度。理由：

- 多租户代码路径**总要有人写**——晚写不如早写，越晚改造代价越大（DB 表要 alter、所有查询要回填 WHERE）
- 单 tenant 起步走多租户路径的代价 = 多 1 个 JWT 签发脚本 + 每查询多一个 `tenant_id` 参数（这是 OK 的）
- 收益 = 第二个客户接入时**零迁移**

### 关于"零文件方案是否激进"

正如 Q8 讨论所说：当前业务模型下图片只用作预览，引用小红书 CDN URL 够用。但要在 PRD 里**明确告诉未来读者**——这是个**业务约束驱动的决定**，不是普适的"零文件最佳实践"：

- ❌ 如果未来加"账号本地素材库"——必须上 OSS
- ❌ 如果发现小红书反盗链严格 + 频繁 hotlink 失败——必须上 OSS
- ✅ 当前业务模型下，OSS 是过度设计

### 关于回滚预案的可信度

回滚预案的关键不在"代码层支持回滚"——而在 **"`.env` 翻 + 重启 = 数据 0 损失"是真的吗**。它是真的，因为：

1. `LocalJsonBackend` 在 cutover 前所有数据都还在
2. `migrate_to_pg.py` 是**只读源 + 写目标**，不删源
3. `.env` 翻回 `STORAGE_BACKEND=local` 后，server 重启读到的是原封不动的 `config/goals.json` 等
4. 切流窗口（停服那 ~10 分钟）期间没新数据写入

所以回滚预案的可信度核心是 **"切流窗口必须停服 10 分钟"**，不是其他任何技术细节。这是 P4.5 的硬约束。

### 关于 Phase 4 之后

P4 完成后，下一阶段（Phase 5 候选）的重点：

- OSS 对象存储（如果踩到上面提到的"零文件方案破口"）
- KMS 信封加密（如果第二个客户对合规要求更严）
- 租户自助注册流程（如果开通客户的频率超过"每周 1 个"）
- 全局看板 / 跨租户分析（超管自用）
- Read replica / 读写分离（如果业务量起来）

这些都不放在 Phase 4 范围。Phase 4 的目标是**"能开第二个客户"**，不是**"能开 100 个客户"**。
