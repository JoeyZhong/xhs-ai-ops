# Design: 多租户存储迁移到阿里云 RDS PostgreSQL

> 本文档是 `proposal.md` 的架构补充，回答"为什么这么选"。

---

## 1. 决策矩阵（Q1–Q9）

| # | 决策点 | 结论 | 理由摘要 |
|---|---|---|---|
| Q1 | 数据库 | 阿里云 RDS PostgreSQL 17 | 境内合规（PIPL + 数据出境办法）；托管运维负担最低；不需要 Supabase 的 GoTrue/PostgREST 全套 |
| Q2 | 租户隔离 | 多租户 RLS + app 层 WHERE 双保险，起步 1 tenant | RLS 在 PG 层强制，app 层 WHERE 防 RLS bypass bug；Schema-per-tenant 上百客户后 migration 噩梦 |
| Q3 | Auth | Bearer 升级 JWT HS256，claims = {sub: tenant_id, exp, iat} | 每请求不查 DB；起步 HS256 简单，未来 SSO 再升 RS256；token 失效靠短 TTL（24h）+ 重签发 |
| Q4 | Migration | `migrations/NNN_xxx.sql` + 30 行 `migration_runner.py` | 不用 Alembic（避免引入 ORM）；纯 SQL 可平移到 Supabase 原生 migration 工具 |
| Q5 | Cookie | PG 统一存储 + `pgcrypto.pgp_sym_encrypt` + env var 主密钥 | 否决"SQLite + 文件隔离"——不 stateless、备份地狱、RCE 一锅端；KMS 推到 v2 |
| Q6 | 驱动 | `psycopg2 + ThreadedConnectionPool + run_in_threadpool` | 否决 asyncpg——避免对现有同步 Agent 主循环 / Playwright 的"异步传染" |
| Q7 | 数据迁移 | 一次性 `scripts/migrate_to_pg.py`（Big Bang），dry-run → 导入 → `.bak` 保留 ≥ 1 月 | 否决双写双读——单人维护下 OCC 版本同步是泥潭；"`.env` 翻 + 重启 = 秒级回滚"是 Big Bang 的安全网 |
| Q8 | 文件存储 | 纯 Postgres·零文件。XLSX 用 `BytesIO + StreamingResponse` 实时生成；图片引用小红书 CDN URL | OSS 推到 Phase 5；当前业务模型下图片只用作预览，CDN URL 够用 |
| Q9 | 本地开发 | `docker-compose.dev.yml` 起 `postgres:17-alpine` | 单一命令起本地 PG；版本与生产对齐；不连云 RDS 浪费费用 |

### 被否决的方案备忘（避免未来重新讨论）

| 否决项 | 否决理由 |
|---|---|
| **Supabase Cloud** | 数据中心境外，跨境违反《数据出境安全评估办法》和 PIPL |
| **Supabase 自托管** | 强行运维 GoTrue/PostgREST/Kong 等 10+ 容器；GoTrue 我们已用 Bearer 替代、PostgREST 用 FastAPI 替代 → 80% 重叠 |
| **Cookies 留 SQLite** | 破坏 stateless；备份时间一致性地狱；RCE 时纵深防御是假象（攻击者一个 `cat` 就拿走 .db 文件） |
| **DualWriteBackend / 双写双读** | 单人维护下，部分失败处理 + OCC 版本冲突同步是 2-3 周的额外工作；旧文件作为安全网已经够了 |
| **asyncpg / 异步驱动** | 现有 Agent 主循环、LLM 调用、Playwright 全是同步；引入 asyncpg 会"异步传染"全链路改造 + 阻塞 SSE event loop |
| **Schema-per-tenant** | 当 tenant 上百时 migration 要逐 schema 跑，迁移脚本噩梦 |
| **app 层 WHERE 单保险** | RLS 不要的话，任何业务代码忘加 WHERE 都会跨租户泄漏；RLS 是 PG 层强制 |
| **Alembic** | 当前没用 SQLAlchemy ORM，只为 migration 引入 Alembic 太重；`raw SQL + runner` 30 行就够 |
| **Phase 4 阶段引入 OSS** | 当前业务模型下图片只用作预览，CDN URL 够；上 OSS 是过度设计 |
| **KMS 信封加密 v1 引入** | 单租户起步 env var 够用；攻击者要 RCE + dump env 已经比"读 SQLite 文件"门槛高 |

