"""Tests for scripts/cutover.py — P4.5 cutover & smoke verification."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

# Import cutover module functions directly (avoid subprocess on Windows)
import scripts.cutover as cutover


# ── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def temp_project(tmp_path: Path) -> Path:
    """Create a minimal project tree that mirrors the real layout."""
    proj = tmp_path / "spider_xhs"
    (proj / "config").mkdir(parents=True)
    (proj / "xhs_data").mkdir()
    (proj / "memory" / "default" / "shared").mkdir(parents=True)
    (proj / "memory" / "default" / "content").mkdir(parents=True)

    (proj / "config" / "goals.json").write_text(json.dumps({"active_goal_id": "g1", "goals": []}))
    (proj / "config" / "personas.json").write_text(json.dumps({"active_id": "p1", "personas": []}))
    (proj / "config" / "strategy.json").write_text(json.dumps({"overall": {}}))
    (proj / "config" / "cookies.db").write_text("mock sqlite")
    (proj / "memory" / "default" / "shared" / "test.md").write_text("# test")
    (proj / "memory" / "default" / "content" / "playbook.md").write_text("# playbook")

    env_path = proj / ".env"
    env_path.write_text("STORAGE_BACKEND=local\nDATABASE_URL=postgresql://x\nJWT_SECRET=test\n")
    return proj


@pytest.fixture
def temp_project_baked(temp_project: Path) -> Path:
    """Run backup first, then return the path (simulating pre-cutover state)."""
    rc = cutover.cmd_backup(temp_project)
    assert rc == 0, f"backup failed with code {rc}"
    return temp_project


def _ns(**kwargs) -> argparse.Namespace:
    """Build a minimal argparse.Namespace for testing."""
    return argparse.Namespace(**kwargs)


# ── backup tests ─────────────────────────────────────────────────────────


class TestBackup:
    def test_backup_renames_config_files(self, temp_project):
        rc = cutover.cmd_backup(temp_project)
        assert rc == 0

        assert not (temp_project / "config" / "goals.json").exists()
        assert not (temp_project / "config" / "personas.json").exists()
        assert not (temp_project / "config" / "strategy.json").exists()
        assert not (temp_project / "config" / "cookies.db").exists()

        assert (temp_project / "config" / "goals.json.bak").exists()
        assert (temp_project / "config" / "personas.json.bak").exists()
        assert (temp_project / "config" / "strategy.json.bak").exists()
        assert (temp_project / "config" / "cookies.db.bak").exists()

    def test_backup_renames_memory_default(self, temp_project):
        rc = cutover.cmd_backup(temp_project)
        assert rc == 0

        assert not (temp_project / "memory" / "default").exists()
        assert (temp_project / "memory" / "default.bak").is_dir()
        assert (temp_project / "memory" / "default.bak" / "shared" / "test.md").exists()

    def test_backup_idempotent(self, temp_project):
        rc1 = cutover.cmd_backup(temp_project)
        assert rc1 == 0
        rc2 = cutover.cmd_backup(temp_project)
        assert rc2 == 0

    def test_backup_without_files_is_ok(self, tmp_path):
        rc = cutover.cmd_backup(tmp_path)
        assert rc == 0

    def test_backup_preserves_file_content(self, temp_project):
        original = (temp_project / "config" / "goals.json").read_bytes()
        rc = cutover.cmd_backup(temp_project)
        assert rc == 0
        bak = (temp_project / "config" / "goals.json.bak").read_bytes()
        assert bak == original


# ── rollback tests ───────────────────────────────────────────────────────


class TestRollback:
    def test_rollback_restores_config_files(self, temp_project_baked):
        rc = cutover.cmd_rollback(temp_project_baked)
        assert rc == 0

        assert (temp_project_baked / "config" / "goals.json").exists()
        assert (temp_project_baked / "config" / "personas.json").exists()
        assert (temp_project_baked / "config" / "strategy.json").exists()
        assert (temp_project_baked / "config" / "cookies.db").exists()
        assert not (temp_project_baked / "config" / "goals.json.bak").exists()

    def test_rollback_restores_memory_default(self, temp_project_baked):
        rc = cutover.cmd_rollback(temp_project_baked)
        assert rc == 0

        assert (temp_project_baked / "memory" / "default").is_dir()
        assert not (temp_project_baked / "memory" / "default.bak").exists()

    def test_rollback_without_bak_is_noop(self, temp_project):
        rc = cutover.cmd_rollback(temp_project)
        assert rc == 0

    def test_rollback_flips_env_to_local(self, temp_project_baked):
        env_path = temp_project_baked / ".env"
        env_path.write_text("STORAGE_BACKEND=postgres\nDATABASE_URL=x\nJWT_SECRET=test\n")

        rc = cutover.cmd_rollback(temp_project_baked)
        assert rc == 0
        assert "STORAGE_BACKEND=local" in env_path.read_text()
        assert "STORAGE_BACKEND=postgres" not in env_path.read_text()


# ── env flip tests ───────────────────────────────────────────────────────


class TestEnvFlip:
    def test_flip_to_postgres(self, temp_project):
        args = _ns(target="postgres", env_path=None, project_root=str(temp_project))
        rc = cutover.cmd_flip_env(args)
        assert rc == 0
        assert "STORAGE_BACKEND=postgres" in (temp_project / ".env").read_text()

    def test_flip_to_local(self, temp_project):
        args = _ns(target="local", env_path=None, project_root=str(temp_project))
        rc = cutover.cmd_flip_env(args)
        assert rc == 0
        assert "STORAGE_BACKEND=local" in (temp_project / ".env").read_text()

    def test_flip_env_preserves_other_vars(self, temp_project):
        env_path = temp_project / ".env"
        env_path.write_text("STORAGE_BACKEND=local\nDATABASE_URL=pg://secret\nJWT_SECRET=abc\n")
        args = _ns(target="postgres", env_path=None, project_root=str(temp_project))
        rc = cutover.cmd_flip_env(args)
        assert rc == 0
        content = env_path.read_text()
        assert "DATABASE_URL=pg://secret" in content
        assert "JWT_SECRET=abc" in content

    def test_flip_env_invalid_value_fails(self, temp_project):
        args = _ns(target="invalid", env_path=None, project_root=str(temp_project))
        rc = cutover.cmd_flip_env(args)
        assert rc != 0


# ── status tests ─────────────────────────────────────────────────────────


class TestStatus:
    def test_status_shows_backend(self, temp_project):
        args = _ns(project_root=str(temp_project), env_path=None)
        rc = cutover.cmd_status(args)
        assert rc == 0

    def test_status_detects_bak_files(self, temp_project_baked):
        args = _ns(project_root=str(temp_project_baked), env_path=None)
        rc = cutover.cmd_status(args)
        assert rc == 0


# ── smoke tests ──────────────────────────────────────────────────────────


class TestSmoke:
    def test_smoke_health_endpoint(self, temp_project):
        """Without a running server, smoke hits connection errors."""
        env_path = temp_project / ".env"
        env_path.write_text("STORAGE_BACKEND=postgres\nDATABASE_URL=x\nJWT_SECRET=test\n")
        args = _ns(jwt="test-token", base_url="http://localhost:8000",
                   project_root=str(temp_project), env_path=None)
        rc = cutover.cmd_smoke(args)
        # Will fail because no server is running
        assert rc != 0  # connection refused or timeout

    def test_smoke_missing_jwt_fails(self, temp_project):
        args = _ns(jwt="", base_url="http://localhost:8000",
                   project_root=str(temp_project), env_path=None)
        rc = cutover.cmd_smoke(args)
        assert rc != 0

    def test_cutover_requires_tenant_id(self, temp_project_baked):
        args = _ns(tenant_id="", jwt="", base_url="http://localhost:8000",
                   project_root=str(temp_project_baked), env_path=None)
        rc = cutover.cmd_cutover(args)
        assert rc != 0
