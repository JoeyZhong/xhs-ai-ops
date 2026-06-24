# Spec Delta: web-api

> 本 delta **MODIFIES** `web-api` capability 的鉴权模型，把静态 Bearer token 升级到 JWT HS256。

---

## MODIFIED Requirement: API Bearer Token 鉴权

系统 SHALL 用 JWT HS256 替代静态 Bearer token，所有 `/api/v1/*` 业务端点（除 `/health`）MUST 通过 JWT 验证。

### Scenario: 缺 Authorization header
- **WHEN** 请求 `/api/v1/goals` 不带 `Authorization` header
- **THEN** 返回 401

### Scenario: JWT 签名伪造
- **WHEN** 请求带 `Authorization: Bearer <fake>` 但签名无法用 `JWT_SECRET` 验证
- **THEN** 返回 403

### Scenario: JWT 过期
- **WHEN** 请求带 `Authorization: Bearer <token>` 但 `exp` claim 已过
- **THEN** 返回 401

### Scenario: JWT 缺 sub claim
- **WHEN** 请求带合法签名但 claims 缺 `sub`
- **THEN** 返回 422

### Scenario: 合法 JWT 透传 tenant_id
- **WHEN** 请求带合法 JWT，claims `{sub: "<tid>", exp: <future>}`
- **THEN** dependency `verify_jwt` 返回 `tid`
- **AND** router 可用 `tenant_id: str = Depends(verify_jwt)` 拿到

---

## ADDED Requirement: JWT 签发 CLI

系统 SHALL 提供 `scripts/issue_token.py` CLI，超管可通过命令行为指定租户签发 JWT。

### Scenario: 为新租户签发 token
- **WHEN** 超管运行 `python scripts/issue_token.py --tenant-name shenzhen-pj`
- **THEN** 在 tenants 表插入新行（如不存在）
- **AND** stdout 输出 JWT（HS256 签名，sub = tenant_id, ttl = 24h）

### Scenario: 为已存在租户重签发
- **WHEN** 超管运行 `python scripts/issue_token.py --tenant-id <existing-uuid> --ttl 168`
- **THEN** 不新建 tenant 行，仅签发新 token，ttl=168h（7 天）

---

## ADDED Requirement: SSE 端点接受 query string token

SSE 流式端点 SHALL 支持通过 query string `?token=<jwt>` 传 token，与 header 二选一。

### Scenario: SSE 用 query string
- **WHEN** 前端 EventSource 连接 `/api/v1/collect/stream?token=<jwt>`
- **THEN** server 解析 query string 取 token 并验证
- **AND** 验证逻辑与 header 路径完全一致

> **理由**：浏览器 EventSource API 不支持自定义 header，必须走 query string。
