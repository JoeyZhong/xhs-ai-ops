"""
Pre-mock heavy dependencies before any test module imports them.

xhs_utils.xhs_util calls execjs.compile() at module level (spawns Node.js).
browser_search requires Playwright.  Both would hang or fail in the test
runner.  We register mocks in sys.modules before stream_utils is imported,
so those import-time side effects never run.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

def _mock(name: str) -> MagicMock:
    m = MagicMock(name=name)
    sys.modules.setdefault(name, m)
    return m

_mock("execjs")
_mock("xhs_utils")
_mock("xhs_utils.xhs_util")
_mock("xhs_utils.cookie_util")
_mock("xhs_utils.common_util")
_mock("xhs_utils.data_util")
_mock("apis")
_mock("apis.xhs_pc_apis")
_mock("browser_search")


# ── Phase 4a · load .env before any PG fixture ──────────────────────────
import os
from pathlib import Path

_env_path = Path.home() / ".spider_xhs" / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

os.environ.setdefault("JWT_SECRET", "test-jwt-secret-for-pytest-only")
os.environ.setdefault("JWT_ALGORITHM", "HS256")


# ── Phase 4a · PgBackend fixtures ──────────────────────────────────────

import uuid

import psycopg2
import pytest


@pytest.fixture
def pg_admin_dsn():
    """Admin DSN,用于创建/清理测试 tenant。DATABASE_URL_ADMIN 缺失或 PG 不可达 → skip。"""
    import os
    dsn = os.environ.get("DATABASE_URL_ADMIN")
    if not dsn:
        pytest.skip("DATABASE_URL_ADMIN not set; skipping PG tests")
    # DSN 配了但本机 PG 没起 → skip 而非 error（与无 DSN 行为一致）
    try:
        psycopg2.connect(dsn).close()
    except psycopg2.OperationalError as exc:
        pytest.skip(f"PG unreachable ({dsn!r}): {exc}")
    return dsn


@pytest.fixture
def pg_tenant(pg_admin_dsn):
    """建一个测试 tenant,yield tenant_id (str UUID),teardown 删 cascade。"""
    name = f"test_{uuid.uuid4().hex[:8]}"
    conn = psycopg2.connect(pg_admin_dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tenants(name, is_admin) VALUES (%s, false) RETURNING tenant_id",
                (name,)
            )
            tid = str(cur.fetchone()[0])
        yield tid
        with conn.cursor() as cur:
            cur.execute("DELETE FROM tenants WHERE tenant_id = %s", (tid,))
    finally:
        conn.close()


@pytest.fixture
def two_pg_tenants(pg_admin_dsn):
    """yield (tid_a, tid_b) 两个 tenant,用于跨租户隔离测试。"""
    tids = []
    conn = psycopg2.connect(pg_admin_dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            for label in ("a", "b"):
                cur.execute(
                    "INSERT INTO tenants(name, is_admin) VALUES (%s, false) RETURNING tenant_id",
                    (f"test_{label}_{uuid.uuid4().hex[:8]}",)
                )
                tids.append(str(cur.fetchone()[0]))
        yield tuple(tids)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM tenants WHERE tenant_id = ANY(%s::uuid[])", (tids,))
    finally:
        conn.close()


@pytest.fixture
def pg_backend(pg_admin_dsn):
    """Yield PgBackend instance, reset factory after test."""
    from storage.factory import reset_backend
    reset_backend()
    import os
    os.environ["STORAGE_BACKEND"] = "postgres"
    from storage.pg_backend import PgBackend
    yield PgBackend()
    reset_backend()
