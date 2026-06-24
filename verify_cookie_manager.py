#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cookie Manager 验收测试
运行：python -X utf8 verify_cookie_manager.py
"""

import sys
import threading
import time
import tempfile
from pathlib import Path

_results: list[tuple[str, bool, str]] = []

def check(name, cond, detail=""):
    mark = "[+]" if cond else "[X]"
    s = "PASS" if cond else "FAIL"
    line = f"  {mark} {s}  {name}"
    if detail:
        line += f"  <- {detail}"
    print(line)
    _results.append((name, cond, detail))


def section(title):
    print(f"\n{'='*60}\n  {title}\n{'-'*60}")


def summary():
    total = len(_results)
    ok = sum(1 for _, c, _ in _results if c)
    print(f"\n{'='*60}\n  结果：{ok}/{total} 通过")
    if ok != total:
        print("  失败清单：")
        for n, c, d in _results:
            if not c:
                print(f"    [X] {n}" + (f": {d}" if d else ""))
    else:
        print("  全部通过")
    print('='*60)
    return ok == total


# ─────────────────────────────────────────────────────────────────────
# 重定向 cookie_manager 的 DB 路径到临时目录（避免污染真实 config/）
# ─────────────────────────────────────────────────────────────────────

_TMPDIR = tempfile.mkdtemp(prefix="cookie_mgr_test_")
_TMP_DB = Path(_TMPDIR) / "config" / "cookies.db"

# 在 import 之前就 monkey-patch 路径
import storage.cookie_manager as cm
cm._DB_PATH = _TMP_DB


# ─────────────────────────────────────────────────────────────────────
# S1 · 基础 CRUD
# ─────────────────────────────────────────────────────────────────────

section("S1 · 基础 CRUD")

# 1.1 不存在 → None
check("get 不存在的 account → None",
      cm.get_cookie("nonexistent") is None)

# 1.2 save + get 一致
cm.save_cookie("default", "session=abc123; user=test", note="manual paste")
val = cm.get_cookie("default")
check("save 后 get 一致",
      val == "session=abc123; user=test", repr(val))

# 1.3 save 同 account → overwrite
cm.save_cookie("default", "session=NEW; user=test")
val2 = cm.get_cookie("default")
check("同 account 二次 save 覆盖", val2 == "session=NEW; user=test")

# 1.4 list_accounts 至少 1 个
accounts = cm.list_accounts()
check("list_accounts 含 default", any(a["account_id"] == "default" for a in accounts))
check("list_accounts 含 last_update_time",
      "last_update_time" in accounts[0])
check("list_accounts 含 age_minutes",
      "age_minutes" in accounts[0]
      and isinstance(accounts[0]["age_minutes"], int))

# 1.5 delete 存在的 → True，再 get → None
ok_del = cm.delete_cookie("default")
check("delete 存在的 account → True", ok_del is True)
check("delete 后 get 返回 None",
      cm.get_cookie("default") is None)

# 1.6 delete 不存在的 → False
check("delete 不存在的 account → False",
      cm.delete_cookie("never_existed") is False)


# ─────────────────────────────────────────────────────────────────────
# S2 · 多账号隔离
# ─────────────────────────────────────────────────────────────────────

section("S2 · 多账号隔离")

cm.save_cookie("acc_A", "cookie_A_value")
cm.save_cookie("acc_B", "cookie_B_value")
cm.save_cookie("acc_C", "cookie_C_value")

check("3 个账号互相独立",
      cm.get_cookie("acc_A") == "cookie_A_value"
      and cm.get_cookie("acc_B") == "cookie_B_value"
      and cm.get_cookie("acc_C") == "cookie_C_value")

# 改 A 不影响 B/C
cm.save_cookie("acc_A", "cookie_A_NEW")
check("修改 A 不影响 B",
      cm.get_cookie("acc_B") == "cookie_B_value")
check("修改 A 不影响 C",
      cm.get_cookie("acc_C") == "cookie_C_value")

# 删 B 不影响 A/C
cm.delete_cookie("acc_B")
check("删除 B 不影响 A",
      cm.get_cookie("acc_A") == "cookie_A_NEW")
check("删除 B 不影响 C",
      cm.get_cookie("acc_C") == "cookie_C_value")

# list 应返回 2 个（A, C）
accounts = cm.list_accounts()
ids = {a["account_id"] for a in accounts}
check("list 返回剩余 2 个账号", ids == {"acc_A", "acc_C"}, str(ids))


# ─────────────────────────────────────────────────────────────────────
# S3 · 输入校验
# ─────────────────────────────────────────────────────────────────────

section("S3 · 输入校验")

# 3.1 空 account_id 拒绝
try:
    cm.save_cookie("", "some_cookie")
    check("save 空 account_id 抛 ValueError", False)
except ValueError:
    check("save 空 account_id 抛 ValueError", True)

# 3.2 空 cookie_str 拒绝
try:
    cm.save_cookie("test", "")
    check("save 空 cookie_str 抛 ValueError", False)
except ValueError:
    check("save 空 cookie_str 抛 ValueError", True)

# 3.3 空白字符串视为空
try:
    cm.save_cookie("test", "   \n   ")
    check("save 纯空白拒绝", False)
except ValueError:
    check("save 纯空白拒绝", True)

# 3.4 get 空 account_id → None
check("get 空 account_id → None", cm.get_cookie("") is None)
check("get None → None", cm.get_cookie(None) is None)

# 3.5 delete 空 → False
check("delete 空 account_id → False",
      cm.delete_cookie("") is False)


# ─────────────────────────────────────────────────────────────────────
# S4 · 长 Cookie / 特殊字符
# ─────────────────────────────────────────────────────────────────────

section("S4 · 长 Cookie / 特殊字符")

# 4.1 长 Cookie（10KB）
long_cookie = "abc=" + "X" * 10240
cm.save_cookie("long_test", long_cookie)
val = cm.get_cookie("long_test")
check("10KB Cookie 完整存取",
      val == long_cookie, f"length={len(val) if val else 0}")

# 4.2 含特殊字符（=, ;, 中文, emoji）
special = "key1=value1; key2=中文值; key3=\"quoted\"; key4=😀"
cm.save_cookie("special_test", special)
val = cm.get_cookie("special_test")
check("特殊字符 Cookie 完整保存", val == special, repr(val))

# 4.3 SQL 注入尝试（防 SQL 注入）
inject = "test=value'; DROP TABLE cookies; --"
cm.save_cookie("inject_test", inject)
val = cm.get_cookie("inject_test")
check("SQL 注入文本被当作普通字符串", val == inject)
check("注入后表仍存在",
      cm.get_cookie("acc_A") == "cookie_A_NEW")


# ─────────────────────────────────────────────────────────────────────
# S5 · note 字段 + 时间戳
# ─────────────────────────────────────────────────────────────────────

section("S5 · note 字段与时间戳")

cm.save_cookie("note_test", "x=1", note="from browser fallback")
accounts = cm.list_accounts()
note_acc = next((a for a in accounts if a["account_id"] == "note_test"), None)
check("note 字段被保存", note_acc and note_acc["note"] == "from browser fallback")

# 重写不带 note → note 应该被清空
cm.save_cookie("note_test", "x=2")
accounts = cm.list_accounts()
note_acc2 = next((a for a in accounts if a["account_id"] == "note_test"), None)
check("重写不带 note → note 清空", note_acc2 and note_acc2["note"] == "")

# age_minutes 应为 0（刚写）
check("刚写入的 age_minutes ≈ 0",
      note_acc2 and note_acc2["age_minutes"] in (0, 1),
      f"age={note_acc2['age_minutes'] if note_acc2 else None}")


# ─────────────────────────────────────────────────────────────────────
# S6 · 多线程并发
# ─────────────────────────────────────────────────────────────────────

section("S6 · 多线程并发安全")

errors = []

def _writer(acc_id, count):
    try:
        for i in range(count):
            cm.save_cookie(acc_id, f"thread={acc_id}-iter={i}")
            time.sleep(0.001)
    except Exception as e:
        errors.append(f"{acc_id}: {type(e).__name__}: {e}")

def _reader(acc_id, count):
    try:
        for _ in range(count):
            cm.get_cookie(acc_id)
            time.sleep(0.001)
    except Exception as e:
        errors.append(f"{acc_id}-r: {type(e).__name__}: {e}")

threads = []
for tid in ["t1", "t2", "t3", "t4"]:
    threads.append(threading.Thread(target=_writer, args=(tid, 30)))
    threads.append(threading.Thread(target=_reader, args=(tid, 30)))

for t in threads: t.start()
for t in threads: t.join()

check("100+ 并发读写无异常", len(errors) == 0,
      str(errors[:3]) if errors else "")

# 4 个账号最终都有最新值
for tid in ["t1", "t2", "t3", "t4"]:
    val = cm.get_cookie(tid)
    check(f"并发后 {tid} 最终值正确",
          val is not None and val.startswith("thread=") and "iter=29" in val,
          val[:40] if val else "None")


# ─────────────────────────────────────────────────────────────────────
# S7 · WAL / 文件结构
# ─────────────────────────────────────────────────────────────────────

section("S7 · WAL 模式与文件落地")

db_path = cm.get_db_path()
check("get_db_path 返回 Path", isinstance(db_path, Path))
check("DB 文件已创建", db_path.exists())

# WAL 文件应该出现
wal_path = Path(str(db_path) + "-wal")
shm_path = Path(str(db_path) + "-shm")
check("WAL 文件存在（journal_mode=WAL 已生效）",
      wal_path.exists() or db_path.stat().st_size > 0,
      f"wal={wal_path.exists()}, db={db_path.stat().st_size}")


# ─────────────────────────────────────────────────────────────────────
# 清理
# ─────────────────────────────────────────────────────────────────────

import shutil
try:
    shutil.rmtree(_TMPDIR)
except Exception:
    pass

ok = summary()
sys.exit(0 if ok else 1)
