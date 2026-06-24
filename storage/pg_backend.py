"""StorageBackend Protocol 的 PostgreSQL 实现。

5 条纪律必看:
- 所有 SQL 经过 db.session.get_rls_cursor(),不直连
- WHERE tenant_id 显式 + RLS policy 双保险
- SET LOCAL(get_rls_cursor 内部保证)
- 纯 SQL,无 ORM
- PG 16 兼容
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import uuid
from typing import Any, Optional

import pandas as pd

from storage.base import RevMismatch, _require_tenant
from db.session import get_rls_cursor, init_pool


import math


def _json_safe(obj: Any) -> Any:
    """Replace NaN / NaT with None so json.dumps produces valid JSON."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float) and math.isnan(obj):
        return None
    return obj


# universal 池写操作占位 tenant_id(实际 RLS 不检查此值,is_admin=true 时放行)
_SYSTEM_TENANT = "00000000-0000-0000-0000-000000000000"


class PgBackend:
    def __init__(self):
        # 启动时初始化连接池;init_pool 幂等
        init_pool()

    # ── 任务结果 ────────────────────────────────────────────────────────

    def save_task_result(self, tenant_id: str, task_id: str, result: dict) -> None:
        _require_tenant(tenant_id)
        with get_rls_cursor(tenant_id) as cur:
            cur.execute(
                """INSERT INTO task_results(task_id, tenant_id, data)
                   VALUES (%s, %s, %s::jsonb)
                   ON CONFLICT (task_id) DO UPDATE SET
                       data = EXCLUDED.data,
                       created_at = now()""",
                (task_id, tenant_id, json.dumps(result, default=str))
            )

    def load_task_result(self, tenant_id: str, task_id: str) -> Optional[dict]:
        _require_tenant(tenant_id)
        with get_rls_cursor(tenant_id) as cur:
            cur.execute(
                "SELECT data FROM task_results WHERE tenant_id = %s AND task_id = %s",
                (tenant_id, task_id)
            )
            row = cur.fetchone()
        return row[0] if row else None

    # ── Memory ──────────────────────────────────────────────────────────

    def load_memory(self, tenant_id: str, scope: str, file: str) -> Optional[str]:
        _require_tenant(tenant_id)
        with get_rls_cursor(tenant_id) as cur:
            cur.execute(
                "SELECT body FROM agent_memory WHERE tenant_id = %s AND scope = %s AND file = %s AND entry_id = ''",
                (tenant_id, scope, file)
            )
            row = cur.fetchone()
        return row[0] if row else None

    def save_memory(self, tenant_id: str, scope: str, file: str, content: str) -> None:
        _require_tenant(tenant_id)
        with get_rls_cursor(tenant_id) as cur:
            cur.execute(
                """INSERT INTO agent_memory(tenant_id, scope, file, entry_id, body)
                   VALUES (%s, %s, %s, '', %s)
                   ON CONFLICT (tenant_id, scope, file, entry_id) DO UPDATE SET
                       body = EXCLUDED.body,
                       rev = agent_memory.rev + 1,
                       updated_at = now()""",
                (tenant_id, scope, file, content)
            )

    # ── 采集数据 ─────────────────────────────────────────────────────────

    def list_collected_data(self, tenant_id: str, since: datetime,
                            goal_id: Optional[str] = None) -> pd.DataFrame:
        _require_tenant(tenant_id)
        with get_rls_cursor(tenant_id) as cur:
            base_sql = (
                """SELECT note_id, goal_id, keyword, title, author, likes,
                          comments_count, shares, collects, ces_score, raw, collected_at
                   FROM collected_notes
                   WHERE tenant_id = %s AND collected_at >= %s"""
            )
            params: list[Any] = [tenant_id, since]
            if goal_id is not None:
                base_sql += " AND goal_id = %s"
                params.append(goal_id)
            base_sql += " ORDER BY collected_at DESC"
            cur.execute(base_sql, params)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
        return pd.DataFrame(rows, columns=cols)

    def save_collected_data(self, tenant_id: str, source: str,
                            df: pd.DataFrame, meta: Optional[dict] = None) -> str:
        _require_tenant(tenant_id)
        if df.empty:
            return ""
        from psycopg2.extras import execute_values
        batch_id = f"batch-{uuid.uuid4().hex[:12]}"
        rows = []
        for _, r in df.iterrows():
            note_id = str(r.get("note_id") or r.get("id") or uuid.uuid4())
            rows.append((
                note_id, tenant_id,
                r.get("goal_id"), r.get("keyword"), r.get("title"), r.get("author"),
                int(r.get("likes", 0) or 0),
                int(r.get("comments_count", 0) or r.get("comments", 0) or 0),
                int(r.get("shares", 0) or 0),
                int(r.get("collects", 0) or 0),
                float(r.get("ces_score", 0) or 0),
                json.dumps(_json_safe(r.to_dict()), default=str),
            ))
        with get_rls_cursor(tenant_id) as cur:
            execute_values(
                cur,
                """INSERT INTO collected_notes(
                       note_id, tenant_id, goal_id, keyword, title, author,
                       likes, comments_count, shares, collects, ces_score, raw)
                   VALUES %s
                   ON CONFLICT (tenant_id, note_id) DO UPDATE SET
                       likes = EXCLUDED.likes,
                       comments_count = EXCLUDED.comments_count,
                       raw = EXCLUDED.raw""",
                rows,
                template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)"
            )
        return batch_id

    # ── 热词 ─────────────────────────────────────────────────────────────

    def list_hot_keywords(self, tenant_id: str, since: datetime) -> pd.DataFrame:
        _require_tenant(tenant_id)
        with get_rls_cursor(tenant_id) as cur:
            cur.execute(
                """SELECT hot_id, keyword, score, raw, captured_at
                   FROM hot_keywords
                   WHERE tenant_id = %s AND captured_at >= %s
                   ORDER BY captured_at DESC""",
                (tenant_id, since)
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
        return pd.DataFrame(rows, columns=cols)

    def save_hot_keywords(self, tenant_id: str, df: pd.DataFrame) -> str:
        _require_tenant(tenant_id)
        if df.empty:
            return ""
        from psycopg2.extras import execute_values
        batch_id = f"hot-{uuid.uuid4().hex[:12]}"
        rows = []
        for _, r in df.iterrows():
            hot_id = str(r.get("hot_id") or uuid.uuid4())
            keyword = str(r.get("keyword", ""))
            score = float(r.get("score", 0) or 0)
            raw = json.dumps(_json_safe(r.to_dict()), default=str)
            rows.append((hot_id, tenant_id, keyword, score, raw))
        with get_rls_cursor(tenant_id) as cur:
            execute_values(
                cur,
                """INSERT INTO hot_keywords(hot_id, tenant_id, keyword, score, raw)
                   VALUES %s
                   ON CONFLICT (hot_id) DO UPDATE SET
                       score = EXCLUDED.score,
                       raw = EXCLUDED.raw""",
                rows,
                template="(%s,%s,%s,%s::numeric,%s::jsonb)"
            )
        return batch_id

    # ── 生成内容 ─────────────────────────────────────────────────────────

    def list_generated_posts(self, tenant_id: str,
                             since: Optional[datetime] = None,
                             topic_id: Optional[str] = None,
                             strategy_id: Optional[str] = None,
                             calendar_item_id: Optional[str] = None,
                             status: Optional[str] = None) -> pd.DataFrame:
        _require_tenant(tenant_id)
        where = ["tenant_id = %s"]
        params: list[Any] = [tenant_id]
        if since:
            where.append("created_at >= %s"); params.append(since)
        if topic_id:
            where.append("topic_id = %s"); params.append(topic_id)
        if strategy_id:
            where.append("strategy_id = %s"); params.append(strategy_id)
        if calendar_item_id:
            where.append("calendar_item_id = %s"); params.append(calendar_item_id)
        if status:
            where.append("status = %s"); params.append(status)
        w = " AND ".join(where)

        with get_rls_cursor(tenant_id) as cur:
            cur.execute(
                f"""SELECT content_id, goal_id, persona_id, title, body, hashtags,
                           publish_at, status, meta, created_at, updated_at,
                           topic_id, strategy_id, calendar_item_id,
                           knowledge_refs, memory_refs, rev
                    FROM generated_content
                    WHERE {w}
                    ORDER BY created_at DESC""",
                tuple(params)
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
        return pd.DataFrame(rows, columns=cols)

    def save_generated_posts(self, tenant_id: str, df: pd.DataFrame,
                             meta: Optional[dict] = None) -> str:
        _require_tenant(tenant_id)
        if df.empty:
            return ""
        from psycopg2.extras import execute_values
        batch_id = f"gen-{uuid.uuid4().hex[:12]}"
        rows = []
        for _, r in df.iterrows():
            content_id = str(r.get("content_id") or uuid.uuid4())
            title = str(r.get("title", ""))
            body = str(r.get("body", ""))
            hashtags = list(r.get("hashtags") or [])
            goal_id = r.get("goal_id") if r.get("goal_id") else None
            persona_id = r.get("persona_id")
            publish_at = str(r.get("publish_at", ""))
            status = str(r.get("status", "draft"))
            # 新 lifecycle 字段
            topic_id = r.get("topic_id") or None
            strategy_id = r.get("strategy_id") or None
            cal_item_id = r.get("calendar_item_id") or None
            knowledge_refs = json.dumps(r.get("knowledge_refs") or [])
            memory_refs = json.dumps(r.get("memory_refs") or [])
            # 剩余字段进 meta
            post_meta = {k: v for k, v in r.to_dict().items()
                         if k not in ("content_id", "title", "body", "hashtags",
                                      "goal_id", "persona_id", "publish_at", "status",
                                      "topic_id", "strategy_id", "calendar_item_id",
                                      "knowledge_refs", "memory_refs")}
            if meta:
                post_meta["_batch_meta"] = meta
            rows.append((content_id, tenant_id, goal_id, persona_id, title, body,
                         hashtags, publish_at, status,
                         topic_id, strategy_id, cal_item_id,
                         knowledge_refs, memory_refs,
                         json.dumps(_json_safe(post_meta), default=str)))
        with get_rls_cursor(tenant_id) as cur:
            execute_values(
                cur,
                """INSERT INTO generated_content(
                       content_id, tenant_id, goal_id, persona_id, title, body,
                       hashtags, publish_at, status,
                       topic_id, strategy_id, calendar_item_id,
                       knowledge_refs, memory_refs, meta)
                   VALUES %s
                   ON CONFLICT (content_id) DO UPDATE SET
                       title = EXCLUDED.title,
                       body = EXCLUDED.body,
                       status = EXCLUDED.status,
                       topic_id = EXCLUDED.topic_id,
                       strategy_id = EXCLUDED.strategy_id,
                       calendar_item_id = EXCLUDED.calendar_item_id,
                       knowledge_refs = EXCLUDED.knowledge_refs,
                       memory_refs = EXCLUDED.memory_refs,
                       rev = generated_content.rev + 1,
                       updated_at = now()""",
                rows,
                template="(%s,%s,%s,%s,%s,%s,%s::text[],%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb)"
            )
        return batch_id

    def get_generated_post(self, tenant_id: str, content_id: str) -> Optional[dict]:
        _require_tenant(tenant_id)
        with get_rls_cursor(tenant_id) as cur:
            cur.execute(
                """SELECT content_id, tenant_id, goal_id, persona_id, title, body, hashtags,
                          publish_at, status, meta, created_at, updated_at,
                          topic_id, strategy_id, calendar_item_id,
                          knowledge_refs, memory_refs, rev
                   FROM generated_content
                   WHERE tenant_id = %s AND content_id = %s""",
                (tenant_id, content_id)
            )
            row = cur.fetchone()
        if row is None:
            return None
        cols = ["content_id", "tenant_id", "goal_id", "persona_id", "title", "body",
                "hashtags", "publish_at", "status", "meta", "created_at", "updated_at",
                "topic_id", "strategy_id", "calendar_item_id",
                "knowledge_refs", "memory_refs", "rev"]
        d = dict(zip(cols, row))
        d["tenant_id"] = str(d["tenant_id"])
        return d

    def update_generated_post(self, tenant_id: str, content_id: str, *,
                              expected_rev: int, **changes: Any) -> dict:
        _require_tenant(tenant_id)
        allowed = {"title", "body", "hashtags", "publish_at", "status",
                   "topic_id", "strategy_id", "calendar_item_id",
                   "knowledge_refs", "memory_refs", "meta"}
        jsonb_fields = {"knowledge_refs", "memory_refs", "meta"}
        sets, params = [], []
        for k, v in changes.items():
            if k in jsonb_fields:
                sets.append(f"{k} = %s::jsonb"); params.append(json.dumps(v or {} if k == "meta" else v or []))
            elif k == "hashtags":
                sets.append(f"{k} = %s::text[]"); params.append(list(v) if v else [])
            elif k in allowed:
                sets.append(f"{k} = %s"); params.append(v)
        sets.append("updated_at = now()")
        sets.append("rev = rev + 1")

        sql = f"""UPDATE generated_content SET {', '.join(sets)}
                  WHERE content_id = %s AND rev = %s AND tenant_id = %s
                  RETURNING content_id, tenant_id, goal_id, persona_id, title, body,
                            hashtags, publish_at, status, meta, created_at, updated_at,
                            topic_id, strategy_id, calendar_item_id,
                            knowledge_refs, memory_refs, rev"""
        params += [content_id, expected_rev, tenant_id]

        with get_rls_cursor(tenant_id) as cur:
            cur.execute(sql, tuple(params))
            row = cur.fetchone()
        if row is None:
            with get_rls_cursor(tenant_id) as cur:
                cur.execute(
                    "SELECT rev FROM generated_content WHERE tenant_id = %s AND content_id = %s",
                    (tenant_id, content_id)
                )
                r = cur.fetchone()
            if r is None:
                raise KeyError(f"generated_content '{content_id}' not found")
            raise RevMismatch(f"expected rev={expected_rev}, actual rev={r[0]}")
        cols = ["content_id", "tenant_id", "goal_id", "persona_id", "title", "body",
                "hashtags", "publish_at", "status", "meta", "created_at", "updated_at",
                "topic_id", "strategy_id", "calendar_item_id",
                "knowledge_refs", "memory_refs", "rev"]
        d = dict(zip(cols, row))
        d["tenant_id"] = str(d["tenant_id"])
        return d

    # ── 目标 ─────────────────────────────────────────────────────────────

    def load_goals(self, tenant_id: str) -> dict:
        _require_tenant(tenant_id)
        with get_rls_cursor(tenant_id) as cur:
            cur.execute(
                "SELECT data FROM goals WHERE tenant_id = %s",
                (tenant_id,)
            )
            rows = cur.fetchall()
        if not rows:
            return {"active_goal_id": "", "goals": []}
        # 合并多行:每个 goal 在 data 中有完整结构,_META 行存 active_goal_id
        goals_list = []
        active_id = ""
        for (data,) in rows:
            if isinstance(data, dict):
                if data.get("_meta"):
                    active_id = data.get("active_goal_id", "")
                else:
                    goals_list.append(data)
        return {"active_goal_id": active_id, "goals": goals_list}

    def save_goals(self, tenant_id: str, data: dict) -> None:
        _require_tenant(tenant_id)
        with get_rls_cursor(tenant_id) as cur:
            # 清空旧数据
            cur.execute("DELETE FROM goals WHERE tenant_id = %s", (tenant_id,))
            # 存 active_goal_id 元数据行
            cur.execute(
                "INSERT INTO goals(goal_id, tenant_id, data) VALUES (%s, %s, %s::jsonb)",
                (f"{tenant_id}:_META", tenant_id,
                 json.dumps({"_meta": True, "active_goal_id": data.get("active_goal_id", "")}))
            )
            # 存每条 goal
            for g in data.get("goals", []):
                gid = g.get("goal_id", "")
                if not gid:
                    gid = str(uuid.uuid4())
                cur.execute(
                    "INSERT INTO goals(goal_id, tenant_id, data) VALUES (%s, %s, %s::jsonb)",
                    (gid, tenant_id, json.dumps(g, default=str))
                )

    # ── 人设 ─────────────────────────────────────────────────────────────

    def load_persona(self, tenant_id: str) -> dict:
        _require_tenant(tenant_id)
        with get_rls_cursor(tenant_id) as cur:
            cur.execute(
                "SELECT persona_id, data, is_active FROM personas WHERE tenant_id = %s ORDER BY persona_id",
                (tenant_id,)
            )
            rows = cur.fetchall()
        if not rows:
            return {}
        # 重构与 LocalJsonBackend 兼容的格式
        result = {"active_id": None, "personas": []}
        for pid, data, is_active in rows:
            p = dict(data) if isinstance(data, dict) else {}
            p["persona_id"] = pid
            p["is_active"] = is_active
            result["personas"].append(p)
            if is_active:
                result["active_id"] = pid
        return result

    def save_persona(self, tenant_id: str, data: dict) -> None:
        _require_tenant(tenant_id)
        with get_rls_cursor(tenant_id) as cur:
            cur.execute("DELETE FROM personas WHERE tenant_id = %s", (tenant_id,))
            for p in data.get("personas", []):
                pid = p.get("persona_id", "")
                if not pid:
                    continue
                is_active = (pid == data.get("active_id"))
                p_data = {k: v for k, v in p.items() if k not in ("persona_id", "is_active")}
                cur.execute(
                    "INSERT INTO personas(persona_id, tenant_id, data, is_active) VALUES (%s, %s, %s::jsonb, %s)",
                    (pid, tenant_id, json.dumps(p_data, default=str), is_active)
                )

    # ── 审计 ─────────────────────────────────────────────────────────────

    def save_audit_log(self, tenant_id: str, entry: dict) -> None:
        _require_tenant(tenant_id)
        kind = entry.get("kind", "unknown")
        with get_rls_cursor(tenant_id) as cur:
            cur.execute(
                "INSERT INTO audit_log(tenant_id, kind, data) VALUES (%s, %s, %s::jsonb)",
                (tenant_id, kind, json.dumps({**entry, "_ts": datetime.now(timezone.utc).isoformat()}, default=str))
            )

    # ── 数据生命周期 ─────────────────────────────────────────────────────

    def cleanup_old_data(self, tenant_id: str, days: int) -> list[str]:
        _require_tenant(tenant_id)
        table_config = {
            "collected_notes":     {"pk": "note_id",    "ts": "collected_at"},
            "hot_keywords":        {"pk": "hot_id",     "ts": "captured_at"},
            "generated_content":   {"pk": "content_id", "ts": "updated_at"},
        }
        deleted: list[str] = []
        with get_rls_cursor(tenant_id) as cur:
            for table, cfg in table_config.items():
                cur.execute(
                    f"""DELETE FROM {table}
                        WHERE tenant_id = %s AND {cfg["ts"]} < now() - (%s || ' days')::interval
                        RETURNING {cfg["pk"]}""",
                    (tenant_id, str(days))
                )
                deleted.extend(str(r[0]) for r in cur.fetchall())
        return deleted

    # ── Insight Evidence Pool (P2) ──────────────────────────────────────

    def list_evidence(self, tenant_id: str, *,
                      angle: str | None = None,
                      funnel_stage: str | None = None,
                      limit: int = 3) -> list[dict]:
        _require_tenant(tenant_id)
        with get_rls_cursor(tenant_id) as cur:
            sql = (
                """SELECT evidence_id, tenant_id, source_note_id,
                          angle, funnel_stage, hook, key_insight,
                          ces_score, extracted_at, raw
                   FROM content_evidence
                   WHERE tenant_id = %s"""
            )
            params: list[Any] = [tenant_id]
            if angle is not None:
                sql += " AND angle = %s"
                params.append(angle)
            if funnel_stage is not None:
                sql += " AND funnel_stage = %s"
                params.append(funnel_stage)
            # ranking: 同 funnel 优先 → 同 angle 次之 → ces_score DESC NULLS LAST
            sql += (
                """ ORDER BY (funnel_stage = %s)::int DESC,
                           (angle = %s)::int DESC,
                           ces_score DESC NULLS LAST
                   LIMIT %s"""
            )
            params.extend([funnel_stage or "", angle or "", limit])
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        return [dict(zip(cols, r)) for r in rows]

    def upsert_evidence(self, tenant_id: str, evidence: dict) -> dict:
        _require_tenant(tenant_id)
        source_note_id = evidence.get("source_note_id", "")
        if not source_note_id:
            raise ValueError("source_note_id is required")
        evidence_id = evidence.get("evidence_id") or f"{tenant_id}:{source_note_id}"
        with get_rls_cursor(tenant_id) as cur:
            cur.execute(
                """INSERT INTO content_evidence(
                       evidence_id, tenant_id, source_note_id,
                       angle, funnel_stage, hook, key_insight,
                       ces_score, extracted_at, raw)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                   ON CONFLICT (tenant_id, source_note_id) DO UPDATE SET
                       evidence_id  = EXCLUDED.evidence_id,
                       angle        = EXCLUDED.angle,
                       funnel_stage = EXCLUDED.funnel_stage,
                       hook         = EXCLUDED.hook,
                       key_insight  = EXCLUDED.key_insight,
                       ces_score    = EXCLUDED.ces_score,
                       extracted_at = EXCLUDED.extracted_at,
                       raw          = EXCLUDED.raw""",
                (
                    evidence_id, tenant_id, source_note_id,
                    evidence.get("angle"),
                    evidence.get("funnel_stage"),
                    evidence.get("hook"),
                    evidence.get("key_insight"),
                    evidence.get("ces_score"),
                    evidence.get("extracted_at"),
                    json.dumps(evidence.get("raw", {}), default=str),
                )
            )
            # 读回
            cur.execute(
                """SELECT evidence_id, tenant_id, source_note_id,
                          angle, funnel_stage, hook, key_insight,
                          ces_score, extracted_at, raw
                   FROM content_evidence
                   WHERE tenant_id = %s AND source_note_id = %s""",
                (tenant_id, source_note_id)
            )
            cols = [d[0] for d in cur.description]
            row = cur.fetchone()
        return dict(zip(cols, row)) if row else {"evidence_id": evidence_id}

    # ── Skills Hub ──────────────────────────────────────────────────────

    def list_skills(self, *, tenant_id: Optional[str] = None,
                    owner: str = "all",
                    suggested_for: Optional[str] = None,
                    limit: int = 20,
                    cursor: Optional[str] = None) -> list[dict]:
        # 即使只读 universal 池也需要一个 tenant_id 来 SET LOCAL
        tid = tenant_id or _SYSTEM_TENANT

        where_clauses = []
        params = []

        if owner == "universal":
            where_clauses.append("tenant_id IS NULL")
        elif owner == "mine":
            where_clauses.append("tenant_id = %s::uuid")
            params.append(tid)
        else:  # all
            where_clauses.append("(tenant_id IS NULL OR tenant_id = %s::uuid)")
            params.append(tid)

        if suggested_for:
            where_clauses.append("%s = ANY(suggested_for)")
            params.append(suggested_for)

        if cursor:
            where_clauses.append("name > %s")
            params.append(cursor)

        sql = f"""SELECT skill_id, tenant_id, name, description, version,
                         suggested_for, allowed_tools, license, body,
                         source_skill_id, status, rev, created_at, updated_at
                  FROM skills
                  WHERE {' AND '.join(where_clauses)}
                  ORDER BY name
                  LIMIT %s"""
        params.append(limit)

        with get_rls_cursor(tid) as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]

        result = []
        for r in rows:
            d = dict(zip(cols, r))
            d["id"] = d.pop("skill_id")
            d["tenant_id"] = str(d["tenant_id"]) if d["tenant_id"] else None
            result.append(d)
        return result

    def get_skill(self, skill_id: str, tenant_id: str) -> dict:
        _require_tenant(tenant_id)
        with get_rls_cursor(tenant_id) as cur:
            cur.execute(
                """SELECT skill_id, tenant_id, name, description, version,
                          suggested_for, allowed_tools, license, body,
                          source_skill_id, status, rev, created_at, updated_at
                   FROM skills
                   WHERE skill_id = %s AND (tenant_id = %s::uuid OR tenant_id IS NULL)""",
                (skill_id, tenant_id)
            )
            row = cur.fetchone()
        if row is None:
            raise KeyError(f"skill '{skill_id}' not found")
        cols = ["skill_id", "tenant_id", "name", "description", "version",
                "suggested_for", "allowed_tools", "license", "body",
                "source_skill_id", "status", "rev", "created_at", "updated_at"]
        d = dict(zip(cols, row))
        d["id"] = d.pop("skill_id")
        d["tenant_id"] = str(d["tenant_id"]) if d["tenant_id"] else None
        return d

    def create_skill(self, *, tenant_id: Optional[str], name: str,
                     description: str, body: str,
                     suggested_for: list[str],
                     source_skill_id: Optional[str] = None,
                     extras: dict[str, str] = {}) -> dict:
        skill_id = str(uuid.uuid4())

        # universal:tenant_id=None,走 admin 旁路;否则普通租户路径
        cursor_tid = tenant_id or _SYSTEM_TENANT
        is_admin = tenant_id is None

        with get_rls_cursor(cursor_tid, is_admin=is_admin) as cur:
            cur.execute(
                """INSERT INTO skills(
                       skill_id, tenant_id, name, description, body,
                       suggested_for, source_skill_id, status, rev)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 'active', 1)
                   RETURNING skill_id, tenant_id, name, description, body,
                             suggested_for, allowed_tools, license, version,
                             source_skill_id, status, rev, created_at, updated_at""",
                (skill_id, tenant_id, name, description, body,
                 suggested_for, source_skill_id)
            )
            row = cur.fetchone()
            cols = [d[0] for d in cur.description]

        d = dict(zip(cols, row))
        d["id"] = d.pop("skill_id")
        d["tenant_id"] = str(d["tenant_id"]) if d["tenant_id"] else None
        return d

    def update_skill(self, skill_id: str, tenant_id: str, *,
                     expected_rev: int, **changes) -> dict:
        _require_tenant(tenant_id)

        allowed = {"name", "description", "body", "suggested_for",
                   "allowed_tools", "license", "version", "status"}
        sets, params = [], []
        for k, v in changes.items():
            if k in allowed:
                sets.append(f"{k} = %s")
                params.append(v)
        if not sets:
            # no field changes: touch updated_at to trigger OCC check without bumping rev
            sets.append("updated_at = now()")

        if "updated_at" not in str(sets):
            sets.append("updated_at = now()")
        sets.append("rev = rev + 1")

        sql = f"""UPDATE skills SET {', '.join(sets)}
                  WHERE skill_id = %s AND rev = %s
                    AND (tenant_id = %s::uuid OR tenant_id IS NULL)
                  RETURNING skill_id, tenant_id, name, description, body,
                            suggested_for, allowed_tools, license, version,
                            source_skill_id, status, rev, created_at, updated_at"""
        params.extend([skill_id, expected_rev, tenant_id])

        with get_rls_cursor(tenant_id) as cur:
            cur.execute(sql, tuple(params))
            row = cur.fetchone()

        if row is None:
            with get_rls_cursor(tenant_id) as cur:
                cur.execute("SELECT rev FROM skills WHERE skill_id = %s", (skill_id,))
                r = cur.fetchone()
                if r is None:
                    raise KeyError(f"skill '{skill_id}' not found")
                raise RevMismatch(f"expected rev={expected_rev}, actual rev={r[0]}")

        cols = ["skill_id", "tenant_id", "name", "description", "body",
                "suggested_for", "allowed_tools", "license", "version",
                "source_skill_id", "status", "rev", "created_at", "updated_at"]
        d = dict(zip(cols, row))
        d["id"] = d.pop("skill_id")
        d["tenant_id"] = str(d["tenant_id"]) if d["tenant_id"] else None
        return d

    def delete_skill(self, skill_id: str, tenant_id: str) -> list[str]:
        _require_tenant(tenant_id)

        # 先查出被级联 unequip 的 role
        unequipped: list[str] = []
        with get_rls_cursor(tenant_id) as cur:
            cur.execute(
                "DELETE FROM agent_equipment WHERE tenant_id = %s AND skill_id = %s RETURNING role",
                (tenant_id, skill_id)
            )
            for row in cur.fetchall():
                unequipped.append(row[0])

            cur.execute(
                "DELETE FROM skills WHERE skill_id = %s AND (tenant_id = %s::uuid OR tenant_id IS NULL)",
                (skill_id, tenant_id)
            )
            if cur.rowcount == 0:
                raise KeyError(f"skill '{skill_id}' not found")

        return unequipped

    def list_equipment(self, tenant_id: str, agent_role: str) -> list[dict]:
        _require_tenant(tenant_id)
        with get_rls_cursor(tenant_id) as cur:
            cur.execute(
                """SELECT s.skill_id, s.name, s.description, s.suggested_for,
                          s.source_skill_id, s.version, s.rev, s.tenant_id
                   FROM agent_equipment ae
                   JOIN skills s ON s.skill_id = ae.skill_id
                   WHERE ae.tenant_id = %s AND ae.role = %s
                   ORDER BY s.name""",
                (tenant_id, agent_role)
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]

        results = []
        for r in rows:
            d = dict(zip(cols, r))
            d["id"] = d.pop("skill_id")
            d["owner"] = "universal" if d["tenant_id"] is None else "mine"
            d.pop("tenant_id")
            results.append(d)
        return results

    def equip(self, tenant_id: str, agent_role: str, skill_id: str) -> None:
        _require_tenant(tenant_id)
        with get_rls_cursor(tenant_id) as cur:
            cur.execute(
                "INSERT INTO agent_equipment(tenant_id, role, skill_id) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (tenant_id, agent_role, skill_id)
            )

    def unequip(self, tenant_id: str, agent_role: str, skill_id: str) -> None:
        _require_tenant(tenant_id)
        with get_rls_cursor(tenant_id) as cur:
            cur.execute(
                "DELETE FROM agent_equipment WHERE tenant_id = %s AND role = %s AND skill_id = %s",
                (tenant_id, agent_role, skill_id)
            )

    # ── 内容选题 (topics) ─────────────────────────────────────────────────

    _TOPIC_COLS = [
        "topic_id", "tenant_id", "goal_id", "persona_id", "title", "angle",
        "funnel_stage", "source", "source_refs", "status", "created_by",
        "rev", "created_at", "updated_at"
    ]

    def list_topics(self, tenant_id: str, *,
                    goal_id: Optional[str] = None,
                    status: Optional[str] = None,
                    page: int = 1,
                    page_size: int = 20,
                    sort: str = "-updated_at") -> dict:
        _require_tenant(tenant_id)
        _allowed_sort = {"title", "created_at", "updated_at", "status"}
        direction = "DESC" if sort.startswith("-") else "ASC"
        field = sort.lstrip("-")
        if field not in _allowed_sort:
            field, direction = "updated_at", "DESC"

        where = ["tenant_id = %s"]
        params: list[Any] = [tenant_id]
        if goal_id:
            where.append("goal_id = %s"); params.append(goal_id)
        if status:
            where.append("status = %s"); params.append(status)

        w = " AND ".join(where)
        offset = (page - 1) * page_size
        limit = min(page_size, 100)

        with get_rls_cursor(tenant_id) as cur:
            cur.execute(f"SELECT count(*) FROM topics WHERE {w}", tuple(params))
            total = cur.fetchone()[0]

            cols_sql = ", ".join(self._TOPIC_COLS)
            cur.execute(
                f"SELECT {cols_sql} FROM topics WHERE {w} ORDER BY {field} {direction} LIMIT %s OFFSET %s",
                tuple(params + [limit, offset])
            )
            rows = cur.fetchall()
            col_names = [d[0] for d in cur.description]

        items = [dict(zip(col_names, r)) for r in rows]
        for it in items:
            it["tenant_id"] = str(it["tenant_id"])
        return {
            "items": items,
            "total": total if total <= 10000 else None,
            "page": page,
            "page_size": limit,
            "has_more": (page * limit) < total
        }

    def get_topic(self, tenant_id: str, topic_id: str) -> dict:
        _require_tenant(tenant_id)
        cols = ", ".join(self._TOPIC_COLS)
        with get_rls_cursor(tenant_id) as cur:
            cur.execute(
                f"SELECT {cols} FROM topics WHERE tenant_id = %s AND topic_id = %s",
                (tenant_id, topic_id)
            )
            row = cur.fetchone()
        if row is None:
            raise KeyError(f"topic '{topic_id}' not found")
        d = dict(zip(self._TOPIC_COLS, row))
        d["tenant_id"] = str(d["tenant_id"])
        return d

    def create_topic(self, tenant_id: str, *,
                     title: str,
                     goal_id: Optional[str] = None,
                     persona_id: Optional[str] = None,
                     angle: Optional[str] = None,
                     funnel_stage: Optional[str] = None,
                     source: str = "manual",
                     source_refs: Optional[list] = None,
                     status: str = "idea",
                     created_by: str = "user") -> dict:
        _require_tenant(tenant_id)
        import uuid as _uuid
        topic_id = f"topic_{_uuid.uuid4().hex[:12]}"
        src = json.dumps(source_refs or [])

        cols = ", ".join(self._TOPIC_COLS)
        with get_rls_cursor(tenant_id) as cur:
            cur.execute(
                f"""INSERT INTO topics(topic_id, tenant_id, goal_id, persona_id, title,
                                       angle, funnel_stage, source, source_refs,
                                       status, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                    RETURNING {cols}""",
                (topic_id, tenant_id, goal_id, persona_id, title,
                 angle, funnel_stage, source, src, status, created_by)
            )
            row = cur.fetchone()
        d = dict(zip(self._TOPIC_COLS, row))
        d["tenant_id"] = str(d["tenant_id"])
        return d

    def update_topic(self, tenant_id: str, topic_id: str, *,
                     expected_rev: int, **changes: Any) -> dict:
        _require_tenant(tenant_id)
        allowed = {"goal_id", "persona_id", "title", "angle", "funnel_stage",
                   "source", "source_refs", "status", "created_by"}
        sets, params = [], []
        for k, v in changes.items():
            if k == "source_refs":
                sets.append(f"{k} = %s::jsonb"); params.append(json.dumps(v or []))
            elif k in allowed:
                sets.append(f"{k} = %s"); params.append(v)
        sets.append("updated_at = now()")
        sets.append("rev = rev + 1")

        cols = ", ".join(self._TOPIC_COLS)
        sql = f"""UPDATE topics SET {', '.join(sets)}
                  WHERE topic_id = %s AND rev = %s AND tenant_id = %s
                  RETURNING {cols}"""
        params += [topic_id, expected_rev, tenant_id]

        with get_rls_cursor(tenant_id) as cur:
            cur.execute(sql, tuple(params))
            row = cur.fetchone()
        if row is None:
            with get_rls_cursor(tenant_id) as cur:
                cur.execute(
                    "SELECT rev FROM topics WHERE tenant_id = %s AND topic_id = %s",
                    (tenant_id, topic_id)
                )
                r = cur.fetchone()
            if r is None:
                raise KeyError(f"topic '{topic_id}' not found")
            raise RevMismatch(f"expected rev={expected_rev}, actual rev={r[0]}")
        d = dict(zip(self._TOPIC_COLS, row))
        d["tenant_id"] = str(d["tenant_id"])
        return d

    def delete_topic(self, tenant_id: str, topic_id: str, expected_rev: int) -> dict:
        """Archive a topic (set status='archived')."""
        return self.update_topic(tenant_id, topic_id, expected_rev=expected_rev, status="archived")

    # ── 内容日历 (calendar_items) ──────────────────────────────────────────

    _CALENDAR_COLS = [
        "calendar_item_id", "tenant_id", "topic_id", "content_id",
        "scheduled_date", "scheduled_time", "funnel_stage", "status",
        "delete_mode", "deleted_at", "created_by", "rev", "created_at", "updated_at"
    ]

    def list_calendar_items(self, tenant_id: str, *,
                            date_from: Optional[str] = None,
                            date_to: Optional[str] = None,
                            status: Optional[str] = None,
                            include_deleted: bool = False,
                            page: int = 1,
                            page_size: int = 20,
                            sort: str = "scheduled_date") -> dict:
        _require_tenant(tenant_id)
        _allowed_sort = {"scheduled_date", "updated_at", "status"}
        direction = "DESC" if sort.startswith("-") else "ASC"
        field = sort.lstrip("-")
        if field not in _allowed_sort:
            field, direction = "scheduled_date", "ASC"

        where = ["tenant_id = %s"]
        params: list[Any] = [tenant_id]
        if not include_deleted:
            where.append("deleted_at IS NULL")
        if date_from:
            where.append("scheduled_date >= %s"); params.append(date_from)
        if date_to:
            where.append("scheduled_date <= %s"); params.append(date_to)
        if status:
            where.append("status = %s"); params.append(status)

        w = " AND ".join(where)
        offset = (page - 1) * page_size
        limit = min(page_size, 100)

        with get_rls_cursor(tenant_id) as cur:
            cur.execute(f"SELECT count(*) FROM calendar_items WHERE {w}", tuple(params))
            total = cur.fetchone()[0]

            cols_sql = ", ".join(self._CALENDAR_COLS)
            cur.execute(
                f"SELECT {cols_sql} FROM calendar_items WHERE {w} ORDER BY {field} {direction} LIMIT %s OFFSET %s",
                tuple(params + [limit, offset])
            )
            rows = cur.fetchall()
            col_names = [d[0] for d in cur.description]

        items = [dict(zip(col_names, r)) for r in rows]
        for it in items:
            it["tenant_id"] = str(it["tenant_id"])
        return {
            "items": items,
            "total": total if total <= 10000 else None,
            "page": page,
            "page_size": limit,
            "has_more": (page * limit) < total
        }

    def get_calendar_item(self, tenant_id: str, calendar_item_id: str) -> dict:
        _require_tenant(tenant_id)
        cols = ", ".join(self._CALENDAR_COLS)
        with get_rls_cursor(tenant_id) as cur:
            cur.execute(
                f"SELECT {cols} FROM calendar_items WHERE tenant_id = %s AND calendar_item_id = %s",
                (tenant_id, calendar_item_id)
            )
            row = cur.fetchone()
        if row is None:
            raise KeyError(f"calendar_item '{calendar_item_id}' not found")
        d = dict(zip(self._CALENDAR_COLS, row))
        d["tenant_id"] = str(d["tenant_id"])
        return d

    def create_calendar_item(self, tenant_id: str, *,
                             scheduled_date: str,
                             scheduled_time: Optional[str] = None,
                             topic_id: Optional[str] = None,
                             funnel_stage: Optional[str] = None,
                             content_id: Optional[str] = None,
                             created_by: str = "user") -> dict:
        _require_tenant(tenant_id)
        import uuid as _uuid
        cid = f"cal_{_uuid.uuid4().hex[:12]}"

        cols = ", ".join(self._CALENDAR_COLS)
        with get_rls_cursor(tenant_id) as cur:
            cur.execute(
                f"""INSERT INTO calendar_items(calendar_item_id, tenant_id, topic_id, content_id,
                                               scheduled_date, scheduled_time, funnel_stage, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING {cols}""",
                (cid, tenant_id, topic_id, content_id,
                 scheduled_date, scheduled_time, funnel_stage, created_by)
            )
            row = cur.fetchone()
        d = dict(zip(self._CALENDAR_COLS, row))
        d["tenant_id"] = str(d["tenant_id"])
        return d

    def update_calendar_item(self, tenant_id: str, calendar_item_id: str, *,
                             expected_rev: int, **changes: Any) -> dict:
        _require_tenant(tenant_id)
        allowed = {"topic_id", "content_id", "scheduled_date", "scheduled_time",
                   "funnel_stage", "status", "delete_mode", "deleted_at"}
        sets, params = [], []
        for k, v in changes.items():
            if k in allowed:
                sets.append(f"{k} = %s"); params.append(v)
        sets.append("updated_at = now()")
        sets.append("rev = rev + 1")

        cols = ", ".join(self._CALENDAR_COLS)
        sql = f"""UPDATE calendar_items SET {', '.join(sets)}
                  WHERE calendar_item_id = %s AND rev = %s AND tenant_id = %s
                  RETURNING {cols}"""
        params += [calendar_item_id, expected_rev, tenant_id]

        with get_rls_cursor(tenant_id) as cur:
            cur.execute(sql, tuple(params))
            row = cur.fetchone()
        if row is None:
            with get_rls_cursor(tenant_id) as cur:
                cur.execute(
                    "SELECT rev FROM calendar_items WHERE tenant_id = %s AND calendar_item_id = %s",
                    (tenant_id, calendar_item_id)
                )
                r = cur.fetchone()
            if r is None:
                raise KeyError(f"calendar_item '{calendar_item_id}' not found")
            raise RevMismatch(f"expected rev={expected_rev}, actual rev={r[0]}")
        d = dict(zip(self._CALENDAR_COLS, row))
        d["tenant_id"] = str(d["tenant_id"])
        return d

    def delete_calendar_item(self, tenant_id: str, calendar_item_id: str,
                             expected_rev: int, *, mode: str = "soft") -> dict:
        _require_tenant(tenant_id)
        if mode == "hard":
            with get_rls_cursor(tenant_id) as cur:
                cur.execute(
                    "DELETE FROM calendar_items WHERE calendar_item_id = %s AND rev = %s AND tenant_id = %s",
                    (calendar_item_id, expected_rev, tenant_id)
                )
                if cur.rowcount == 0:
                    with get_rls_cursor(tenant_id) as cur:
                        cur.execute(
                            "SELECT rev FROM calendar_items WHERE tenant_id = %s AND calendar_item_id = %s",
                            (tenant_id, calendar_item_id)
                        )
                        r = cur.fetchone()
                    if r is None:
                        raise KeyError(f"calendar_item '{calendar_item_id}' not found")
                    raise RevMismatch(f"expected rev={expected_rev}, actual rev={r[0]}")
            return {"deleted": True}
        # soft delete
        return self.update_calendar_item(
            tenant_id, calendar_item_id, expected_rev=expected_rev,
            status="cancelled", delete_mode="soft",
            deleted_at=datetime.now(timezone.utc).isoformat()
        )

    # ── 内容策略 (content_strategies) ─────────────────────────────────────

    _STRATEGY_COLS = [
        "strategy_id", "tenant_id", "topic_id", "manual_input_hint",
        "target_reader", "funnel_stage", "angle", "hook", "key_points",
        "cta", "avoid_points", "evidence_refs", "memory_refs",
        "knowledge_refs", "created_by", "rev", "created_at", "updated_at"
    ]

    def list_strategies(self, tenant_id: str, *,
                        topic_id: Optional[str] = None,
                        page: int = 1,
                        page_size: int = 20,
                        sort: str = "-created_at") -> dict:
        _require_tenant(tenant_id)
        _allowed_sort = {"created_at", "topic_id"}
        direction = "DESC" if sort.startswith("-") else "ASC"
        field = sort.lstrip("-")
        if field not in _allowed_sort:
            field, direction = "created_at", "DESC"

        where = ["tenant_id = %s"]
        params: list[Any] = [tenant_id]
        if topic_id:
            where.append("topic_id = %s"); params.append(topic_id)

        w = " AND ".join(where)
        offset = (page - 1) * page_size
        limit = min(page_size, 100)

        with get_rls_cursor(tenant_id) as cur:
            cur.execute(f"SELECT count(*) FROM content_strategies WHERE {w}", tuple(params))
            total = cur.fetchone()[0]

            cols_sql = ", ".join(self._STRATEGY_COLS)
            cur.execute(
                f"SELECT {cols_sql} FROM content_strategies WHERE {w} ORDER BY {field} {direction} LIMIT %s OFFSET %s",
                tuple(params + [limit, offset])
            )
            rows = cur.fetchall()
            col_names = [d[0] for d in cur.description]

        items = [dict(zip(col_names, r)) for r in rows]
        for it in items:
            it["tenant_id"] = str(it["tenant_id"])
        return {
            "items": items,
            "total": total if total <= 10000 else None,
            "page": page,
            "page_size": limit,
            "has_more": (page * limit) < total
        }

    def get_strategy(self, tenant_id: str, strategy_id: str) -> dict:
        _require_tenant(tenant_id)
        cols = ", ".join(self._STRATEGY_COLS)
        with get_rls_cursor(tenant_id) as cur:
            cur.execute(
                f"SELECT {cols} FROM content_strategies WHERE tenant_id = %s AND strategy_id = %s",
                (tenant_id, strategy_id)
            )
            row = cur.fetchone()
        if row is None:
            raise KeyError(f"strategy '{strategy_id}' not found")
        d = dict(zip(self._STRATEGY_COLS, row))
        d["tenant_id"] = str(d["tenant_id"])
        return d

    def create_strategy(self, tenant_id: str, *,
                        topic_id: Optional[str] = None,
                        manual_input_hint: Optional[str] = None,
                        target_reader: Optional[str] = None,
                        funnel_stage: Optional[str] = None,
                        angle: Optional[str] = None,
                        hook: Optional[str] = None,
                        key_points: Optional[list] = None,
                        cta: Optional[str] = None,
                        avoid_points: Optional[list] = None,
                        evidence_refs: Optional[list] = None,
                        memory_refs: Optional[list] = None,
                        knowledge_refs: Optional[list] = None,
                        created_by: str = "user") -> dict:
        _require_tenant(tenant_id)
        if not topic_id and not manual_input_hint:
            raise ValueError("topic_id or manual_input_hint required")
        import uuid as _uuid
        sid = f"str_{_uuid.uuid4().hex[:12]}"

        cols = ", ".join(self._STRATEGY_COLS)
        with get_rls_cursor(tenant_id) as cur:
            cur.execute(
                f"""INSERT INTO content_strategies(strategy_id, tenant_id, topic_id, manual_input_hint,
                                                    target_reader, funnel_stage, angle, hook,
                                                    key_points, cta, avoid_points, evidence_refs,
                                                    memory_refs, knowledge_refs, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                            %s::jsonb, %s, %s::jsonb, %s::jsonb,
                            %s::jsonb, %s::jsonb, %s)
                    RETURNING {cols}""",
                (sid, tenant_id, topic_id, manual_input_hint,
                 target_reader, funnel_stage, angle, hook,
                 json.dumps(key_points or []), cta, json.dumps(avoid_points or []),
                 json.dumps(evidence_refs or []), json.dumps(memory_refs or []),
                 json.dumps(knowledge_refs or []), created_by)
            )
            row = cur.fetchone()
        d = dict(zip(self._STRATEGY_COLS, row))
        d["tenant_id"] = str(d["tenant_id"])
        return d

    def update_strategy(self, tenant_id: str, strategy_id: str, *,
                        expected_rev: int, **changes: Any) -> dict:
        _require_tenant(tenant_id)
        allowed = {"topic_id", "manual_input_hint", "target_reader", "funnel_stage",
                   "angle", "hook", "key_points", "cta", "avoid_points",
                   "evidence_refs", "memory_refs", "knowledge_refs", "created_by"}
        jsonb_fields = {"key_points", "avoid_points", "evidence_refs", "memory_refs", "knowledge_refs"}
        sets, params = [], []
        for k, v in changes.items():
            if k in jsonb_fields:
                sets.append(f"{k} = %s::jsonb"); params.append(json.dumps(v or []))
            elif k in allowed:
                sets.append(f"{k} = %s"); params.append(v)
        if not sets:
            sets.append("updated_at = now()")
        sets.append("updated_at = now()")
        sets.append("rev = rev + 1")

        cols = ", ".join(self._STRATEGY_COLS)
        sql = f"""UPDATE content_strategies SET {', '.join(sets)}
                  WHERE strategy_id = %s AND rev = %s AND tenant_id = %s
                  RETURNING {cols}"""
        params += [strategy_id, expected_rev, tenant_id]

        with get_rls_cursor(tenant_id) as cur:
            cur.execute(sql, tuple(params))
            row = cur.fetchone()
        if row is None:
            with get_rls_cursor(tenant_id) as cur:
                cur.execute(
                    "SELECT rev FROM content_strategies WHERE tenant_id = %s AND strategy_id = %s",
                    (tenant_id, strategy_id)
                )
                r = cur.fetchone()
            if r is None:
                raise KeyError(f"strategy '{strategy_id}' not found")
            raise RevMismatch(f"expected rev={expected_rev}, actual rev={r[0]}")
        d = dict(zip(self._STRATEGY_COLS, row))
        d["tenant_id"] = str(d["tenant_id"])
        return d

    def delete_strategy(self, tenant_id: str, strategy_id: str) -> None:
        _require_tenant(tenant_id)
        with get_rls_cursor(tenant_id) as cur:
            cur.execute(
                "DELETE FROM content_strategies WHERE tenant_id = %s AND strategy_id = %s",
                (tenant_id, strategy_id)
            )
            if cur.rowcount == 0:
                raise KeyError(f"strategy '{strategy_id}' not found")

    # ── Orchestrator 会话（V1.3） ────────────────────────────────────────

    _SESSION_COLS = [
        "session_id", "tenant_id", "goal_id", "status", "messages",
        "proposed_plan", "decision_cards", "dag_id",
        "trace", "pending", "rev",
        "created_at", "updated_at"
    ]
    _SESSION_JSON_COLS = {"messages", "proposed_plan", "decision_cards", "trace"}

    def _session_row(self, row) -> dict:
        d = dict(zip(self._SESSION_COLS, row))
        d["tenant_id"] = str(d["tenant_id"])
        return d

    def create_session(self, tenant_id: str, *, session_id: str,
                        goal_id: Optional[str] = None,
                        status: str = "thinking",  # 011 CHECK 不含旧 'gathering'；默认改为合法值（live caller 均显式传 status）
                        messages: Optional[list] = None,
                        proposed_plan: Optional[list] = None,
                        decision_cards: Optional[list] = None,
                        dag_id: Optional[str] = None) -> dict:
        _require_tenant(tenant_id)
        cols = ", ".join(self._SESSION_COLS)
        with get_rls_cursor(tenant_id) as cur:
            cur.execute(
                f"""INSERT INTO orchestrator_sessions(session_id, tenant_id, goal_id,
                                                      status, messages, proposed_plan,
                                                      decision_cards, dag_id)
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s)
                    RETURNING {cols}""",
                (session_id, tenant_id, goal_id, status,
                 json.dumps(_json_safe(messages or [])),
                 json.dumps(_json_safe(proposed_plan or [])),
                 json.dumps(_json_safe(decision_cards or [])), dag_id)
            )
            row = cur.fetchone()
        return self._session_row(row)

    def get_session(self, tenant_id: str, session_id: str) -> Optional[dict]:
        _require_tenant(tenant_id)
        cols = ", ".join(self._SESSION_COLS)
        with get_rls_cursor(tenant_id) as cur:
            cur.execute(
                f"SELECT {cols} FROM orchestrator_sessions WHERE tenant_id = %s AND session_id = %s",
                (tenant_id, session_id)
            )
            row = cur.fetchone()
        return self._session_row(row) if row is not None else None

    def update_session(self, tenant_id: str, session_id: str, *,
                       expected_rev: int, **changes: Any) -> dict:
        _require_tenant(tenant_id)
        allowed = {"goal_id", "status", "messages", "proposed_plan",
                   "decision_cards", "dag_id"}
        sets, params = [], []
        for k, v in changes.items():
            if k in self._SESSION_JSON_COLS:
                sets.append(f"{k} = %s::jsonb"); params.append(json.dumps(_json_safe(v or [])))
            elif k == "pending":
                sets.append(f"{k} = %s::jsonb"); params.append(json.dumps(_json_safe(v)))
            elif k in allowed:
                sets.append(f"{k} = %s"); params.append(v)
        sets.append("updated_at = now()")
        sets.append("rev = rev + 1")

        cols = ", ".join(self._SESSION_COLS)
        sql = f"""UPDATE orchestrator_sessions SET {', '.join(sets)}
                  WHERE session_id = %s AND rev = %s AND tenant_id = %s
                  RETURNING {cols}"""
        params += [session_id, expected_rev, tenant_id]

        with get_rls_cursor(tenant_id) as cur:
            cur.execute(sql, tuple(params))
            row = cur.fetchone()
        if row is None:
            with get_rls_cursor(tenant_id) as cur:
                cur.execute(
                    "SELECT rev FROM orchestrator_sessions WHERE tenant_id = %s AND session_id = %s",
                    (tenant_id, session_id)
                )
                r = cur.fetchone()
            if r is None:
                raise KeyError(f"session '{session_id}' not found")
            raise RevMismatch(f"expected rev={expected_rev}, actual rev={r[0]}")
        return self._session_row(row)

    def list_sessions(self, tenant_id: str, *, goal_id: Optional[str] = None,
                      limit: int = 20) -> list[dict]:
        _require_tenant(tenant_id)
        cols = ", ".join(self._SESSION_COLS)
        lim = min(limit, 100)
        where = "WHERE tenant_id = %s"
        params: list = [tenant_id]
        if goal_id is not None:
            where += " AND goal_id = %s"
            params.append(goal_id)
        with get_rls_cursor(tenant_id) as cur:
            cur.execute(
                f"""SELECT {cols} FROM orchestrator_sessions {where}
                    ORDER BY updated_at DESC LIMIT %s""",
                tuple(params + [lim])
            )
            rows = cur.fetchall()
        return [self._session_row(r) for r in rows]

    def delete_session(self, tenant_id: str, session_id: str) -> bool:
        _require_tenant(tenant_id)
        with get_rls_cursor(tenant_id) as cur:
            cur.execute(
                "DELETE FROM orchestrator_sessions WHERE tenant_id = %s AND session_id = %s",
                (tenant_id, session_id)
            )
            return cur.rowcount > 0