---

## 2. 物理分层架构

```
┌─────────────────────── Application 层 ────────────────────────┐
│  server/routers/*.py       agents/*.py                       │
│         ↓                       ↓                            │
│  storage.get_backend()    通过 get_rls_cursor 走 PG          │
└──────────────┬───────────────────────┬───────────────────────┘
               ↓                       ↓
┌──────── Storage 业务层 ────────┐  ┌───── Security 边界层 ─────┐
│ pg_backend.py (14 CRUD)        │  │ jwt.py (encode/decode)   │
│ cookie_manager.py (PG+加密)    │  │ middleware.py (FastAPI)  │
│ local_json.py (兼容兜底)       │  │ kms.py (env var fail-fast)│
└──────────────┬─────────────────┘  └─────────────┬─────────────┘
               ↓                                   │
┌────────────── DB 基建层 ──────────────────────┐  │
│ session.py                                    │  │
│   ├─ ThreadedConnectionPool (psycopg2)        │  │
│   └─ get_rls_cursor(tenant_id) ────────────┐  │  │
│ migration_runner.py                         │  │  │
│ migrations/001..004.sql                     │  │  │
└─────────────────────────────────────────────┼──┘  │
                                              ↓     │
┌────────────────── PostgreSQL 17 ─────────────────────────────┐
│ ALTER TABLE ... ENABLE ROW LEVEL SECURITY                    │
│ CREATE POLICY USING (tenant_id = current_setting(...)::uuid) │
│ pgcrypto: pgp_sym_encrypt / pgp_sym_decrypt                  │
└──────────────────────────────────────────────────────────────┘
```

### 为什么这么分

- **`security/` 收口鉴权与密钥**：避免散落在 routers / utils / middleware 各处。审计员 review 时只看一个目录
- **`db/` 是基建，零业务**：`session.py` / `migration_runner.py` / SQL 文件 三件套。任何业务模块不引这一层就是滥用
- **`storage/` 是仓储，零基建关心**：`pg_backend.py` 的所有方法都用 `with get_rls_cursor(tid) as cur` 一行打开，不关心 pool/事务/RLS 细节
- **`scripts/` 是一次性工具**：`migrate_to_pg.py` 永远不被业务代码 import

### 反模式：原"塞一个 storage/postgres.py 1500 行" 为何不对

- 该文件会**同时**承担：连接池管理、RLS 注入、14 个业务 CRUD、加密解密、事务边界 → God Object
- review 时无法定位"加密改了"还是"业务改了"
- 测试时无法 mock 一层（连接池和业务 CRUD 在同一文件）
- 升级 driver / 切 pool 实现需要 touch 所有方法

物理分层后这些问题全部消失：
- driver 切换 → 只动 `db/session.py`
- 加密算法升级 → 只动 `security/kms.py` 和 cookie 写入语句
- 业务方法新增 → 只动 `storage/pg_backend.py`
- 鉴权策略升级（HS256 → RS256） → 只动 `security/jwt.py`

---

## 3. 关键 API 契约

### 3.1 `db.session.get_rls_cursor`

```python
@contextmanager
def get_rls_cursor(tenant_id: str) -> Iterator[psycopg2.cursor]:
    """
    从 ThreadedConnectionPool 借 conn，开事务，激活 RLS，归还时自动提交/回滚。

    用法：
        with get_rls_cursor(tid) as cur:
            cur.execute("SELECT * FROM goals")  # RLS 自动按 tid 过滤
            ...
    """
```

