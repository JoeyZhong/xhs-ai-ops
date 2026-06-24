"""
StorageBackend 接口定义（Protocol）。

设计目标：
- 同一套接口可以背靠本地文件 或 Supabase
- 所有方法强制 tenant_id 显式传入，禁止默认 None（防跨租户串数据）
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Protocol, runtime_checkable

import pandas as pd


class TenantContextRequired(Exception):
    """tenant_id 必须显式提供"""


@runtime_checkable
class StorageBackend(Protocol):
    # ── 任务结果 ─────────────────────────────────────────────────────────
    def save_task_result(self, tenant_id: str, task_id: str, result: dict) -> None: ...
    def load_task_result(self, tenant_id: str, task_id: str) -> Optional[dict]: ...

    # ── Memory ──────────────────────────────────────────────────────────
    def load_memory(self, tenant_id: str, scope: str, file: str) -> Optional[str]: ...
    def save_memory(self, tenant_id: str, scope: str, file: str, content: str) -> None: ...

    # ── 采集数据 ─────────────────────────────────────────────────────────
    def list_collected_data(self, tenant_id: str, since: datetime,
                            goal_id: Optional[str] = None) -> pd.DataFrame: ...
    def save_collected_data(self, tenant_id: str, source: str, df: pd.DataFrame,
                              meta: Optional[dict] = None) -> str: ...

    # ── 热词 ─────────────────────────────────────────────────────────────
    def list_hot_keywords(self, tenant_id: str, since: datetime) -> pd.DataFrame: ...
    def save_hot_keywords(self, tenant_id: str, df: pd.DataFrame) -> str: ...

    # ── 生成内容 ─────────────────────────────────────────────────────────
    def list_generated_posts(self, tenant_id: str, since: Optional[datetime] = None) -> pd.DataFrame: ...
    def save_generated_posts(self, tenant_id: str, df: pd.DataFrame, meta: Optional[dict] = None) -> str: ...

    # ── 目标（goals） ────────────────────────────────────────────────────
    def load_goals(self, tenant_id: str) -> dict: ...
    def save_goals(self, tenant_id: str, data: dict) -> None: ...

    # ── 人设 ─────────────────────────────────────────────────────────────
    def load_persona(self, tenant_id: str) -> dict: ...
    def save_persona(self, tenant_id: str, data: dict) -> None: ...

    # ── 审计 ─────────────────────────────────────────────────────────────
    def save_audit_log(self, tenant_id: str, entry: dict) -> None: ...

    # ── 数据生命周期 ─────────────────────────────────────────────────────
    def cleanup_old_data(self, tenant_id: str, days: int) -> list[str]: ...

    # ── Insight Evidence Pool (P2) ──────────────────────────────────────
    def list_evidence(self, tenant_id: str, *,
                      angle: str | None = None,
                      funnel_stage: str | None = None,
                      limit: int = 3) -> list[dict]: ...
    def upsert_evidence(self, tenant_id: str, evidence: dict) -> dict: ...

    # ── Skills Hub ──────────────────────────────────────────────────────
    def list_skills(self, *, tenant_id: Optional[str] = None,
                    owner: str = "all",
                    suggested_for: Optional[str] = None,
                    limit: int = 20,
                    cursor: Optional[str] = None) -> list[dict]: ...
    def get_skill(self, skill_id: str, tenant_id: str) -> dict: ...
    def create_skill(self, *, tenant_id: Optional[str], name: str,
                     description: str, body: str,
                     suggested_for: list[str],
                     source_skill_id: Optional[str] = None,
                     extras: dict[str, str] = {}) -> dict: ...
    def update_skill(self, skill_id: str, tenant_id: str, *,
                     expected_rev: int,
                     **changes) -> dict: ...
    def delete_skill(self, skill_id: str, tenant_id: str) -> list[str]: ...
    def list_equipment(self, tenant_id: str, agent_role: str) -> list[dict]: ...
    def equip(self, tenant_id: str, agent_role: str, skill_id: str) -> None: ...
    def unequip(self, tenant_id: str, agent_role: str, skill_id: str) -> None: ...

    # ── Orchestrator 会话（V1.3） ────────────────────────────────────────
    def create_session(self, tenant_id: str, *, session_id: str,
                        goal_id: Optional[str] = None,
                        status: str = "gathering",
                        messages: Optional[list] = None,
                        proposed_plan: Optional[list] = None,
                        decision_cards: Optional[list] = None,
                        dag_id: Optional[str] = None) -> dict: ...
    def get_session(self, tenant_id: str, session_id: str) -> Optional[dict]: ...
    def update_session(self, tenant_id: str, session_id: str, *,
                       expected_rev: int, **changes: Any) -> dict: ...
    def list_sessions(self, tenant_id: str, *, goal_id: Optional[str] = None,
                      limit: int = 20) -> list[dict]: ...
    def delete_session(self, tenant_id: str, session_id: str) -> bool: ...

    # ── 线索雷达 leads（lead-intent-radar V1） ───────────────────────────
    def create_lead(self, tenant_id: str, *, signal_key: str,
                    goal_id: Optional[str] = None,
                    persona_id: Optional[str] = None,
                    source: str = "xhs",
                    source_url: Optional[str] = None,
                    author: Optional[str] = None,
                    posted_at: Optional[str] = None,
                    post_text: Optional[str] = None,
                    excerpt: Optional[str] = None,
                    detected_at: Optional[str] = None,
                    is_intent: bool = True,
                    match_score: Optional[int] = None,
                    trigger_type: Optional[str] = None,
                    judge_reason: Optional[str] = None,
                    draft_text: Optional[str] = None,
                    check_lure_pass: bool = False,
                    check_dup_pass: bool = False,
                    lead_status: str = "qualified",
                    meta: Optional[dict] = None) -> dict: ...
    def get_lead(self, tenant_id: str, lead_id: str) -> Optional[dict]: ...
    def list_leads(self, tenant_id: str, *, goal_id: Optional[str] = None,
                   lead_status: Optional[str] = None,
                   trigger_type: Optional[str] = None,
                   limit: int = 50) -> list[dict]: ...
    def update_lead(self, tenant_id: str, lead_id: str, *,
                    expected_rev: int, **changes: Any) -> dict: ...
    def delete_lead(self, tenant_id: str, lead_id: str) -> bool: ...


class RevMismatch(Exception):
    """OCC revision mismatch — caller must refetch."""


def _require_tenant(tenant_id: str | None) -> str:
    if not tenant_id or not str(tenant_id).strip():
        raise TenantContextRequired("tenant_id is required for all storage operations")
    return str(tenant_id).strip()
