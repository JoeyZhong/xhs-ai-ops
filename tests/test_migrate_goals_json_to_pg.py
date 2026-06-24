"""
Tests for scripts/migrate_goals_json_to_pg.py.

Coverage:
  - --dry-run: parse + count, no PG writes
  - 正式迁移: backup + upsert + clear legacy arrays
  - 幂等重跑: second run produces no new rows
  - --verify: compare source vs PG counts

Uses mock PG cursor + temp goals.json to avoid touching real database or config.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# Prevent the migration script from trying to load ~/.spider_xhs/.env
os.environ.setdefault("DATABASE_URL", "postgresql://mock:mock@localhost/test_for_pytest")


@pytest.fixture(autouse=True)
def _cleanup_env() -> None:
    """Ensure DATABASE_URL is set for every test."""
    os.environ.setdefault("DATABASE_URL", "postgresql://mock:mock@localhost/test_for_pytest")
    yield


# ── Helpers: build sample goals.json ────────────────────────────────────────


def _sample_goals(extra_topics: int = 0) -> dict:
    """Build a goals.json payload resembling production data."""
    goals: list[dict[str, Any]] = [
        {
            "goal_id": "goal_001",
            "persona_id": "persona_default",
            "topic_library": [
                {"title": "工厂选址技巧", "angle": "反直觉型", "funnel_stage": "traffic"},
                {"title": "自助机收益揭秘", "funnel_stage": "trust"},
            ],
            "content_calendar": [
                {
                    "title": "工厂选址技巧",
                    "date": "2026-06-01",
                    "time": "12:00",
                    "status": "scheduled",
                },
                {
                    "title": "自助机收益揭秘",
                    "date": "2026-06-05",
                    "time": "20:30",
                    "status": "planned",
                },
            ],
        },
        {
            "goal_id": "goal_002",
            "persona_id": None,
            "topic_library": [
                {"title": "学校点位怎么谈", "angle": "焦虑共鸣型"},
            ],
            "content_calendar": [
                {
                    "title": "学校点位怎么谈",
                    "date": "2026-06-10",
                    "time": "12:00",
                    "status": "draft",
                },
            ],
        },
    ]
    # Add extra topics for idempotency test
    for i in range(extra_topics):
        goals[0]["topic_library"].append({"title": f"Extra Topic {i}"})
        goals[0]["content_calendar"].append({
            "title": f"Extra Topic {i}",
            "date": "2026-07-01",
            "time": "12:00",
            "status": "planned",
        })
    return {"goals": goals}


# ── Mock PG cursor ──────────────────────────────────────────────────────────


class MockCursor:
    """Simulates a psycopg2 cursor for capturing upserts and responding to count queries."""

    def __init__(self) -> None:
        self.topics: list[dict] = []
        self.calendar_items: list[dict] = []
        self.executed_sql: list[str] = []
        self._result: tuple | None = None

    def execute(self, sql: str, params: tuple | None = None) -> None:
        self.executed_sql.append(sql)
        if "SELECT count(*)" in sql:
            if "topics" in sql.lower():
                self._result = (len(self.topics),)
            elif "calendar_items" in sql.lower():
                self._result = (len(self.calendar_items),)
            else:
                self._result = (0,)

    def fetchone(self) -> tuple | None:
        return self._result

    def __enter__(self) -> MockCursor:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_execute_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace psycopg2.extras.execute_values to capture rows into the mock cursor."""
    import psycopg2.extras

    def _fake_execute_values(cursor: MockCursor, sql: str, params: list[tuple], template: str = "") -> None:
        if "topics" in sql:
            for row in params:
                cursor.topics.append({
                    "topic_id": row[0],
                    "tenant_id": row[1],
                    "title": row[4],
                })
        elif "calendar_items" in sql:
            for row in params:
                cursor.calendar_items.append({
                    "calendar_item_id": row[0],
                    "tenant_id": row[1],
                })

    monkeypatch.setattr(psycopg2.extras, "execute_values", _fake_execute_values)


@pytest.fixture
def mock_cursor(monkeypatch: pytest.MonkeyPatch, mock_execute_values: None) -> MockCursor:
    """Replace db.session.get_rls_cursor with a fixture that yields MockCursor."""
    cursor = MockCursor()

    from db import session as db_session_module

    def _fake_cursor(tenant_id: str, *, is_admin: bool = False):
        return cursor

    monkeypatch.setattr(db_session_module, "get_rls_cursor", _fake_cursor)
    return cursor


# ── Tests ───────────────────────────────────────────────────────────────────


