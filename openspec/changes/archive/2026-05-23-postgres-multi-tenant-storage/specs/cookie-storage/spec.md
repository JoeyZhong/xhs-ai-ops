# Spec Delta: cookie-storage

> 本 delta **MODIFIES** `cookie-storage` capability，把后端从 SQLite WAL 迁到 PostgreSQL + pgcrypto 列加密。

---

## MODIFIED Requirement: Cookie 持久化后端

系统 SHALL 把所有 Cookie 凭据存到 PostgreSQL `cookies` 表，列 `cookie_encrypted BYTEA` 用 `pgcrypto.pgp_sym_encrypt` 加密，主密钥从环境变量 `MASTER_ENCRYPTION_KEY` 读。SQLite WAL 后端在 cutover 后 MUST 退役。

### Scenario: Cookie 写入加密存储
- **WHEN** 调 `cookie_manager.save_cookie(account_id, cookie_str)`
- **THEN** 内部 SQL `INSERT ... cookie_encrypted = pgp_sym_encrypt(%s, %s)` 传明文 + MASTER_KEY
- **AND** DBA 直 SELECT cookie_encrypted 列拿到的是 BYTEA 密文，无法识别原内容

### Scenario: Cookie 读取自动解密
- **WHEN** 调 `cookie_manager.get_cookie(account_id)`
- **THEN** 内部 SQL `SELECT pgp_sym_decrypt(cookie_encrypted, %s)::TEXT` 用 MASTER_KEY 解密
- **AND** 返回明文给调用方

### Scenario: MASTER_KEY 错误时解密失败
- **WHEN** `MASTER_ENCRYPTION_KEY` 与写入时不同
- **THEN** `pgp_sym_decrypt` raise `Wrong key or corrupt data`
- **AND** `cookie_manager.get_cookie` 不吞异常，let it bubble up

### Scenario: 跨租户读取返回 None（RLS 拦截）
- **WHEN** tenant A 存了 `account_id=acc1` 的 cookie
- **AND** tenant B 调 `cookie_manager.get_cookie('acc1')`
- **THEN** 返回 None（因 RLS policy 拦住了 tenant A 的行）

---

## ADDED Requirement: Cookie 凭据列必须加密

系统 SHALL 在数据库层面用 pgcrypto 加密 Cookie 列，禁止以明文 TEXT 形式存储。

### Scenario: cookies 表结构包含加密列
- **WHEN** 创建 cookies 表
- **THEN** 列 `cookie_encrypted` 类型必须为 `BYTEA`，不是 `TEXT`
- **AND** 必须有 `CREATE EXTENSION IF NOT EXISTS pgcrypto`

### Scenario: 应用启动时验证 MASTER_ENCRYPTION_KEY 可用
- **WHEN** server 启动
- **THEN** `security/kms.py` import 时 `os.environ["MASTER_ENCRYPTION_KEY"]` 必须可读
- **AND** 缺失则 import 抛 KeyError，server fail fast

---

## ADDED Requirement: Cookie Manager 接口签名稳定

`storage/cookie_manager.py` 的 public API 签名在 SQLite → PG 迁移期间 MUST 保持完全不变，调用方零感知。

### Scenario: 调用方签名兼容
- **WHEN** 业务代码调 `cookie_manager.get_cookie(account_id) -> str | None`
- **THEN** 签名与 cutover 前完全一致
- **WHEN** 业务代码调 `cookie_manager.save_cookie(account_id, cookie) -> None`
- **THEN** 签名与 cutover 前完全一致

> **理由**：保持接口稳定让 cutover 完全是 storage 层内部改造，不影响 `apis/`、`agents/`、`agent_tools/` 任何调用方。
