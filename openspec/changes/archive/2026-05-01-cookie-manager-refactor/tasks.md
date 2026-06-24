# Tasks: Cookie Manager Refactor

按章节顺序执行，每节完成后建议验证再进下一节。

---

## 1 · 核心基础设施（建立 cookie_manager）

- [ ] 1.1 新建 `storage/cookie_manager.py`，SQLite WAL 模式 + busy_timeout
- [ ] 1.2 schema：
  ```sql
  CREATE TABLE IF NOT EXISTS cookies (
      account_id       TEXT PRIMARY KEY,
      cookie_str       TEXT NOT NULL,
      last_update_time TEXT NOT NULL,    -- ISO8601
      note             TEXT
  );
  ```
- [ ] 1.3 接口：
  - `get_cookie(account_id="default") -> Optional[str]`
  - `save_cookie(account_id, cookie_str, note=None) -> None`
  - `list_accounts() -> list[dict]`（account_id, last_update_time, age_minutes, note）
  - `delete_cookie(account_id) -> bool`
  - `get_db_path() -> Path`（暴露给 Dashboard 显示）
- [ ] 1.4 内部线程锁防 SQLite 多线程冲突（stdlib sqlite3 默认禁止跨线程共享 connection）
- [ ] 1.5 数据库文件位置：`config/cookies.db`，目录不存在时自动 mkdir
- [ ] 1.6 加入 `.gitignore`：`config/cookies.db`、`config/cookies.db-wal`、`config/cookies.db-shm`
- [ ] 1.7 新建 `verify_cookie_manager.py`：
  - 基础 CRUD（save / get / delete / list）
  - 多账号隔离（A 写不影响 B）
  - 不存在的 account_id 返回 None
  - 长 Cookie（10KB+）正常存取
  - 并发写入（多线程模拟，最终状态正确）
  - WAL 文件确实生成
  - delete 不存在的 account_id 返回 False

---

## 2 · CLI 脚本改造（读取路径切换）

- [ ] 2.1 `run_search.py`:
  - 删除顶部 `COOKIES_STR = "..."` 硬编码
  - 加 `ACCOUNT_ID = os.environ.get("XHS_ACCOUNT_ID", "default")`
  - `COOKIES_STR = cookie_manager.get_cookie(ACCOUNT_ID) or os.environ.get("COOKIES", "")`
  - 找不到时 `print` 友好提示 + `sys.exit(1)`
- [ ] 2.2 `hot_trend_monitor.py`：同上改造
- [ ] 2.3 `xhs_collector.py`：同上改造
- [ ] 2.4 改完后语法检查（ast.parse）+ 单脚本独立运行不崩溃（不调真 API，至少 import 阶段通过）

---

## 3 · 浏览器兜底重写（写入路径）

- [ ] 3.1 删除 `browser_search.py::update_script_cookies()` 整个函数
- [ ] 3.2 `search_notes()` / `get_keyword_suggestions()` 增加 `account_id="default"` 参数
- [ ] 3.3 浏览器拿到新 Cookie 后调用 `cookie_manager.save_cookie(account_id, new_ck, note="from browser fallback")`
- [ ] 3.4 调用方（run_search.py / hot_trend_monitor.py）传入 `ACCOUNT_ID` 给浏览器函数
- [ ] 3.5 grep 整个仓库确认无残留 `update_script_cookies` 引用

---

## 4 · agent_tools 适配

- [ ] 4.1 删除 `agent_tools/search.py::_load_cookies_from_script()` 函数
- [ ] 4.2 `_collect_notes_handler` 改读 `cookie_manager.get_cookie(account_id)`
  （account_id 从 `args["account_id"]` 优先；其次 `ctx.tenant_id`；最后 "default"）
- [ ] 4.3 同步检查 `agent_tools/hot_monitor.py` 等 Tool 是否也有类似逻辑，一并改
- [ ] 4.4 schema 增加 `account_id` 可选字段（不传则走默认）

---

## 5 · Dashboard 改造（用户感知层）

- [ ] 5.1 ⚙️ API 配置页：
  - 删除「更新 Cookie 到脚本」按钮的 regex 重写逻辑
  - 改为「按账号选择 → 粘贴 Cookie → 保存到 cookie_manager」
  - account 选择器读取 `personas.json` 的 ids
- [ ] 5.2 显示每个账号的 Cookie 状态：
  - 「✅ Cookie 有效，更新于 X 分钟前」
  - 「❌ 该账号还没设置 Cookie」
  - 「⚠️ Cookie 已 X 小时前更新，可能即将失效」（>1h）
- [ ] 5.3 ② 市场洞察 Tab：
  - 采集前从 cookie_manager 检查当前账号 Cookie 是否存在
  - 没有时阻断「立即采集」按钮 + 提示跳转 ⚙️ API 配置
- [ ] 5.4 侧边栏 Cookie 状态指示器：
  - 当前 active goal 关联的 persona_id → 检查对应账号 Cookie 状态
  - 不再读取 `run_search.py` 文件 mtime（旧逻辑过时了）

---

## 6 · 测试与验证

- [ ] 6.1 跑 `verify_cookie_manager.py` 全部通过
- [ ] 6.2 跑 `verify_phase1_2.py` 确保 150/150 不回归
- [ ] 6.3 跑 `verify_phase3.py` 确保 95/95 不回归
- [ ] 6.4 grep 全仓库：
  - `update_script_cookies` 应 0 引用
  - `_load_cookies_from_script` 应 0 引用
  - `COOKIES_STR\s*=\s*"abRequestId` 之类的硬编码 0 出现
- [ ] 6.5 手工 e2e（用户验证）：
  - Dashboard 进 ⚙️ API 配置 → 选 puji_paidang → 粘贴 Cookie → 保存
  - 进 ② 市场洞察 → 立即采集 → 成功
  - 模拟 Cookie 失效场景 → 浏览器兜底 → 自动写入 cookie_manager → 下次采集成功

---

## 7 · 收尾与归档

- [ ] 7.1 spec 合入 `openspec/specs/cookie-storage/spec.md`（apply ADDED 条款）
- [ ] 7.2 整个 change 目录移到 `openspec/changes/archive/2026-05-XX-cookie-manager-refactor/`
- [ ] 7.3 更新 `docs/ARCHITECTURE.md` 第 8 章 B（标记已修复，移到「已解决架构债」附录）
- [ ] 7.4 更新 `docs/USER_GUIDE.md` ⚙️ API 配置部分：
  - Cookie 管理改为「按账号选择 → 粘贴 → 保存」
  - 移除「自动同步到 run_search.py」的描述
- [ ] 7.5 更新 `CLAUDE.md` 目录结构：
  - 加 `storage/cookie_manager.py`
  - 加 `config/cookies.db`（标注 gitignored）
- [ ] 7.6 git 检查：确认 `cookies.db` 未被提交（应在 .gitignore 中）