**关键实现细节**：

- 用 `SET LOCAL app.tenant_id = '<tid>'` 而非 `SET`
  - `SET LOCAL` 仅在当前事务有效，事务结束（commit/rollback）自动清空
  - `SET` 是 session 级别，连接归还到 pool 后下次借出仍残留 → **跨租户泄漏**
- `tenant_id` 必须用 SQL 参数绑定，禁止字符串拼接（防 SQL 注入到 `current_setting`）
- 异常自动 rollback（`__exit__` 看 exc_type）

### 3.2 `security.jwt.encode/decode`

```python
def encode(tenant_id: str, ttl_hours: int = 24) -> str:
    """HS256 编码，claims = {sub: tenant_id, iat, exp}"""

def decode(token: str) -> dict:
    """
    抛 InvalidTokenError（伪造）/ ExpiredSignatureError（过期）。
    返回 dict，调用方用 claims["sub"] 取 tenant_id。
    """
```

**为何 sub 而非 tenant_id**：JWT 标准把 `sub`（subject）定义为"主体标识"，正符合"这个 token 代表哪个 tenant"语义。少自定义字段名 = 更易被通用工具/库识别。

### 3.3 `security.middleware.verify_jwt`

```python
def verify_jwt(authorization: str = Header(...)) -> str:
    """
    FastAPI dependency。返回 tenant_id。
    缺 header → 401；伪造 → 403；过期 → 401。
    """
```

替换现有 `server/auth.py::verify_token`。所有 routers 现有写法 `_: str = Depends(verify_token)` 不需改（保持接口兼容）；新写法可拿到 tenant_id：`tenant_id: str = Depends(verify_jwt)`。

### 3.4 `storage.pg_backend.PgBackend`

实现 `StorageBackend` Protocol 全部 14 方法。每方法的标准模板：

```python
def list_collected_data(self, tenant_id: str, since: datetime) -> pd.DataFrame:
    with get_rls_cursor(tenant_id) as cur:
        cur.execute(
            "SELECT * FROM collected_notes "
            "WHERE tenant_id = %s AND collected_at >= %s "  # app 层 WHERE 双保险
            "ORDER BY collected_at DESC",
            (tenant_id, since),
        )
        return pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
```

注意：`WHERE tenant_id = %s` **不是冗余**——它是双保险层。万一 `get_rls_cursor` 实现 bug 导致 RLS 没生效，app 层 WHERE 仍能拦住。

### 3.5 `storage.cookie_manager` 改造

**接口签名不变**（外部调用零感知）：

```python
def get_cookie(account_id: str) -> str | None: ...
def save_cookie(account_id: str, cookie: str) -> None: ...
```

**内部改动**：

```python
# 写入
cur.execute(
    "INSERT INTO cookies (tenant_id, account_id, cookie_encrypted, valid) "
    "VALUES (%s, %s, pgp_sym_encrypt(%s, %s), TRUE) "
    "ON CONFLICT (tenant_id, account_id) DO UPDATE SET ...",
    (tenant_id, account_id, cookie_plain, MASTER_KEY),
)

# 读取
cur.execute(
    "SELECT pgp_sym_decrypt(cookie_encrypted, %s)::TEXT FROM cookies "
    "WHERE tenant_id = %s AND account_id = %s",
    (MASTER_KEY, tenant_id, account_id),
)
```

`pgp_sym_decrypt` 解密失败会 raise `error: Wrong key or corrupt data` → 调用方明确知道 KEY 错。

---

## 4. 数据 Schema 详解

### 4.1 `tenants` 表（控制平面）

```sql
CREATE TABLE tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    status TEXT DEFAULT 'active'  -- active | suspended | archived
);
```

不开 RLS——超管脚本要能操作所有 tenant 行。

### 4.2 业务表通用结构

