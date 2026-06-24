"""
F3 PR-2: DAG retry endpoint tests (TDD).
Run: pytest tests/test_dag_retry.py -v
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test_secret_for_pytest_only_not_for_prod")
os.environ.setdefault("JWT_ALGORITHM", "HS256")

from security.jwt import encode_token

TEST_JWT = encode_token("test-tenant", is_admin=False)
JWT_HEADER = {"Authorization": f"Bearer {TEST_JWT}"}


@pytest.fixture()
def client(tmp_path):
    settings = {
        "kimi_api_key": "test-key",
        "llm_provider": "mock",
    }
    (tmp_path / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

    from server.main import app
    with TestClient(app) as c:
        yield c


class TestDagRetry:
    """POST /api/v1/dag/{dag_id}/retry/{node_id}."""

    def test_retry_dag_not_found(self, client):
        """Unknown dag_id → 400 (ValueError from retry_task)."""
        r = client.post("/api/v1/dag/no_such_dag/retry/n1", headers=JWT_HEADER)
        assert r.status_code == 400

    @patch("agents.master.HermesMaster")
    def test_retry_success(self, MockMaster, client):
        """Successful retry returns updated DAG status."""
        mock_instance = MockMaster.return_value
        mock_instance.retry_task.return_value = []

        r = client.post("/api/v1/dag/dag_test/retry/n1", headers=JWT_HEADER)
        assert r.status_code == 200
        mock_instance.retry_task.assert_called_once_with("dag_test", "n1", tenant_id="test-tenant")

    @patch("agents.master.HermesMaster")
    def test_retry_value_error(self, MockMaster, client):
        """HermesMaster.retry_task raises ValueError → 400."""
        mock_instance = MockMaster.return_value
        mock_instance.retry_task.side_effect = ValueError("task n1 status is pending, cannot retry")

        r = client.post("/api/v1/dag/dag_test/retry/n1", headers=JWT_HEADER)
        assert r.status_code == 400
        body = r.json()
        assert "pending" in body["detail"]
