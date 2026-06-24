# Spec: cookie-storage

> 基线 capability。Phase 4a 更新：后端从 SQLite WAL 迁到 PostgreSQL + pgcrypto 列加密。

---

## ADDED Requirement: 集中式 Cookie 持久化

系统 SHALL 把所有账号的小红书 Cookie 集中存储于 `config/cookies.db`（SQLite WAL 模式，**仅 `STORAGE_BACKEND=local` 时**）。

> **Phase 4a 更新**：`STORAGE_BACKEND=postgres` 时，Cookie 存储后端为 PG `cookies` 表 + `pgcrypto.pgp_sym_encrypt` 列加密。见下方「Phase 4a: PG + pgcrypto 加密」requirement。

**任何代码路径都不允许**再以 Python 源文件为存储介质（含 regex 改写）。

### Scenario: Dashboard 提交新 Cookie 持久化
- **WHEN** 用户在 Dashboard 选定 account_id，粘贴 Cookie 后点击保存
- **THEN** `cookie_manager.save_cookie(account_id, cookie_str)` 被调用
- **AND** `STORAGE_BACKEND=local` → 写入 SQLite `cookies` 表
- **AND** `STORAGE_BACKEND=postgres` → 写入 PG `cookies` 表（pgp_sym_encrypt 加密）
- **AND** `last_update_time` 更新为当前 ISO8601 时间
- **AND** Dashboard 立即显示「刚刚更新」

### Scenario: 浏览器兜底自动持久化
- **GIVEN** API 调用失败，Playwright 兜底成功捕获新 Cookie
- **WHEN** browser_search 拿到新 Cookie
- **THEN** 调用 `cookie_manager.save_cookie(account_id, new_cookie, note="from browser fallback")`
- **AND** 不调用任何修改源码的方法
- **AND** 同一进程内下次 `get_cookie(account_id)` 立刻拿到新值

---

## ADDED Requirement: 多账号隔离

系统 SHALL 按 `account_id` 隔离 Cookie，互不串扰。`account_id` 取值与
`config/personas.json[].id` 一致，形成业务-技术映射。

### Scenario: 多账号读写不串扰
- **GIVEN** account_id="A" 和 account_id="B" 各自存了 Cookie
- **WHEN** `save_cookie("A", new_value)` 写入
- **THEN** account_id="B" 的 cookie_str 和 last_update_time 不变

### Scenario: 默认 account_id 回退
- **WHEN** 调用方未指定 account_id
- **THEN** 使用 "default"
- **AND** 不影响其他显式 account_id 的数据

### Scenario: account_id 与 persona.id 联动
- **GIVEN** active goal 的 persona_id = "puji_paidang"
- **WHEN** Dashboard 触发采集
- **THEN** CLI 子进程通过 `XHS_ACCOUNT_ID` 环境变量收到 "puji_paidang"
- **AND** 自动从 cookie_manager 取出 puji_paidang 账号的 Cookie

---

## ADDED Requirement: 回退链与启动校验

CLI 脚本和 Tool 在读取 Cookie 时 SHALL 严格按以下顺序，**不允许第 3 级**：

1. `cookie_manager.get_cookie(account_id)` — 主路径
2. `os.environ.get("COOKIES")` — 容器 / CI 场景
3. ❌ 不允许（删除所有源文件硬编码 `COOKIES_STR = "..."`）

### Scenario: 找不到 Cookie 时优雅失败
- **WHEN** 两级回退都没拿到 Cookie
- **THEN** 脚本 print 提示「请进入 Dashboard ⚙️ API 配置 添加 account_id=X 的 Cookie」
- **AND** `sys.exit(1)`，禁止静默失败导致后续业务异常

### Scenario: 源码硬编码已被彻底删除
- **WHEN** 在仓库根目录 grep `COOKIES_STR\s*=\s*"abRequestId`（典型硬编码）
- **THEN** 0 个匹配（除归档目录与本 spec 外）
- **AND** 所有 CLI 脚本顶部不再含明文 Cookie

---

## ADDED Requirement: 并发安全

`cookie_manager` SHALL 在多进程 / 多线程并发读写时不丢失数据，不损坏文件。