```sql
CREATE TABLE goals (
    id TEXT PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    name TEXT NOT NULL,
    config JSONB,                   -- 完整 goal 对象（与现 goals.json item 等价）
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE goals ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON goals
    USING (tenant_id = current_setting('app.tenant_id')::uuid);
```

**JSONB 而非展开列的理由**：
- goals 字段经常变（Phase 1-3 加了 keyword_library / topic_library / used_angles 等）
- JSONB 让前端契约演化无需 ALTER TABLE
- 查询性能不是瓶颈（goals 量小，单 tenant 几十行）

`collected_notes` / `generated_posts` 等高频表则用展开列 + 索引。

### 4.3 `agent_memory` 按 entry 拆行

```sql
CREATE TABLE agent_memory (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    scope TEXT NOT NULL,            -- shared | intel | content | analyst
    file TEXT NOT NULL,             -- playbook.md / methodology.md
    entry_id TEXT NOT NULL,         -- §entry-id
    body TEXT NOT NULL,             -- 单 entry 内容
    status TEXT DEFAULT 'active',   -- active | draft | rejected
    rev INT DEFAULT 1,              -- OCC 版本号
    written_by TEXT NOT NULL,       -- agent role
    written_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (tenant_id, scope, file, entry_id)
);
```

**为什么按 entry 拆行而不整存 markdown TEXT**：
- DB 主键约束保护并发写冲突，不用 `_ContentLock` 文件锁
- `status` / `rev` 字段是 SQL 字段，不用解析 markdown 提取
- LocalJsonBackend **保留**整存 markdown（人类可读、git diff 友好）；两种形态由 Backend 透明承担

### 4.4 `cookies` 加密表

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE cookies (
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    account_id TEXT NOT NULL,
    cookie_encrypted BYTEA NOT NULL,    -- pgp_sym_encrypt 输出
    valid BOOLEAN DEFAULT TRUE,
    last_validated_at TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, account_id)
);

ALTER TABLE cookies ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON cookies
    USING (tenant_id = current_setting('app.tenant_id')::uuid);
```

`cookie_encrypted` 列即使被备份/dump 出来也是 BYTEA 密文。攻击者要解密必须**同时**拿到 master key（在应用 env var 里）。

---

## 5. RLS 策略验证模型

### 5.1 RLS 怎么保证不串数据

PostgreSQL 文档：当 `ENABLE ROW LEVEL SECURITY` 后，**所有** SELECT/UPDATE/DELETE 自动追加 `WHERE <policy>`。即使 app 写错查询：

```python
cur.execute("SELECT * FROM goals")  # 没带 WHERE tenant_id
```

PG 实际执行的是：

```sql
SELECT * FROM goals
WHERE (tenant_id = current_setting('app.tenant_id')::uuid)
```

所以**只要 `app.tenant_id` 设对了，跨租户读就不可能**。

### 5.2 `app.tenant_id` 怎么设？三个时机

| 时机 | 谁设 | 命令 |
|---|---|---|
| 每个请求 | `security/middleware.py::verify_jwt` 解 JWT 后传给 router | `Depends(verify_jwt) → tenant_id` |
| 每个事务 | `db/session.py::get_rls_cursor` 在事务内 | `SET LOCAL app.tenant_id = '<tid>'` |
| 超管脚本 | `scripts/issue_token.py` 类工具 **不设**，跳出 RLS 走 superuser | 用单独的 `superuser` DB 账号 |

### 5.3 双保险：app 层 WHERE 还是要写

虽然 RLS 已经强制隔离，**业务代码每条 SQL 仍然写 `WHERE tenant_id = %s`**。为什么：

1. **防 RLS bypass bug**：万一 `SET LOCAL` 因 transaction 配置异常没生效，app 层 WHERE 兜底
2. **防 superuser 误用**：如果某天 routers 误用了 superuser conn 而非业务 conn，app 层 WHERE 仍能拦
3. **代码可读性**：写 `WHERE tenant_id = %s` 让 reviewer 一眼看到这是租户隔离查询，不需要追到 session.py 才理解
4. **微小性能收益**：PG planner 在 WHERE 命中索引时更快（虽然 RLS 也命中，但 planner 不一定能合并）

### 5.4 如何测 RLS 真的生效

```python
# tests/test_db_session.py
def test_cross_tenant_invisible(pg):
    tid_a = uuid4()
    tid_b = uuid4()
    # tenant A 插入一行
    with get_rls_cursor(str(tid_a)) as cur:
        cur.execute("INSERT INTO goals (id, tenant_id, name) VALUES ('g1', %s, 'x')", (tid_a,))
    # tenant B 完全看不见
    with get_rls_cursor(str(tid_b)) as cur:
        cur.execute("SELECT * FROM goals")
        assert cur.fetchall() == []
    # tenant A 看得见
    with get_rls_cursor(str(tid_a)) as cur:
        cur.execute("SELECT * FROM goals")
        assert len(cur.fetchall()) == 1
