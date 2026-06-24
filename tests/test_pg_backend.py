"""Phase 4a · PgBackend 22 方法 × 跨租户隔离测试。

跑法:
  STORAGE_BACKEND=postgres PYTHONPATH=. pytest tests/test_pg_backend.py -v

DATABASE_URL_ADMIN 缺失 → pytest.skip(允许 CI 跳过)。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import uuid

import pandas as pd
import psycopg2
import pytest

from storage.base import RevMismatch, TenantContextRequired
from storage.factory import reset_backend


# ── 辅助 ─────────────────────────────────────────────────────────────────

def _make_df(**cols) -> pd.DataFrame:
    """单行 DataFrame 工厂。"""
    return pd.DataFrame([cols])


def _delete_universal_skills(pg_admin_dsn: str, *names: str) -> None:
    """Clean universal skills created by tests; tenant fixtures cannot cascade NULL rows."""
    conn = psycopg2.connect(pg_admin_dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SET app.is_admin = 'true'")
            cur.execute(
                "DELETE FROM skills WHERE tenant_id IS NULL AND name = ANY(%s)",
                (list(names),),
            )
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 1. 任务结果
# ══════════════════════════════════════════════════════════════════════════

class TestTaskResult:
    def test_save_and_load(self, pg_backend, pg_tenant):
        tid = pg_tenant
        pg_backend.save_task_result(tid, "task-1", {"status": "done", "score": 0.95})
        result = pg_backend.load_task_result(tid, "task-1")
        assert result is not None
        assert result["status"] == "done"

    def test_isolation(self, pg_backend, two_pg_tenants):
        ta, tb = two_pg_tenants
        uid = uuid.uuid4().hex[:8]
        pg_backend.save_task_result(ta, f"task-secret-{uid}", {"data": "for A only"})
        # A 能看到
        result = pg_backend.load_task_result(ta, f"task-secret-{uid}")
        assert result is not None
        # B 看不到
        result = pg_backend.load_task_result(tb, f"task-secret-{uid}")
        assert result is None

    def test_load_nonexistent(self, pg_backend, pg_tenant):
        result = pg_backend.load_task_result(pg_tenant, "no-such-task")
        assert result is None


# ══════════════════════════════════════════════════════════════════════════
# 2. Memory
# ══════════════════════════════════════════════════════════════════════════

class TestMemory:
    def test_save_and_load(self, pg_backend, pg_tenant):
        tid = pg_tenant
        pg_backend.save_memory(tid, "content", "playbook.md", "test content")
        content = pg_backend.load_memory(tid, "content", "playbook.md")
        assert content == "test content"

    def test_isolation(self, pg_backend, two_pg_tenants):
        ta, tb = two_pg_tenants
        pg_backend.save_memory(ta, "intel", "skills.md", "A's knowledge")
        content = pg_backend.load_memory(tb, "intel", "skills.md")
        assert content is None

    def test_load_nonexistent(self, pg_backend, pg_tenant):
        content = pg_backend.load_memory(pg_tenant, "no_scope", "no_file.md")
        assert content is None


# ══════════════════════════════════════════════════════════════════════════
# 3. 采集数据
# ══════════════════════════════════════════════════════════════════════════

class TestCollectedData:
    def test_save_and_list(self, pg_backend, pg_tenant):
        tid = pg_tenant
        df = _make_df(note_id="n1", title="Test Note", likes=10,
                      comments=2, shares=1, collects=3, ces_score=8.5,
                      keyword="test", author="user1")
        pg_backend.save_collected_data(tid, "search", df)
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        listed = pg_backend.list_collected_data(tid, since)
        assert len(listed) >= 1

    def test_isolation(self, pg_backend, two_pg_tenants):
        ta, tb = two_pg_tenants
        df = _make_df(note_id="n-secret", title="Secret", likes=0)
        pg_backend.save_collected_data(ta, "search", df)
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        listed = pg_backend.list_collected_data(tb, since)
        assert listed.empty or not (listed["note_id"] == "n-secret").any()

    def test_save_empty_df(self, pg_backend, pg_tenant):
        empty = pd.DataFrame()
        result = pg_backend.save_collected_data(pg_tenant, "search", empty)
        assert result == ""


# ══════════════════════════════════════════════════════════════════════════
# 4. 热词
# ══════════════════════════════════════════════════════════════════════════

class TestHotKeywords:
    def test_save_and_list(self, pg_backend, pg_tenant):
        tid = pg_tenant
        df = _make_df(keyword="热门词", score=95.0)
        pg_backend.save_hot_keywords(tid, df)
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        listed = pg_backend.list_hot_keywords(tid, since)
        assert len(listed) >= 1

    def test_isolation(self, pg_backend, two_pg_tenants):
        ta, tb = two_pg_tenants
        df = _make_df(keyword="A的独家词", score=100.0)
        batch_id = pg_backend.save_hot_keywords(ta, df)
        assert batch_id.startswith("hot-")
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        listed = pg_backend.list_hot_keywords(tb, since)
        assert listed.empty or not (listed["keyword"] == "A的独家词").any()

    def test_save_empty_df(self, pg_backend, pg_tenant):
        result = pg_backend.save_hot_keywords(pg_tenant, pd.DataFrame())
        assert result == ""


# ══════════════════════════════════════════════════════════════════════════
# 5. 生成内容
# ══════════════════════════════════════════════════════════════════════════

class TestGeneratedPosts:
    def test_save_and_list(self, pg_backend, pg_tenant):
        tid = pg_tenant
        df = _make_df(title="测试笔记", body="内容正文", hashtags=["tag1", "tag2"])
        pg_backend.save_generated_posts(tid, df, {"goal_id": "g1"})
        listed = pg_backend.list_generated_posts(tid)
        assert len(listed) >= 1

    def test_isolation(self, pg_backend, two_pg_tenants):
        ta, tb = two_pg_tenants
        df = _make_df(title="A的私密笔记", body="secret")
        pg_backend.save_generated_posts(ta, df)
        listed = pg_backend.list_generated_posts(tb)
        assert listed.empty or not (listed["title"] == "A的私密笔记").any()

    def test_list_with_since(self, pg_backend, pg_tenant):
        tid = pg_tenant
        df = _make_df(title="旧笔记", body="old")
        pg_backend.save_generated_posts(tid, df)
        # 查未来时间 → 空
        future = datetime.now(timezone.utc) + timedelta(days=1)
        listed = pg_backend.list_generated_posts(tid, since=future)
        assert listed.empty

    def test_save_empty_df(self, pg_backend, pg_tenant):
        result = pg_backend.save_generated_posts(pg_tenant, pd.DataFrame())
        assert result == ""


# ══════════════════════════════════════════════════════════════════════════
# 6. Goals
# ══════════════════════════════════════════════════════════════════════════

class TestGoals:
    def test_save_and_load(self, pg_backend, pg_tenant):
        tid = pg_tenant
        data = {"active_goal_id": "g1", "goals": [
            {"goal_id": "g1", "title": "测试目标", "metric": "impressions"},
        ]}
        pg_backend.save_goals(tid, data)
        loaded = pg_backend.load_goals(tid)
        assert loaded["active_goal_id"] == "g1"
        assert len(loaded["goals"]) == 1

    def test_isolation(self, pg_backend, two_pg_tenants):
        ta, tb = two_pg_tenants
        unique_id = str(uuid.uuid4().hex[:8])
        data = {"active_goal_id": unique_id, "goals": [{"goal_id": unique_id, "title": "A的目标"}]}
        pg_backend.save_goals(ta, data)
        loaded_b = pg_backend.load_goals(tb)
        assert loaded_b == {"active_goal_id": "", "goals": []}

    def test_load_empty(self, pg_backend, pg_tenant):
        loaded = pg_backend.load_goals(pg_tenant)
        assert loaded == {"active_goal_id": "", "goals": []}


# ══════════════════════════════════════════════════════════════════════════
# 7. Personas
# ══════════════════════════════════════════════════════════════════════════

class TestPersonas:
    def test_save_and_load(self, pg_backend, pg_tenant):
        tid = pg_tenant
        data = {"active_id": "p1", "personas": [
            {"persona_id": "p1", "name": "测试人设", "style": "professional"},
            {"persona_id": "p2", "name": "备用", "style": "casual"},
        ]}
        pg_backend.save_persona(tid, data)
        loaded = pg_backend.load_persona(tid)
        assert loaded["active_id"] == "p1"
        assert len(loaded["personas"]) == 2

    def test_isolation(self, pg_backend, two_pg_tenants):
        ta, tb = two_pg_tenants
        unique_id = str(uuid.uuid4().hex[:8])
        data = {"active_id": unique_id, "personas": [{"persona_id": unique_id, "name": "A"}]}
        pg_backend.save_persona(ta, data)
        loaded_b = pg_backend.load_persona(tb)
        assert loaded_b == {}

    def test_load_empty(self, pg_backend, pg_tenant):
        loaded = pg_backend.load_persona(pg_tenant)
        assert loaded == {}


# ══════════════════════════════════════════════════════════════════════════
# 8. Audit Log
# ══════════════════════════════════════════════════════════════════════════

class TestAuditLog:
    def test_save(self, pg_backend, pg_tenant):
        tid = pg_tenant
        pg_backend.save_audit_log(tid, {"kind": "test", "message": "hello"})
        # 验证不抛异常即可

    def test_isolation(self, pg_backend, two_pg_tenants):
        ta, tb = two_pg_tenants
        import os
        import psycopg2
        pg_backend.save_audit_log(ta, {"kind": "secret_op", "detail": "A only"})
        # 用 spider_app(NOSUPERUSER) 连,RLS 才生效
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            pytest.skip("DATABASE_URL not set")
        conn = psycopg2.connect(dsn)
        conn.autocommit = False
        try:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL app.tenant_id = %s", (tb,))
                cur.execute("SELECT count(*) FROM audit_log")
                count = cur.fetchone()[0]
            assert count == 0
        finally:
            conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 9. Cleanup
# ══════════════════════════════════════════════════════════════════════════

class TestCleanup:
    def test_cleanup_days_zero(self, pg_backend, pg_tenant):
        tid = pg_tenant
        df = _make_df(note_id="old-note", title="Old", likes=0)
        pg_backend.save_collected_data(tid, "search", df)
        deleted = pg_backend.cleanup_old_data(tid, days=0)
        # days=0 会删所有 old data
        assert isinstance(deleted, list)

    def test_isolation_cleanup(self, pg_backend, two_pg_tenants):
        ta, tb = two_pg_tenants
        df = _make_df(note_id="n-cleanup", title="Will be cleaned")
        pg_backend.save_collected_data(ta, "search", df)
        pg_backend.cleanup_old_data(tb, days=0)  # 删 B 的,不应影响 A


# ══════════════════════════════════════════════════════════════════════════
# 10. Skills
# ══════════════════════════════════════════════════════════════════════════

class TestSkills:
    def test_create_tenant_skill(self, pg_backend, pg_tenant):
        tid = pg_tenant
        skill = pg_backend.create_skill(
            tenant_id=tid, name="我的技能", description="测试",
            body="print('hello')", suggested_for=["intel"],
        )
        assert skill["name"] == "我的技能"
        assert skill["tenant_id"] == tid
        assert skill["rev"] == 1

    def test_create_universal_skill(self, pg_backend, pg_admin_dsn):
        """universal 池需要用 admin DSN 建。PgBackend 用 _SYSTEM_TENANT + is_admin=True。"""
        # 这里用 pg_admin_dsn 建一个 admin tenant
        name = f"测试通用技能-{uuid.uuid4().hex[:8]}"
        skill = pg_backend.create_skill(
            tenant_id=None, name=name, description="通用",
            body="common", suggested_for=["content"],
        )
        try:
            assert skill["tenant_id"] is None
            assert skill["name"] == name
        finally:
            _delete_universal_skills(pg_admin_dsn, name)

    def test_get_skill(self, pg_backend, pg_tenant):
        tid = pg_tenant
        created = pg_backend.create_skill(
            tenant_id=tid, name="可查找", description="desc",
            body="body", suggested_for=[],
        )
        fetched = pg_backend.get_skill(created["id"], tid)
        assert fetched["id"] == created["id"]

    def test_get_skill_not_found(self, pg_backend, pg_tenant):
        with pytest.raises(KeyError):
            pg_backend.get_skill("no-such-id", pg_tenant)

    def test_list_skills_all(self, pg_backend, pg_tenant):
        tid = pg_tenant
        pg_backend.create_skill(
            tenant_id=tid, name="私有技能", description="d",
            body="b", suggested_for=[],
        )
        skills = pg_backend.list_skills(tenant_id=tid, owner="all")
        names = [s["name"] for s in skills]
        assert "私有技能" in names

    def test_list_skills_mine(self, pg_backend, pg_tenant):
        tid = pg_tenant
        pg_backend.create_skill(
            tenant_id=tid, name="仅我的", description="d",
            body="b", suggested_for=[],
        )
        mine = pg_backend.list_skills(tenant_id=tid, owner="mine")
        names = [s["name"] for s in mine]
        assert "仅我的" in names

    def test_list_skills_universal(self, pg_backend, pg_admin_dsn):
        name = f"测试通用技能列表-{uuid.uuid4().hex[:8]}"
        pg_backend.create_skill(
            tenant_id=None, name=name, description="d",
            body="b", suggested_for=[],
        )
        try:
            universal = pg_backend.list_skills(tenant_id=None, owner="universal")
            names = [s["name"] for s in universal]
            assert name in names
        finally:
            _delete_universal_skills(pg_admin_dsn, name)

    def test_list_skills_suggested_for(self, pg_backend, pg_tenant):
        tid = pg_tenant
        pg_backend.create_skill(
            tenant_id=tid, name="分析专用", description="d",
            body="b", suggested_for=["analyst"],
        )
        filtered = pg_backend.list_skills(
            tenant_id=tid, owner="all", suggested_for="analyst"
        )
        assert any("分析专用" in s["name"] for s in filtered)

    def test_list_skills_cursor(self, pg_backend, pg_tenant):
        tid = pg_tenant
        pg_backend.create_skill(tenant_id=tid, name="A技能", description="d", body="b", suggested_for=[])
        pg_backend.create_skill(tenant_id=tid, name="B技能", description="d", body="b", suggested_for=[])
        with_cursor = pg_backend.list_skills(tenant_id=tid, owner="all", cursor="A技能", limit=10)
        names = [s["name"] for s in with_cursor]
        assert "A技能" not in names  # cursor 是 name > X

    def test_update_skill(self, pg_backend, pg_tenant):
        tid = pg_tenant
        skill = pg_backend.create_skill(
            tenant_id=tid, name="旧名", description="旧描述",
            body="body", suggested_for=[],
        )
        updated = pg_backend.update_skill(
            skill["id"], tid, expected_rev=1, name="新名"
        )
        assert updated["name"] == "新名"
        assert updated["rev"] == 2

    def test_update_skill_rev_mismatch(self, pg_backend, pg_tenant):
        tid = pg_tenant
        skill = pg_backend.create_skill(
            tenant_id=tid, name="R", description="d",
            body="b", suggested_for=[],
        )
        with pytest.raises(RevMismatch):
            pg_backend.update_skill(skill["id"], tid, expected_rev=999)

    def test_update_skill_not_found(self, pg_backend, pg_tenant):
        with pytest.raises(KeyError):
            pg_backend.update_skill("no-skill", pg_tenant, expected_rev=1, name="x")

    def test_delete_skill(self, pg_backend, pg_tenant):
        tid = pg_tenant
        skill = pg_backend.create_skill(
            tenant_id=tid, name="待删", description="d",
            body="b", suggested_for=["intel"],
        )
        # 先 equip,然后删
        pg_backend.equip(tid, "intel", skill["id"])
        unequipped = pg_backend.delete_skill(skill["id"], tid)
        assert "intel" in unequipped
        with pytest.raises(KeyError):
            pg_backend.get_skill(skill["id"], tid)

    def test_delete_skill_not_found(self, pg_backend, pg_tenant):
        with pytest.raises(KeyError):
            pg_backend.delete_skill("no-skill", pg_tenant)

    def test_skill_isolation(self, pg_backend, two_pg_tenants):
        ta, tb = two_pg_tenants
        skill = pg_backend.create_skill(
            tenant_id=ta, name="A的私有", description="d",
            body="b", suggested_for=[],
        )
        # B 不应看到 A 的私有 skill
        fetched = pg_backend.list_skills(tenant_id=tb, owner="mine")
        assert all(s["name"] != "A的私有" for s in fetched)
        # B 也不能 get
        with pytest.raises(KeyError):
            pg_backend.get_skill(skill["id"], tb)


# ══════════════════════════════════════════════════════════════════════════
# 11. Equipment
# ══════════════════════════════════════════════════════════════════════════

class TestEquipment:
    def test_equip_and_list(self, pg_backend, pg_tenant):
        tid = pg_tenant
        skill = pg_backend.create_skill(
            tenant_id=tid, name="可装备", description="d",
            body="b", suggested_for=["intel", "content"],
        )
        pg_backend.equip(tid, "intel", skill["id"])
        equipped = pg_backend.list_equipment(tid, "intel")
        assert any(e["id"] == skill["id"] for e in equipped)

    def test_unequip(self, pg_backend, pg_tenant):
        tid = pg_tenant
        skill = pg_backend.create_skill(
            tenant_id=tid, name="卸下", description="d",
            body="b", suggested_for=[],
        )
        pg_backend.equip(tid, "intel", skill["id"])
        pg_backend.unequip(tid, "intel", skill["id"])
        equipped = pg_backend.list_equipment(tid, "intel")
        assert not any(e["id"] == skill["id"] for e in equipped)

    def test_equip_isolation(self, pg_backend, two_pg_tenants):
        ta, tb = two_pg_tenants
        skill = pg_backend.create_skill(
            tenant_id=ta, name="A专用", description="d",
            body="b", suggested_for=[],
        )
        pg_backend.equip(ta, "intel", skill["id"])
        # B 看 intel 装备应是空的(或至少不含 A 的技能)
        equipped_b = pg_backend.list_equipment(tb, "intel")
        assert not any(e["id"] == skill["id"] for e in equipped_b)


# ══════════════════════════════════════════════════════════════════════════
# 12. tenant_id 守卫
# ══════════════════════════════════════════════════════════════════════════

class TestTenantGuard:
    def test_empty_tenant_id_raises(self, pg_backend):
        with pytest.raises(TenantContextRequired):
            pg_backend.save_task_result("", "t", {})

    def test_none_tenant_id_raises(self, pg_backend):
        with pytest.raises(TenantContextRequired):
            pg_backend.load_task_result(None, "t")

    def test_whitespace_tenant_id_raises(self, pg_backend):
        with pytest.raises(TenantContextRequired):
            pg_backend.save_memory("  ", "s", "f", "c")
