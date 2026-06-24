"""
Cookie Manager — 集中式 Cookie 持久化（SQLite WAL + PostgreSQL pgcrypto）。

Phase 4a §A3.7 双模式:
  - tenant_id=None 或 STORAGE_BACKEND!=postgres → SQLite 旧路径（向后兼容）
  - tenant_id 给定且 STORAGE_BACKEND=postgres → PG + pgcrypto 加密

SQLite 路径的公开函数签名改为 _sqlite_ 前缀私有；get_db_path() 保留不动。
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional


# DB 文件位置：与代码并列的 config/ 目录
_DB_PATH = Path(__file__).parent.parent / "config" / "cookies.db"

_LOCK = threading.Lock()


def _use_pg(tenant_id: Optional[str]) -> bool:
    """tenant_id 给定 AND STORAGE_BACKEND=postgres → PG；否则 SQLite legacy。"""
    if tenant_id is None:
        return False
    return os.environ.get("STORAGE_BACKEND", "local").lower() == "postgres"


# ── SQLite 私有路径（原公开函数改名 + 下划线前缀）────────────────────


@contextmanager
def _sqlite_connection():
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), timeout=5.0, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cookies (
                account_id       TEXT PRIMARY KEY,
                cookie_str       TEXT NOT NULL,
                last_update_time TEXT NOT NULL,
                note             TEXT
            )
            """
        )
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _sqlite_get_cookie(account_id: str) -> Optional[str]:
    if not account_id:
        return None
    with _LOCK, _sqlite_connection() as conn:
        row = conn.execute(
            "SELECT cookie_str FROM cookies WHERE account_id = ?",
            (account_id,),
        ).fetchone()
    return row[0] if row else None


def _sqlite_save_cookie(account_id: str, cookie_str: str, note: Optional[str]) -> None:
    if not account_id:
        raise ValueError("account_id cannot be empty")
    if not cookie_str or not cookie_str.strip():
        raise ValueError("cookie_str cannot be empty")
    now_iso = datetime.now().isoformat(timespec="seconds")
    with _LOCK, _sqlite_connection() as conn:
        conn.execute(
            """
            INSERT INTO cookies (account_id, cookie_str, last_update_time, note)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                cookie_str       = excluded.cookie_str,
                last_update_time = excluded.last_update_time,
                note             = excluded.note
            """,
            (account_id, cookie_str.strip(), now_iso, note),
        )


def _sqlite_list_accounts() -> list[dict]:
    with _LOCK, _sqlite_connection() as conn:
        rows = conn.execute(
            "SELECT account_id, last_update_time, note FROM cookies ORDER BY last_update_time DESC"
        ).fetchall()
    now = datetime.now()
    result: list[dict] = []
    for account_id, last_update, note in rows:
        try:
            t = datetime.fromisoformat(last_update)
            age_min = int((now - t).total_seconds() / 60)
        except Exception:
            age_min = -1
        result.append({
            "account_id":       account_id,
            "last_update_time": last_update,
            "age_minutes":      age_min,
            "note":             note or "",
        })
    return result


def _sqlite_delete_cookie(account_id: str) -> bool:
    if not account_id:
        return False
    with _LOCK, _sqlite_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM cookies WHERE account_id = ?", (account_id,)
        )
        return cursor.rowcount > 0


# ── PG 路径（lazy import 在函数内部）───────────────────────────────


def _pg_get_cookie(account_id: str, tenant_id: str) -> Optional[str]:
    from db.session import get_rls_cursor
    from security.kms import get_master_key

    master_key = get_master_key()
    with get_rls_cursor(tenant_id) as cur:
        cur.execute(
            "SELECT pgp_sym_decrypt(cookie_encrypted, %s)::text FROM cookies WHERE tenant_id = %s AND account_id = %s",
            (master_key, tenant_id, account_id),
        )
        row = cur.fetchone()
    return row[0] if row else None


def _pg_save_cookie(account_id: str, cookie_str: str, note: Optional[str], tenant_id: str) -> None:
    from db.session import get_rls_cursor
    from security.kms import get_master_key

    master_key = get_master_key()
    with get_rls_cursor(tenant_id) as cur:
        cur.execute(
            """
            INSERT INTO cookies(tenant_id, account_id, cookie_encrypted, last_update_time, note)
            VALUES (%s, %s, pgp_sym_encrypt(%s, %s), now(), %s)
            ON CONFLICT (tenant_id, account_id) DO UPDATE SET
                cookie_encrypted = EXCLUDED.cookie_encrypted,
                last_update_time = now(),
                note = EXCLUDED.note
            """,
            (tenant_id, account_id, cookie_str.strip(), master_key, note),
        )


def _pg_list_accounts(tenant_id: str) -> list[dict]:
    from db.session import get_rls_cursor

    with get_rls_cursor(tenant_id) as cur:
        cur.execute(
            "SELECT account_id, last_update_time, note FROM cookies WHERE tenant_id = %s ORDER BY last_update_time DESC",
            (tenant_id,),
        )
        rows = cur.fetchall()
    now = datetime.now()
    result: list[dict] = []
    for account_id, last_update, note in rows:
        try:
            t = last_update
            age_min = int((now - t).total_seconds() / 60)
        except Exception:
            age_min = -1
        result.append({
            "account_id": account_id,
            "last_update_time": str(last_update),
            "age_minutes": age_min,
            "note": note or "",
        })
    return result


def _pg_delete_cookie(account_id: str, tenant_id: str) -> bool:
    from db.session import get_rls_cursor

    with get_rls_cursor(tenant_id) as cur:
        cur.execute(
            "DELETE FROM cookies WHERE tenant_id = %s AND account_id = %s",
            (tenant_id, account_id),
        )
        return cur.rowcount > 0


# ── 公开接口（路由到 SQLite 或 PG）───────────────────────────────


def get_cookie(account_id: str = "default", *, tenant_id: Optional[str] = None) -> Optional[str]:
    if _use_pg(tenant_id):
        return _pg_get_cookie(account_id, tenant_id)
    return _sqlite_get_cookie(account_id)


def save_cookie(account_id: str = "default",
                cookie_str: str = "",
                note: Optional[str] = None,
                *,
                tenant_id: Optional[str] = None) -> None:
    if _use_pg(tenant_id):
        return _pg_save_cookie(account_id, cookie_str, note, tenant_id)
    return _sqlite_save_cookie(account_id, cookie_str, note)


def list_accounts(*, tenant_id: Optional[str] = None) -> list[dict]:
    if _use_pg(tenant_id):
        return _pg_list_accounts(tenant_id)
    return _sqlite_list_accounts()


def delete_cookie(account_id: str, *, tenant_id: Optional[str] = None) -> bool:
    if _use_pg(tenant_id):
        return _pg_delete_cookie(account_id, tenant_id)
    return _sqlite_delete_cookie(account_id)


def get_db_path() -> Path:
    """暴露 DB 文件路径（Dashboard 用于显示存储位置）。"""
    return _DB_PATH
