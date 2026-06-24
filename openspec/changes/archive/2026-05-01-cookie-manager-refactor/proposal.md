# Proposal: Cookie 集中管理（cookie-manager-refactor）

## Why

当前 Cookie 管理存在 3 个严重技术债（架构文档第 8 章 B 已点出）：

1. **明文硬编码**：`run_search.py` / `hot_trend_monitor.py` / `xhs_collector.py` 顶部
   `COOKIES_STR = "..."` 写死，git 历史会持续积累 Cookie 泄漏
2. **自我修改源码**：`browser_search.py::update_script_cookies()` 用 regex 重写 Python 文件，
   本质是 self-modifying code，污染 git diff，且与未来 reload / hot-swap 机制天然冲突
3. **单 Cookie 服务全平台**：无账号隔离，多账号场景死锁

向 FastAPI 异步演进时，这套机制会立即崩溃：
- 多进程并发改写同一源文件 → race condition
- `agents/context.py` 已支持多账号 persona，但 Cookie 层是单点，业务-技术映射断裂
- 多租户场景必需账号级 Cookie 隔离

## What

引入 `storage/cookie_manager.py`，SQLite WAL 模式，按 `account_id` 隔离存储。
所有读取 / 写入 Cookie 的代码路径全部走这个统一接口。**彻底删除源码改写逻辑**。

### 多账号链路完整闭合

```
config/personas.json[id="puji_paidang"]
   ↑ 1:N
config/goals.json[persona_id="puji_paidang"]
   ↓ active goal selects
ACCOUNT_ID = "puji_paidang"
   ↓
config/cookies.db [account_id="puji_paidang"]
   ↓
HTTP request to xiaohongshu.com
```

### 回退链（仅两级）

```
1. cookie_manager.get_cookie(account_id)   ← 主路径
2. os.environ.get("COOKIES")                ← 容器 / CI 场景
3. ❌ 不再有第 3 级（硬编码 COOKIES_STR 全部删除）
```

### 找不到 Cookie 时

`sys.exit(1)` + 友好提示「请进入 Dashboard ⚙️ API配置 添加 account_id=X 的 Cookie」，
绝不静默失败。

## Impact

### 新增能力
- `cookie-storage` capability（多账号 + 持久化 + 时间戳 + 并发安全）

### 新增文件
- `storage/cookie_manager.py` — SQLite WAL 持久化（约 150 行）
- `verify_cookie_manager.py` — 测试脚本（约 25 cases）
- `openspec/changes/cookie-manager-refactor/specs/cookie-storage/spec.md`

### 修改文件（6 个）
- `run_search.py` — 启动时读 cookie_manager
- `hot_trend_monitor.py` — 启动时读 cookie_manager
- `xhs_collector.py` — 启动时读 cookie_manager
- `browser_search.py` — 删 update_script_cookies，新 Cookie 写 cookie_manager
- `agent_tools/search.py::_load_cookies_from_script()` — 删除整个函数，改用 cookie_manager
- `dashboard.py` — Cookie 更新 UI 改造（按账号选择 + 状态展示）

### 删除代码（约 50-80 行）
- `browser_search.py::update_script_cookies()` 整个函数
- 三个 CLI 脚本顶部的 `COOKIES_STR = "..."` 硬编码
- `agent_tools/search.py::_load_cookies_from_script()` 函数
- `dashboard.py` 里 regex 重写 Python 文件的逻辑（保留输入框 + 保存按钮）

### 不影响
- Cookie 解析逻辑（`xhs_utils/cookie_util.py`）
- 签名算法、采集业务逻辑
- 现有 `personas.json` / `goals.json` 结构（只是开始被 cookie_manager 引用）
- 验收测试 Phase 1+2+3（仍 245 cases 全通过）

## Risk

| 风险 | 缓解 |
|------|------|
| 改完后用户首次跑 CLI 报「找不到 Cookie」 | 提案已说明：用户上 Dashboard 粘一次（5 秒）即可，sys.exit 时给清晰提示 |
| SQLite 多进程并发损坏 | WAL 模式 + busy_timeout=5000，stdlib `sqlite3` 工业级验证 |
| 多账号 Cookie 串了（A 账号被覆盖到 B） | account_id PRIMARY KEY 约束 + Dashboard 选择器双重保险 |
| 浏览器兜底回写失败 → Cookie 永远旧 | 回写失败抛 audit 日志 + Dashboard 「Cookie 已更新 X 分钟前」可视化 |
| FastAPI 异步场景下 stdlib `sqlite3` 阻塞 | 接口设计预留：本提案先用同步 sqlite3，未来仅替换为 `aiosqlite`（1 处改动） |
| Cookie 数据丢失（误删 cookies.db） | 加入 `.gitignore` 防误提交；用户能从 Dashboard 5 秒重粘 |

## 设计决策记录（DDR）

- **SQLite over JSON**：JSON 多写并发会 lost-update；SQLite WAL 工业级
- **`config/cookies.db` 而非 `xhs_data/cookies.db`**：xhs_data 有 7 天清理逻辑，凭证不该被清
- **`account_id == persona.id`**：业务-技术映射 1:1，DDD 友好
- **不写自动迁移**：手动粘 5 秒 vs 写迁移脚本 30 分钟，明显划不来；纯粹拔草除根
- **回退仅 2 级**：彻底删硬编码，避免兼容期变成永久依赖
- **找不到 Cookie 立即 exit**：宁可早死也不静默失败，避免上线后 debug 噩梦

## 不在范围（明确拒绝）

- ❌ Cookie 加密存储（首版透明文 + 文件系统权限保护，加密留待 Phase 4 多租户云上）
- ❌ Cookie 自动刷新策略（保持现状：浏览器兜底成功 → 写入；用户手工更新 → 写入）
- ❌ aiosqlite 异步实现（接口预留，FastAPI 真正落地时再切）
- ❌ Cookie 有效期主动检测（采集失败 → 浏览器兜底 → 写入新 Cookie，已是闭环）

## 实施顺序提示

按 `tasks.md` 的章节顺序：
1. 先建 `cookie_manager.py` + 写测试（基础设施）
2. 再改 CLI 脚本（让 cookie 读路径切换）
3. 再改 browser_search 写路径
4. 再改 agent_tools 适配
5. 最后改 dashboard UI（用户感知层）
6. 跑全量验收（Phase 1+2+3 + 新 verify_cookie_manager）

每步独立可验证，避免一次性大改后定位问题困难。