### Scenario: 多 CLI 进程同时写
- **GIVEN** 两个 run_search.py 进程同时浏览器兜底成功
- **WHEN** 各自 `save_cookie("default", ...)` 几乎同时调用
- **THEN** 后写覆盖前写（last-write-wins）
- **AND** SQLite WAL 模式 + busy_timeout 保证不损坏文件
- **AND** `last_update_time` 反映最新写入

### Scenario: 多线程并发
- **GIVEN** Streamlit Dashboard + 多个后台采集线程
- **WHEN** 同时调用 `get_cookie` / `save_cookie`
- **THEN** 不抛 `sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that same thread`
- **AND** 通过 cookie_manager 内部线程锁或 per-call connection 解决

---

## ADDED Requirement: 删除自我修改源码逻辑

系统 SHALL 不再以任何 regex 改写 Python 源文件的方式持久化运行时数据。

### Scenario: update_script_cookies 已根除
- **WHEN** grep 仓库 `update_script_cookies`
- **THEN** 0 个匹配（除归档/历史 commit message 外）

### Scenario: _load_cookies_from_script 已根除
- **WHEN** grep 仓库 `_load_cookies_from_script`
- **THEN** 0 个匹配

### Scenario: dashboard 不再 regex 改写脚本
- **WHEN** grep `dashboard.py` 中 `re.sub.*COOKIES_STR`
- **THEN** 0 个匹配

---

## ADDED Requirement: 状态可观测

Dashboard SHALL 展示每个 account_id 的 Cookie 健康状态，让用户知道是否需要更新。

### Scenario: Cookie 状态可视化
- **GIVEN** account_id="puji_paidang" 的 last_update_time = 12 分钟前
- **WHEN** 用户进入 ⚙️ API 配置页
- **THEN** 看到「✅ Cookie 有效，更新于 12 分钟前」

### Scenario: Cookie 缺失提示
- **GIVEN** account_id="another_brand" 在 cookies.db 中不存在
- **WHEN** 用户切换到该账号关联的 goal
- **THEN** 侧边栏显示「❌ 当前账号未设置 Cookie」
- **AND** 「立即采集」按钮被阻断

### Scenario: Cookie 即将失效预警
- **GIVEN** Cookie 上次更新已 > 1 小时
- **WHEN** 用户进入采集页
- **THEN** 显示警告「⚠️ Cookie 已 X 小时未更新，可能即将失效，建议更新」

---

## MODIFIED Requirement: 多账号隔离 → 多租户隔离（Phase 4a 更新）

系统 SHALL 按 `account_id` 隔离 Cookie（`STORAGE_BACKEND=local` 时），按 `tenant_id + account_id` 复合隔离（`STORAGE_BACKEND=postgres` 时）。`account_id` 取值与 `config/personas.json[].id` 一致。

> **Phase 4a 更新**：PG 模式下隔离由 RLS `tenant_isolation` policy 在数据库层强制，跨租户读取返回 None。

---

## ADDED Requirement: PG + pgcrypto 列加密（Phase 4a）

`STORAGE_BACKEND=postgres` 时，系统 SHALL 把 Cookie 凭据存到 PG `cookies` 表，列 `cookie_encrypted BYTEA` 用 `pgcrypto.pgp_sym_encrypt` 加密，主密钥从环境变量 `MASTER_ENCRYPTION_KEY` 读取。

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

### Scenario: 应用启动时验证 MASTER_ENCRYPTION_KEY 可用
- **WHEN** server 启动
- **THEN** `security/kms.py` import 时 `os.environ["MASTER_ENCRYPTION_KEY"]` 必须可读
- **AND** 缺失则 import 抛 KeyError，server fail fast

---

## ADDED Requirement: Cookie Manager 接口签名稳定（Phase 4a）

`storage/cookie_manager.py` 的 public API 签名在 SQLite → PG 迁移期间 MUST 保持完全不变，调用方零感知。

### Scenario: 调用方签名兼容
- **WHEN** 业务代码调 `cookie_manager.get_cookie(account_id) -> str | None`
- **THEN** 签名与 cutover 前完全一致
- **WHEN** 业务代码调 `cookie_manager.save_cookie(account_id, cookie) -> None`
- **THEN** 签名与 cutover 前完全一致

> **理由**：保持接口稳定让 cutover 完全是 storage 层内部改造，不影响 `apis/`、`agents/`、`agent_tools/` 任何调用方。
