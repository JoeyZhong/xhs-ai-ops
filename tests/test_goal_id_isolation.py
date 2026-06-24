"""
goal_id 全链路隔离验收测试（cross-cutting-dimension-governance B.6）。

设计：
  - 使用 LocalJsonBackend（tmp_path 隔离），不依赖 PG
  - 写入 2 个不同 goal 的采集数据
  - 通过 FastAPI TestClient 验证 API 层按 goal_id 过滤

运行：pytest tests/test_goal_id_isolation.py -v
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from security.jwt import encode_token

os.environ.setdefault("JWT_SECRET", "test_secret_for_p2_only")
os.environ.setdefault("JWT_ALGORITHM", "HS256")

JWT = encode_token("test-tenant")
AUTH = {"Authorization": f"Bearer {JWT}"}


@pytest.fixture()
def client(tmp_path):
    """使用 tmp_path 隔离的 LocalJsonBackend + TestClient。"""
    test_backend = _make_test_backend(tmp_path)
    # 预写入 2 个 goal 各 1 条采集记录
    _seed_data(test_backend)

    with patch("storage.factory.get_backend", return_value=test_backend):
        from server.main import app
        with TestClient(app) as c:
            yield c


def _make_test_backend(tmp_path: Path):
    from storage.local_json import LocalJsonBackend
    # 创建必要的 config 子目录（backend.load_goals 需要 config/）
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    goals = {"active_goal_id": "", "goals": []}
    (cfg_dir / "goals.json").write_text(
        __import__("json").dumps(goals), encoding="utf-8")
    # 创建 xhs_data/test-tenant/ 子目录
    data_dir = tmp_path / "xhs_data" / "test-tenant"
    data_dir.mkdir(parents=True, exist_ok=True)
    return LocalJsonBackend(base_dir=str(tmp_path))


def _seed_data(backend):
    """写入 2 条不同 goal_id 的采集记录，加上 1 条老格式（无 goal_id 文件名）记录。"""
    # goal_001 数据（新文件名格式）
    df1 = pd.DataFrame([{
        "采集时间": "2026-05-28 10:00:00",
        "搜索关键词": "test_kw1",
        "笔记ID": "note_001",
        "标题": "Goal 001 Note",
        "goal_id": "goal_001",
    }])
    # goal_001 的 meta 标记
    class _Ctx:
        storage = backend
        tenant_id = "test-tenant"
        extra = None
    df1_path = backend.save_collected_data(
        tenant_id="test-tenant", source="test",
        df=df1, meta={"goal_id": "goal_001", "keywords": ["test_kw1"]},
    )

    # goal_002 数据（新文件名格式）
    df2 = pd.DataFrame([{
        "采集时间": "2026-05-28 11:00:00",
        "搜索关键词": "test_kw2",
        "笔记ID": "note_002",
        "标题": "Goal 002 Note",
        "goal_id": "goal_002",
    }])
    backend.save_collected_data(
        tenant_id="test-tenant", source="test",
        df=df2, meta={"goal_id": "goal_002", "keywords": ["test_kw2"]},
    )

    # 老格式文件（文件名不含 goal_id，模拟历史数据）
    legacy_dir = backend._data_path("test-tenant")
    legacy_df = pd.DataFrame([{
        "采集时间": "2026-05-27 09:00:00",
        "搜索关键词": "legacy",
        "笔记ID": "note_legacy",
        "标题": "Legacy Note (no goal in filename)",
        "goal_id": "",
    }])
    legacy_path = legacy_dir / "spider_xhs_采集结果_20260527_090000.xlsx"
    legacy_df.to_excel(legacy_path, index=False, sheet_name="采集数据")


class TestGoalIdIsolation:
    """验证 goal_id 全链路隔离：写入 → 存储 → API 读取。"""

    def test_list_without_goal_id_returns_all(self, client):
        """GET /api/v1/notes → 返回全部 3 条（2 条新格式 + 1 条老格式）。"""
        r = client.get("/api/v1/notes", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 3, f"expected 3, got {body['total']}"
        note_ids = {n.get("笔记ID") for n in body["notes"]}
        assert note_ids == {"note_001", "note_002", "note_legacy"}

    def test_list_goal_001_returns_only_goal_001(self, client):
        """GET /api/v1/notes?goal_id=goal_001 → 仅返回 goal_001 的数据。"""
        r = client.get("/api/v1/notes?goal_id=goal_001", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1, f"expected 1, got {body['total']}"
        note = body["notes"][0]
        assert note.get("笔记ID") == "note_001"
        assert note.get("goal_id") == "goal_001"

    def test_list_goal_002_returns_only_goal_002(self, client):
        """GET /api/v1/notes?goal_id=goal_002 → 仅返回 goal_002 的数据。"""
        r = client.get("/api/v1/notes?goal_id=goal_002", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1, f"expected 1, got {body['total']}"
        note = body["notes"][0]
        assert note.get("笔记ID") == "note_002"
        assert note.get("goal_id") == "goal_002"

    def test_list_goal_default_returns_all(self, client):
        """GET /api/v1/notes?goal_id=default → 等同不传，返回全部。"""
        r = client.get("/api/v1/notes?goal_id=default", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 3

    def test_list_goal_empty_string_returns_all(self, client):
        """GET /api/v1/notes?goal_id= → 等同不传，返回全部。"""
        r = client.get("/api/v1/notes?goal_id=", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 3

    def test_filesystem_filename_contains_goal_id(self):
        """验证写入的文件名包含 goal_id 前缀。"""
        from storage.local_json import LocalJsonBackend
        import tempfile
        import re
        with tempfile.TemporaryDirectory() as tmp:
            backend = LocalJsonBackend(base_dir=tmp)
            df = pd.DataFrame([{
                "采集时间": "2026-05-28 12:00:00",
                "搜索关键词": "verify",
                "笔记ID": "verify_note",
                "标题": "Verify",
                "goal_id": "goal_verify",
            }])
            path = backend.save_collected_data(
                tenant_id="default", source="test", df=df,
                meta={"goal_id": "goal_verify"},
            )
            # 文件名应包含 goal_verify
            assert "goal_verify" in path, f"filename missing goal_id: {path}"
            # 文件名格式验证
            fname = Path(path).name
            assert re.match(r"spider_xhs_采集结果_goal_verify_\d{8}_\d{6}\.xlsx", fname), \
                f"unexpected filename format: {fname}"

    def test_sanitize_goal_id_replaces_special_chars(self):
        """_sanitize_goal_id 替换特殊字符，空串 fallback 到 unassigned。"""
        from storage.local_json import LocalJsonBackend
        safe = LocalJsonBackend._sanitize_goal_id
        assert safe("goal/001\\test") == "goal_001_test"
        assert safe("goal 001") == "goal_001"
        assert safe("") == "unassigned"
        assert safe(None) == "unassigned"
        assert safe("normal") == "normal"

    def test_list_new_goal_does_not_return_legacy_files(self, client):
        """查询不存在的 goal_id 时，老格式文件不被包含（返回空）。"""
        r = client.get("/api/v1/notes?goal_id=goal_nonexistent", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 0

    def test_sse_worker_routes_save_through_backend(self, monkeypatch):
        """SSE worker 必须经 storage backend 落盘（消除双写路径）。

        旧实现 _save_to_excel 直写文件，绕开 backend，导致 postgres 模式静默丢数据。
        本测试锁住：worker 调用 backend.save_collected_data，且 goal_id 既进 meta
        也进 df 行。
        """
        import asyncio
        import threading
        from server import stream_utils

        calls: dict = {}

        class _FakeBackend:
            def save_collected_data(self, tenant_id, source, df, meta=None):
                calls["tenant_id"] = tenant_id
                calls["source"] = source
                calls["df"] = df
                calls["meta"] = meta or {}
                return "fake/path.xlsx"

        monkeypatch.setattr("storage.factory.get_backend", lambda: _FakeBackend())
        monkeypatch.setattr(stream_utils, "get_cookie", lambda *a, **k: "ck")

        fixture_notes = [{
            "id": "noteX",
            "note_card": {
                "display_title": "标题X",
                "type": "normal",
                "user": {"nick_name": "u"},
                "interact_info": {"liked_count": "5"},
            },
        }]

        class _FakeApi:
            def search_some_note(self, *a, **k):
                return True, "", fixture_notes

        monkeypatch.setattr(stream_utils, "XHS_Apis", lambda: _FakeApi())

        loop = asyncio.new_event_loop()
        try:
            q: asyncio.Queue = asyncio.Queue()
            stop = threading.Event()
            stream_utils.sync_collect_worker(
                ["kw1"], q, loop, account_id="default", stop_event=stop,
                skip_api=False, goal_id="goal_xyz", tenant_id="default",
            )
        finally:
            loop.close()

        assert "df" in calls, "worker did not route save through backend"
        assert calls["tenant_id"] == "default"
        assert calls["meta"].get("goal_id") == "goal_xyz"
        df = calls["df"]
        assert len(df) == 1
        assert df.iloc[0]["goal_id"] == "goal_xyz"
        assert df.iloc[0]["笔记ID"] == "noteX"