```

---

## 6. 同步驱动的工程考量

### 6.1 为何 psycopg2 + ThreadedConnectionPool

FastAPI 路由可以是 async，但内部调用阻塞 IO 时**必须**走 `run_in_threadpool`，否则会卡 event loop。我们的现状：

- `agents/*.py` Agent 主循环：同步（OCC + GOAP scratch_pad 串行流转）
- LLM 调用：`requests.post(...)` 同步（OpenAI SDK 兼容）
- Playwright：同步 API 用得多
- 采集脚本：requests 同步

引入 `asyncpg` 会让我们必须：

- 把所有 `agents/*.py` 改成 async（异步传染）
- 在 async 边界小心包 sync 代码（容易漏掉一处就阻塞 event loop）
- 重写所有 Tool 注册（agent_tools/*.py）
- 重写 SSE 流式输出（已经在 event loop 里跑，调 sync DB 会卡）

**`psycopg2 + run_in_threadpool` 模式**：FastAPI handler 是 async → 调用 `await run_in_threadpool(blocking_db_call)` → 在 thread 里跑 sync DB → 不卡 event loop。这是 FastAPI 文档明确推荐的同步 DB 集成方式。

### 6.2 ThreadedConnectionPool 配置

```python
init_pool(
    database_url,
    minconn=2,    # 启动时预热
    maxconn=20,   # 阿里云 RDS 2c4g 默认 max_connections=200，留余地
)
```

`run_in_threadpool` 默认 thread 数 = CPU * 5（uvicorn 默认）。所以最多有 ~40 个并发线程争 20 个 conn。压测下若发现 pool 耗尽再调 maxconn。

### 6.3 SSE 流式输出怎么办

SSE 端点（如 `POST /api/v1/collect/stream`）的 generator 是 async，**不要**在 generator 里直接调 sync DB。模式：

```python
async def stream_handler():
    async def gen():
        # 准备阶段：用 run_in_threadpool 把 DB 拿数据
        data = await run_in_threadpool(pg_backend.list_xxx, tid, since)
        # 流式输出阶段：纯计算/格式化
        for item in data:
            yield f"data: {json.dumps(item)}\n\n"
            await asyncio.sleep(0.01)
    return StreamingResponse(gen(), media_type="text/event-stream")
```

---

## 7. Cutover 真值表

| 阶段 | 数据完整性来源 | 何时安全 |
|---|---|---|
| 切流前 | LocalJsonBackend（本地文件原封不动） | 一直 |
| 停服中 | LocalJsonBackend（无写入） | 停服 ~10 min |
| 倒库期 | 源 LocalJson + 目标 PG 双备 | 倒库期 ~2 min |
| verify 中 | 源 LocalJson + 目标 PG 双备 | verify 几秒 |
| `.env` 翻 | 目标 PG（业务源）+ 本地 `.bak`（回滚源） | 永久（直到 .bak 退役） |
| 重启后 | 目标 PG（业务源）+ 本地 `.bak`（回滚源） | 永久 |
| 1 周观察期 | 同上 + 新写入只在 PG | 永久 |
| `.bak` 退役 | 目标 PG + OSS 冷存 .bak | 永久 |

**任何阶段失败的回滚动作都是同一个**：`.env` 翻回 local + 重启 + `.bak` 改名回原。**数据 0 损失**前提是切流窗口期间没新写入——这是 P4.5.2 的硬要求（停服）。

---

## 8. 风险与缓解

| 风险 | 缓解 | 检测信号 |
|---|---|---|
| RLS 实现 bug 导致跨租户读 | app 层 WHERE 双保险 + `tests/test_db_session.py` 强制覆盖 | 跨租户测试用例 fail |
| `SET LOCAL` 残留到下次 cursor | `get_rls_cursor` 用 `BEGIN/COMMIT` 包，conn 归还前事务必结束 | 测试用例：第二次 borrow conn 看 `current_setting('app.tenant_id')` 是空 |
| MASTER_KEY 丢失 → 所有 cookie 不可解 | 启动 fail fast；KEY 备份在 1Password / Bitwarden 等密码管理器 | 启动崩溃 → 立即可见 |
| MASTER_KEY 出现在日志 | `security/kms.py` 模块顶部注释 + code review 检查 | grep `MASTER_ENCRYPTION_KEY` 在日志/异常 message 里 |
| 阿里云 RDS 网络抖动 | `psycopg2` connect 重试 3 次 + circuit breaker | connection error 监控 |
| 切流后 1 周内发现致命 bug | `.bak` 文件保留 ≥ 1 月，回滚 = `.env` 翻 + 重启 | smoke test failed → 立即回滚 |
| JWT 密钥泄漏 | 短 TTL（24h）+ 重签发；密钥轮换通过 env var 切换 | audit log 出现非预期 tenant 行为 |
| 小红书反盗链 / 图链失效 | 当前业务模型下图片只用作预览，失效不阻塞核心；未来上 OSS 时再统一存 | 前端图片 broken 比例上升 |
| 双租户数据混合（开通客户后） | 端到端测试用例：用 tenant_b token 调 GET 不应看到 tenant_a 数据 | 端到端 fail |
| audit_log 写入 RLS 拦截自己 | audit_log 也按 tenant_id 隔离，写入端必须先 SET LOCAL | 测试用例覆盖 |

---

## 9. 与现有代码的兼容性

### 9.1 不破坏的接口

- `storage/base.py::StorageBackend` Protocol：保持不变
- `storage/local_json.py`：保持，作为兜底（cutover 失败回滚用）
- `server/routers/*.py` 现有 `_: str = Depends(verify_token)` 写法：保持，verify_token 内部改调 verify_jwt
- `agents/*.py` 现有 MemoryLayer 调用：保持，MemoryLayer 内部改走 `get_backend()`

### 9.2 必须破坏的（可控）

- `config/goals.json` 等本地文件：不再是源真相（cutover 后变成 `.bak`）
- `config/cookies.db`（SQLite）：cutover 后退役
- 静态 Bearer token：升级为 JWT；前端 `lib/api.ts` 必须改

### 9.3 不影响的

- `apis/xhs_*.py` 抓取逻辑
- `agent_tools/*.py` Tool 注册
- 前端除 `lib/api.ts` 外的所有页面（API 契约不变）
- Streamlit `dashboard.py`（如果还在用）

---

## 10. 不在本次范围

> 详见 `proposal.md` Out-of-Scope 段。摘要：

- OSS 对象存储（Phase 5）
- KMS 信封加密（v2）
- JWT 黑名单（短 TTL 已够）
- 租户自助注册（超管 CLI 即可）
- Schema-per-tenant（已选 RLS）
- DualWriteBackend（已否决）
- asyncpg（已否决）
- 历史 xlsx 永久存档（.bak 1 月退役即可）
- i18n
- 跨租户全局看板
- Read replica / 读写分离
