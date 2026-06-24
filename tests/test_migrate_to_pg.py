"""Integration tests for scripts/migrate_to_pg.py — P4.4.2.1."""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
FIXTURE = Path(__file__).parent / "fixtures" / "migrate_source"


# ── helpers ──────────────────────────────────────────────────────────────


def _make_xlsx(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_excel(path, index=False)


def _make_cookie_db(path: Path, account_id: str, cookie_str: str, note: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS cookies (
            account_id TEXT PRIMARY KEY,
            cookie_str TEXT NOT NULL,
            last_update_time TEXT NOT NULL,
            note TEXT
        )"""
    )
    conn.execute(
        "INSERT OR REPLACE INTO cookies(account_id, cookie_str, last_update_time, note) VALUES (?,?,?,?)",
        (account_id, cookie_str, datetime.now().isoformat(), note),
    )
    conn.commit()
    conn.close()


def _run_migrate(tenant_id: str, **kwargs) -> int:
    """Run migrate_to_pg.cmd_migrate with given args, return exit code (0 or 1)."""
    from scripts.migrate_to_pg import cmd_migrate

    defaults = {
        "tenant_id": tenant_id,
        "source_root": str(FIXTURE),
        "dry_run": False,
        "verify": False,
        "skip_tables": "",
    }
    defaults.update(kwargs)
    ns = argparse.Namespace(**defaults)
    try:
        cmd_migrate(ns)
        return 0
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        return code


# ── fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def migrate_fixtures():
    """Create xlsx + sqlite fixture files under tests/fixtures/migrate_source/."""
    # collected_notes xlsx (3 rows)
    _make_xlsx(FIXTURE / "xhs_data" / "spider_xhs_test_001.xlsx", [
        {"note_id": "n1", "goal_id": "goal_mig_test", "keyword": "test_kw1",
         "title": "Test Note 1", "author": "author1", "likes": 10,
         "comments_count": 2, "shares": 1, "collects": 3, "ces_score": 25.0},
        {"note_id": "n2", "goal_id": "goal_mig_test", "keyword": "test_kw2",
         "title": "Test Note 2", "author": "author2", "likes": 20,
         "comments_count": 5, "shares": 2, "collects": 8, "ces_score": 60.0},
        {"note_id": "n3", "goal_id": "goal_mig_test", "keyword": "test_kw3",
         "title": "Test Note 3", "author": "author3", "likes": 5,
         "comments_count": 0, "shares": 0, "collects": 1, "ces_score": 9.0},
    ])

    # hot_keywords xlsx (2 rows)
    _make_xlsx(FIXTURE / "xhs_data" / "hot_trends_test.xlsx", [
        {"hot_id": "h1", "keyword": "trending_kw1", "score": 85.5},
        {"hot_id": "h2", "keyword": "trending_kw2", "score": 42.0},
    ])

    # generated_content xlsx (2 rows)
    _make_xlsx(FIXTURE / "xhs_data" / "generated_content_test.xlsx", [
        {"content_id": "c1", "goal_id": "goal_mig_test", "persona_id": "p_test",
         "title": "Generated Post 1", "body": "Body 1", "hashtags": ["test", "migration"],
         "publish_at": "2026-05-22 12:00", "status": "draft"},
        {"content_id": "c2", "goal_id": "goal_mig_test", "persona_id": "p_test",
         "title": "Generated Post 2", "body": "Body 2", "hashtags": ["test"],
         "publish_at": "2026-05-23 12:00", "status": "published"},
    ])

    # cookies.db (1 row)
    _make_cookie_db(FIXTURE / "config" / "cookies.db", "test_account", "test_cookie_value_123", "test note")

    yield

    # cleanup generated fixture files
    for p in [
        FIXTURE / "xhs_data" / "spider_xhs_test_001.xlsx",
        FIXTURE / "xhs_data" / "hot_trends_test.xlsx",
        FIXTURE / "xhs_data" / "generated_content_test.xlsx",
        FIXTURE / "config" / "cookies.db",
    ]:
        if p.exists():
            p.unlink()


# ── tests ────────────────────────────────────────────────────────────────


class TestDryRun:
    def test_dry_run_no_writes(self, pg_tenant, pg_backend, migrate_fixtures):
        """--dry-run should report source counts but write zero rows to PG."""
        tid = pg_tenant
        code = _run_migrate(tid, dry_run=True)

        assert code == 0

        # collected_notes should be empty
        df = pg_backend.list_collected_data(tid, EPOCH)
        assert len(df) == 0, f"expected 0 collected rows after dry-run, got {len(df)}"

        # hot_keywords should be empty
        df_hot = pg_backend.list_hot_keywords(tid, EPOCH)
        assert len(df_hot) == 0

        # generated_content should be empty
        df_gen = pg_backend.list_generated_posts(tid)
        assert len(df_gen) == 0

    def test_dry_run_output_describes_counts(self, pg_tenant, pg_backend, migrate_fixtures, capsys):
        """dry-run stdout should mention each table with source count."""
        tid = pg_tenant
        code = _run_migrate(tid, dry_run=True)
        assert code == 0

        captured = capsys.readouterr().out
        assert "goals" in captured.lower() or "goals" in captured
        assert "personas" in captured.lower()


class TestRealRun:
    def test_real_run_then_verify(self, pg_tenant, pg_backend, migrate_fixtures):
        """Full migration without --dry-run, with --verify → exit 0, target == source."""
        tid = pg_tenant
        code = _run_migrate(tid, verify=True)
        assert code == 0, f"migrate+verify failed with code {code}"

        # collected_notes: 3 rows
        df = pg_backend.list_collected_data(tid, EPOCH)
        assert len(df) >= 3, f"collected_notes: expected >=3, got {len(df)}"

        # hot_keywords: 2 rows
        df_hot = pg_backend.list_hot_keywords(tid, EPOCH)
        assert len(df_hot) >= 2, f"hot_keywords: expected >=2, got {len(df_hot)}"

        # generated_content: 2 rows
        df_gen = pg_backend.list_generated_posts(tid)
        assert len(df_gen) >= 2, f"generated_content: expected >=2, got {len(df_gen)}"

        # goals: at least 1 goal
        goals_data = pg_backend.load_goals(tid)
        assert len(goals_data.get("goals", [])) >= 1

        # personas: at least 1 persona
        persona_data = pg_backend.load_persona(tid)
        assert len(persona_data.get("personas", [])) >= 1


class TestVerifyMismatch:
    def test_verify_mismatch_exits_1(self, pg_tenant, pg_backend, migrate_fixtures):
        """If PG has fewer rows than source, --verify should exit 1."""
        tid = pg_tenant

        # First, migrate normally without verify
        code = _run_migrate(tid, verify=False)
        assert code == 0

        # Manually delete one row from collected_notes to cause mismatch
        from db.session import get_rls_cursor
        with get_rls_cursor(tid) as cur:
            cur.execute(
                "DELETE FROM collected_notes WHERE tenant_id = %s AND note_id = %s",
                (tid, "n1"),
            )

        # Verify only (don't re-migrate, which would re-add the deleted row)
        from scripts.migrate_to_pg import _verify
        report = {"collected_notes": 3, "hot_keywords": 2, "generated_content": 2,
                  "goals": 2, "personas": 1, "agent_memory": 2,
                  "agent_equipment": 0, "cookies": 1}
        ok = _verify(tid, pg_backend, report)
        assert not ok, "verify should return False after manual DELETE"


class TestSkipTables:
    def test_skip_tables_respected(self, pg_tenant, pg_backend, migrate_fixtures):
        """--skip-tables should skip the named tables."""
        tid = pg_tenant
        code = _run_migrate(tid, skip_tables="collected_notes,hot_keywords")
        assert code == 0

        # skipped tables should be empty
        df = pg_backend.list_collected_data(tid, EPOCH)
        assert len(df) == 0, f"collected_notes should be empty when skipped"

        df_hot = pg_backend.list_hot_keywords(tid, EPOCH)
        assert len(df_hot) == 0, f"hot_keywords should be empty when skipped"

        # but non-skipped tables should have data
        df_gen = pg_backend.list_generated_posts(tid)
        assert len(df_gen) >= 2