class TestMigrateGoalsToPg:
    """Tests for the migration script's core logic."""

    def test_parse_structure(self) -> None:
        """migrate_goals_json correctly parses topic_library and content_calendar."""
        from scripts.migrate_goals_json_to_pg import migrate_goals_json

        goals = _sample_goals()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "goals.json"
            path.write_text(json.dumps(goals, ensure_ascii=False), "utf-8")
            rows = migrate_goals_json("00000000-0000-0000-0000-000000000001", path, dry_run=False)

        assert len(rows["topics"]) == 3  # goal_001: 2 + goal_002: 1
        assert len(rows["calendar_items"]) == 3
        # Verify stable IDs
        assert rows["topics"][0]["topic_id"].startswith("topic_")
        assert rows["calendar_items"][0]["calendar_item_id"].startswith("calendar_")

    def test_parse_old_format_string_list(self) -> None:
        """Handle topic_library as list of strings (old format)."""
        from scripts.migrate_goals_json_to_pg import migrate_goals_json

        goals = {
            "goals": [
                {
                    "goal_id": "goal_001",
                    "topic_library": ["Old Topic One", "Old Topic Two"],
                    "content_calendar": [],
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "goals.json"
            path.write_text(json.dumps(goals, ensure_ascii=False), "utf-8")
            rows = migrate_goals_json("00000000-0000-0000-0000-000000000001", path, dry_run=False)

        assert len(rows["topics"]) == 2
        assert rows["topics"][0]["title"] == "Old Topic One"
        assert rows["topics"][0]["angle"] is None

    def test_dry_run(self, mock_cursor: MockCursor) -> None:
        """--dry-run parses and counts but does not write to PG."""
        from scripts.migrate_goals_json_to_pg import cmd_migrate
        from argparse import Namespace

        goals = _sample_goals()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "goals.json"
            path.write_text(json.dumps(goals, ensure_ascii=False), "utf-8")

            args = Namespace(
                tenant_id="00000000-0000-0000-0000-000000000001",
                goals_json=str(path),
                dry_run=True,
                verify=False,
            )
            cmd_migrate(args)

        # No writes to mock cursor
        assert len(mock_cursor.topics) == 0
        assert len(mock_cursor.calendar_items) == 0

    def test_full_migration(self, mock_cursor: MockCursor) -> None:
        """Full migration writes rows to PG and creates .bak."""
        from scripts.migrate_goals_json_to_pg import cmd_migrate
        from argparse import Namespace

        goals = _sample_goals()
        with tempfile.TemporaryDirectory() as tmp:
            goals_path = Path(tmp) / "goals.json"
            goals_path.write_text(json.dumps(goals, ensure_ascii=False), "utf-8")
            bak_path = goals_path.with_suffix(".json.bak")

            args = Namespace(
                tenant_id="00000000-0000-0000-0000-000000000001",
                goals_json=str(goals_path),
                dry_run=False,
                verify=False,
            )
            cmd_migrate(args)

            # PG rows inserted
            assert len(mock_cursor.topics) == 3
            assert len(mock_cursor.calendar_items) == 3

            # Backup created
            assert bak_path.exists()
            bak_data = json.loads(bak_path.read_text("utf-8"))
            assert len(bak_data["goals"]) == 2

            # Legacy arrays cleared in source
            cleaned = json.loads(goals_path.read_text("utf-8"))
            for goal in cleaned["goals"]:
                assert goal["topic_library"] == []
                assert goal["content_calendar"] == []

    def test_idempotent_rerun(self, mock_cursor: MockCursor) -> None:
        """Second migration run produces same row count (no duplicate rows)."""
        from scripts.migrate_goals_json_to_pg import cmd_migrate
        from argparse import Namespace

        goals = _sample_goals(extra_topics=2)  # 3+2=5 topics
        with tempfile.TemporaryDirectory() as tmp:
            goals_path = Path(tmp) / "goals.json"
            goals_path.write_text(json.dumps(goals, ensure_ascii=False), "utf-8")

            args = Namespace(
                tenant_id="00000000-0000-0000-0000-000000000001",
                goals_json=str(goals_path),
                dry_run=False,
                verify=False,
            )

            # First run
            mock_cursor.topics.clear()
            mock_cursor.calendar_items.clear()
            cmd_migrate(args)
            first_topic_count = len(mock_cursor.topics)
            first_cal_count = len(mock_cursor.calendar_items)

            # Legacy arrays were cleared by first run — simulate re-population
            goals_repop = _sample_goals(extra_topics=2)
            goals_path.write_text(json.dumps(goals_repop, ensure_ascii=False), "utf-8")

            # Second run (fresh mock cursor)
            mock_cursor2 = MockCursor()
            from db import session as db_session_module
            import psycopg2.extras

            # Re-apply mocks for second cursor
            def _fake_cursor2(tid: str, *, is_admin: bool = False):
                return mock_cursor2

            import scripts.migrate_goals_json_to_pg as mig_mod
            mig_mod.upsert_topics.__globals__["get_rls_cursor"] = _fake_cursor2
            mig_mod.upsert_calendar_items.__globals__["get_rls_cursor"] = _fake_cursor2

            def _fake_execute_values2(cur, sql, params, template=""):
                if "topics" in sql:
                    for row in params:
                        mock_cursor2.topics.append({"topic_id": row[0]})
                elif "calendar_items" in sql:
                    for row in params:
                        mock_cursor2.calendar_items.append({"calendar_item_id": row[0]})

            psycopg2.extras.execute_values = _fake_execute_values2

            cmd_migrate(args)
            second_topic_count = len(mock_cursor2.topics)
            second_cal_count = len(mock_cursor2.calendar_items)

            # Idempotent: same number of rows
            assert second_topic_count == first_topic_count
            assert second_cal_count == first_cal_count

    def test_verify_after_migration(self, mock_cursor: MockCursor) -> None:
        """--verify flag compares source vs PG counts and reports success."""
        from scripts.migrate_goals_json_to_pg import cmd_migrate
        from argparse import Namespace

        goals = _sample_goals()
        with tempfile.TemporaryDirectory() as tmp:
            goals_path = Path(tmp) / "goals.json"
            goals_path.write_text(json.dumps(goals, ensure_ascii=False), "utf-8")

            args = Namespace(
                tenant_id="00000000-0000-0000-0000-000000000001",
                goals_json=str(goals_path),
                dry_run=False,
                verify=True,
            )

            # Should exit cleanly (no sys.exit(1))
            cmd_migrate(args)

            # Verify data was written
            assert len(mock_cursor.topics) == 3
            assert len(mock_cursor.calendar_items) == 3
