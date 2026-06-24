from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("JWT_SECRET", "test_secret_for_pytest_only_not_for_prod")
os.environ.setdefault("JWT_ALGORITHM", "HS256")

from agent_tools import packaging_rules
from security.jwt import encode_token
from server.main import app
from server.middleware.idempotency import clear_idempotency_caches_for_tests

TENANT_A = "tenant-a"
JWT_A = encode_token(TENANT_A)
HEADER_A = {"Authorization": f"Bearer {JWT_A}"}


def _ik(suffix: str = "") -> str:
    label = f"{suffix}-" if suffix else ""
    return f"packaging-{label}{uuid.uuid4().hex}"


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    clear_idempotency_caches_for_tests()
    packaging_rules.load_packaging_rules.cache_clear()


@pytest.fixture
def rules_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "test_rules.md"
    path.write_text(
        "# 五大爆文标题公式\n\n"
        "规则正文\n\n"
        "## CES\n\n"
        "点赞 + 收藏 + 评论 * 4 + 分享 * 4 + 关注 * 8\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(packaging_rules, "_RULES_PATH", path)
    packaging_rules.load_packaging_rules.cache_clear()
    return path


async def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class TestPackagingRouter:
    async def test_get_returns_current_rules_with_updated_at(
        self,
        rules_file: Path,
    ) -> None:
        async with await _client() as ac:
            resp = await ac.get("/api/v1/packaging/rules", headers=HEADER_A)

        assert resp.status_code == 200
        body = resp.json()
        assert "五大爆文" in body["rules"]
        datetime.fromisoformat(body["updated_at"])

    async def test_put_writes_new_rules_and_invalidates_cache(
        self,
        rules_file: Path,
    ) -> None:
        old_rules = packaging_rules.load_packaging_rules()
        new_rules = (
            "# 五大爆文标题公式\n\n"
            "1. 反直觉型\n"
            "2. 数字清单型\n\n"
            "## CES\n\n"
            "CES = 点赞 + 收藏 + 评论 * 4 + 分享 * 4 + 关注 * 8\n"
        )

        async with await _client() as ac:
            resp = await ac.put(
                "/api/v1/packaging/rules",
                json={"rules": new_rules},
                headers={**HEADER_A, "Idempotency-Key": _ik("put-success")},
            )

        assert resp.status_code == 200
        assert resp.json()["rules"] == new_rules
        assert rules_file.read_text(encoding="utf-8") == new_rules
        assert old_rules != packaging_rules.load_packaging_rules()
        assert packaging_rules.load_packaging_rules() == new_rules

    async def test_put_rejects_missing_required_fields_422(
        self,
        rules_file: Path,
    ) -> None:
        async with await _client() as ac:
            resp = await ac.put(
                "/api/v1/packaging/rules",
                json={"rules": "## CES\n\n只有互动分公式，没有标题公式。\n"},
                headers={**HEADER_A, "Idempotency-Key": _ik("put-invalid")},
            )

        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "packaging_invalid"

    async def test_put_without_idempotency_key_rejected_428(
        self,
        rules_file: Path,
    ) -> None:
        valid_rules = (
            "# 五大爆文标题公式\n\n"
            "## CES\n\n"
            "CES = 点赞 + 收藏 + 评论 * 4 + 分享 * 4 + 关注 * 8\n"
        )

        async with await _client() as ac:
            resp = await ac.put(
                "/api/v1/packaging/rules",
                json={"rules": valid_rules},
                headers=HEADER_A,
            )

        assert resp.status_code == 428
        assert resp.json()["error"]["code"] == "missing_idempotency_key"

    async def test_put_is_atomic_no_partial_file_on_validation_failure(
        self,
        rules_file: Path,
    ) -> None:
        before = rules_file.read_text(encoding="utf-8")
        tmp_file = rules_file.with_suffix(".md.tmp")

        async with await _client() as ac:
            resp = await ac.put(
                "/api/v1/packaging/rules",
                json={"rules": "# 五大爆文标题公式\n\n缺少互动公式锚点。\n"},
                headers={**HEADER_A, "Idempotency-Key": _ik("put-atomic-invalid")},
            )

        assert resp.status_code == 422
        assert rules_file.read_text(encoding="utf-8") == before
        assert not tmp_file.exists()
