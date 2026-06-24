"""
PostgreSQL 连接池 + RLS-scoped cursor。

Phase 4a 多租户隔离的唯一入口。任何业务 SQL 都必须经过 get_rls_cursor(tenant_id),
该函数在事务里 SET LOCAL app.tenant_id + app.is_admin,RLS policy 据此过滤。

⚠️ 5 条纪律(plan 顶部):
  - SET LOCAL 必须在事务内(本函数已保证)
  - DATABASE_URL 只走 env var(本函数已保证)
  - 严禁手动 SET(无 LOCAL):跨连接复用会泄漏
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import psycopg2
from psycopg2.pool import ThreadedConnectionPool


_POOL: ThreadedConnectionPool | None = None


def init_pool(minconn: int = 2, maxconn: int = 20) -> None:
    """启动时调一次,幂等。从 DATABASE_URL 读连接串。"""
    global _POOL
    if _POOL is not None:
        return
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise EnvironmentError(
            "DATABASE_URL not set; 检查 ~/.spider_xhs/.env 是否已配置并加载"
        )
    _POOL = ThreadedConnectionPool(minconn, maxconn, dsn)


def close_pool() -> None:
    """进程退出时调用,幂等。"""
    global _POOL
    if _POOL is not None:
        _POOL.closeall()
        _POOL = None


@contextmanager
def get_rls_cursor(tenant_id: str, *, is_admin: bool = False) -> Iterator[psycopg2.extensions.cursor]:
    """
    标准入口:每次业务操作必须用本函数拿 cursor。

    内部行为:
        autocommit=False(隐式开启事务) →
        SET LOCAL app.tenant_id = <tenant_id> →
        SET LOCAL app.is_admin  = 'true'/'false' →
        yield cursor →
        COMMIT(成功)/ ROLLBACK(异常)→
        归还连接到池

    SET LOCAL 在事务结束自动失效,连接归还时不携带 session 状态。

    用法:
        with get_rls_cursor("uuid-of-tenant") as cur:
            cur.execute("SELECT * FROM goals")
            rows = cur.fetchall()
    """
    if _POOL is None:
        init_pool()
    assert _POOL is not None

    conn = _POOL.getconn()
    try:
        conn.autocommit = False
        cur = conn.cursor()
        try:
            cur.execute("SET LOCAL app.tenant_id = %s", (tenant_id,))
            cur.execute("SET LOCAL app.is_admin = %s", ("true" if is_admin else "false",))
            try:
                yield cur
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        finally:
            cur.close()
    finally:
        _POOL.putconn(conn)
