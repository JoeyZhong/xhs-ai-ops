"""
本地 JSON / Excel backend。

向下兼容设计：
- 默认 tenant_id="default"，文件落在 xhs_data/default/ 和 config/default/
- 但读取时也回退兼容老路径 xhs_data/ 和 config/（升级期友好）
- Excel 文件命名延续现有约定（spider_xhs_采集结果_*.xlsx 等）
"""

from __future__ import annotations

import json
import os
import uuid
import ast
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from storage.base import RevMismatch, _require_tenant

# Process-global lock guarding sidecar read-modify-write. The JSON sidecars are
# not concurrency-safe (load → mutate dict → write), so concurrent writers can
# lose updates. One reentrant lock serialises the critical sections.
_SIDECAR_LOCK = threading.RLock()


class LocalJsonBackend:
    """文件系统 backend，兼容现有目录结构。"""

    def __init__(self, base_dir: Optional[str] = None):
        self.base = Path(base_dir) if base_dir else Path(__file__).parent.parent
        self.data_dir = self.base / "xhs_data"
        self.config_dir = self.base / "config"
        self.memory_dir = self.base / "memory"
        self.data_dir.mkdir(exist_ok=True)
        self.config_dir.mkdir(exist_ok=True)
        self.memory_dir.mkdir(exist_ok=True)

    # ── 路径策略 ─────────────────────────────────────────────────────────

    def _data_path(self, tenant_id: str, *parts) -> Path:
        """租户数据目录。tenant=default 时回退兼容老路径。"""
        if tenant_id == "default":
            # 兼容期：老用户文件在 xhs_data/ 直接，不在 xhs_data/default/
            tenant_root = self.data_dir
        else:
            tenant_root = self.data_dir / tenant_id
        tenant_root.mkdir(parents=True, exist_ok=True)
        return tenant_root.joinpath(*parts) if parts else tenant_root

    def _config_path(self, tenant_id: str, filename: str) -> Path:
        if tenant_id == "default":
            # 老用户的 config 文件在 config/ 直接
            return self.config_dir / filename
        sub = self.config_dir / tenant_id
        sub.mkdir(parents=True, exist_ok=True)
        return sub / filename

    def _memory_path(self, tenant_id: str, scope: str, filename: str) -> Path:
        sub = self.memory_dir / tenant_id / scope
        sub.mkdir(parents=True, exist_ok=True)
        return sub / filename

    # ── 任务结果 ─────────────────────────────────────────────────────────

    def save_task_result(self, tenant_id: str, task_id: str, result: dict) -> None:
        tenant_id = _require_tenant(tenant_id)
        path = self._data_path(tenant_id, "tasks", f"{task_id}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                          encoding="utf-8")

    def load_task_result(self, tenant_id: str, task_id: str) -> Optional[dict]:
        tenant_id = _require_tenant(tenant_id)
        path = self._data_path(tenant_id, "tasks", f"{task_id}.json")
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    # ── Memory ──────────────────────────────────────────────────────────

    def load_memory(self, tenant_id: str, scope: str, file: str) -> Optional[str]:
        tenant_id = _require_tenant(tenant_id)
        path = self._memory_path(tenant_id, scope, file)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def save_memory(self, tenant_id: str, scope: str, file: str, content: str) -> None:
        tenant_id = _require_tenant(tenant_id)
        path = self._memory_path(tenant_id, scope, file)
        path.write_text(content, encoding="utf-8")

    # ── 采集数据 ─────────────────────────────────────────────────────────

    @staticmethod
    def _sanitize_goal_id(gid: Optional[str]) -> str:
        """Sanitize goal_id for safe filesystem use."""
        if not gid or not str(gid).strip():
            return "unassigned"
        import re as _re
        safe = _re.sub(r'[/\\ ]', '_', str(gid).strip())
        return safe or "unassigned"

    def list_collected_data(self, tenant_id: str, since: datetime,
                            goal_id: Optional[str] = None) -> pd.DataFrame:
        tenant_id = _require_tenant(tenant_id)
        root = self._data_path(tenant_id)

        if goal_id is not None:
            safe = self._sanitize_goal_id(goal_id)
            pattern = f"spider_xhs_采集结果_{safe}_*.xlsx"
        else:
            pattern = "spider_xhs_采集结果_*.xlsx"

        files = sorted(
            [f for f in root.glob(pattern)
             if datetime.fromtimestamp(f.stat().st_mtime) >= since],
            key=lambda f: f.stat().st_mtime,
        )
        if not files:
            return pd.DataFrame()
        dfs = []
        for f in files:
            try:
                dfs.append(pd.read_excel(f))
            except Exception:
                pass
        if not dfs:
            return pd.DataFrame()
        df_all = pd.concat(dfs, ignore_index=True)
        if "笔记ID" in df_all.columns:
            df_all = df_all.drop_duplicates(subset=["笔记ID"], keep="last")
        return df_all

    def save_collected_data(self, tenant_id: str, source: str, df: pd.DataFrame,
                              meta: Optional[dict] = None) -> str:
        tenant_id = _require_tenant(tenant_id)
        goal_id = (meta or {}).get("goal_id", "")
        safe = self._sanitize_goal_id(goal_id)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = self._data_path(tenant_id, f"spider_xhs_采集结果_{safe}_{ts}.xlsx")
        with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="采集数据")
        return str(out_path)

    # ── 热词 ─────────────────────────────────────────────────────────────

    def list_hot_keywords(self, tenant_id: str, since: datetime) -> pd.DataFrame:
        tenant_id = _require_tenant(tenant_id)
        root = self._data_path(tenant_id)
        files = sorted(
            [f for f in root.glob("hot_trends_????????.xlsx")
             if datetime.fromtimestamp(f.stat().st_mtime) >= since],
            key=lambda f: f.stat().st_mtime,
        )
        if not files:
            return pd.DataFrame()
        dfs = []
        for f in files:
            try:
                df = pd.read_excel(f)
                df["_date"] = f.stem.replace("hot_trends_", "")
                dfs.append(df)
            except Exception:
                pass
        if not dfs:
            return pd.DataFrame()
        return pd.concat(dfs, ignore_index=True)

    def save_hot_keywords(self, tenant_id: str, df: pd.DataFrame) -> str:
        tenant_id = _require_tenant(tenant_id)
        date_str = datetime.now().strftime("%Y%m%d")
        out_path = self._data_path(tenant_id, f"hot_trends_{date_str}.xlsx")
        with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="热点词")
        return str(out_path)

    # ── 生成内容 ─────────────────────────────────────────────────────────

    def list_generated_posts(self, tenant_id: str,
                               since: Optional[datetime] = None,
                               topic_id: Optional[str] = None,
                               strategy_id: Optional[str] = None,
                               calendar_item_id: Optional[str] = None,
                               status: Optional[str] = None) -> pd.DataFrame:
        tenant_id = _require_tenant(tenant_id)
        sidecar = self._load_lifecycle(tenant_id, "generated_posts")

        if sidecar:
            df = pd.DataFrame(list(sidecar.values()))
        else:
            root = self._data_path(tenant_id)
            files = sorted(root.glob("generated_content_*.xlsx"),
                           key=lambda f: f.stat().st_mtime, reverse=True)
            if since:
                files = [f for f in files
                         if datetime.fromtimestamp(f.stat().st_mtime) >= since]
            if not files:
                return pd.DataFrame()
            dfs = []
            for f in files:
                try:
                    dfs.append(pd.read_excel(f))
                except Exception:
                    pass
            df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()

        if df.empty:
            return df
        if since is not None and "created_at" in df.columns:
            since_iso = since.isoformat() if isinstance(since, datetime) else str(since)
            df = df[df["created_at"].fillna("").astype(str) >= since_iso]
        if topic_id and "topic_id" in df.columns:
            df = df[df["topic_id"] == topic_id]
        if strategy_id and "strategy_id" in df.columns:
            df = df[df["strategy_id"] == strategy_id]
        if calendar_item_id and "calendar_item_id" in df.columns:
            df = df[df["calendar_item_id"] == calendar_item_id]
        if status and "status" in df.columns:
            df = df[df["status"] == status]
        return df.reset_index(drop=True)

    def save_generated_posts(self, tenant_id: str, df: pd.DataFrame,
                               meta: Optional[dict] = None) -> str:
        tenant_id = _require_tenant(tenant_id)
        meta = meta or {}
        prefix = f"generated_content_{meta.get('goal_id','')}_" if meta.get("goal_id") else "generated_content_"
        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = self._data_path(tenant_id, f"{prefix}{date_str}.xlsx")
        with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="生成内容")

        # 同步 sidecar (V1.1 lifecycle: draft lookup + OCC)
        # 读-改-写需在锁内串行，否则并发写者会丢更新（lost update）。
        if not df.empty:
            with _SIDECAR_LOCK:
                sidecar = self._load_lifecycle(tenant_id, "generated_posts")
                now = datetime.now(timezone.utc).isoformat()
                for _, row in df.iterrows():
                    r = self._normalize_post_row(row.to_dict())
                    cid = str(r.get("content_id") or f"gen_{uuid.uuid4().hex[:12]}")
                    r["content_id"] = cid
                    r["tenant_id"] = tenant_id
                    if cid in sidecar:
                        existing = sidecar[cid]
                        for k, v in r.items():
                            if k in ("created_at", "rev"):
                                continue
                            existing[k] = v
                        existing["rev"] = int(existing.get("rev", 0) or 0) + 1
                        existing["updated_at"] = now
                        sidecar[cid] = existing
                    else:
                        r.setdefault("rev", 1)
                        r.setdefault("created_at", now)
                        r.setdefault("updated_at", now)
                        sidecar[cid] = r
                self._save_lifecycle(tenant_id, "generated_posts", sidecar)
        return str(out_path)

    def get_generated_post(self, tenant_id: str, content_id: str) -> Optional[dict]:
        tenant_id = _require_tenant(tenant_id)
        sidecar = self._load_lifecycle(tenant_id, "generated_posts")
        row = sidecar.get(content_id)
        return dict(row) if row else None

    def update_generated_post(self, tenant_id: str, content_id: str, *,
                               expected_rev: int, **changes: Any) -> dict:
        tenant_id = _require_tenant(tenant_id)
        sidecar = self._load_lifecycle(tenant_id, "generated_posts")
        if content_id not in sidecar:
            raise KeyError(f"generated_content '{content_id}' not found")
        item = sidecar[content_id]
        current_rev = int(item.get("rev", 0) or 0)
        if current_rev != expected_rev:
            raise RevMismatch(f"expected rev={expected_rev}, actual rev={current_rev}")
        allowed = {"title", "body", "hashtags", "publish_at", "status",
                   "topic_id", "strategy_id", "calendar_item_id",
                   "knowledge_refs", "memory_refs", "meta"}
        for k, v in changes.items():
            if k in allowed:
                item[k] = v
        item["rev"] = current_rev + 1
        item["updated_at"] = datetime.now(timezone.utc).isoformat()
        sidecar[content_id] = item
        self._save_lifecycle(tenant_id, "generated_posts", sidecar)
        return dict(item)

    # ── 目标 ─────────────────────────────────────────────────────────────

    def load_goals(self, tenant_id: str) -> dict:
        tenant_id = _require_tenant(tenant_id)
        path = self._config_path(tenant_id, "goals.json")
        if not path.exists():
            return {"active_goal_id": "", "goals": []}
        return json.loads(path.read_text(encoding="utf-8"))

    def save_goals(self, tenant_id: str, data: dict) -> None:
        tenant_id = _require_tenant(tenant_id)
        path = self._config_path(tenant_id, "goals.json")
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                          encoding="utf-8")

    # ── 人设 ─────────────────────────────────────────────────────────────

    def load_persona(self, tenant_id: str) -> dict:
        tenant_id = _require_tenant(tenant_id)
        path = self._config_path(tenant_id, "persona.json")
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def save_persona(self, tenant_id: str, data: dict) -> None:
        tenant_id = _require_tenant(tenant_id)
        path = self._config_path(tenant_id, "persona.json")
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                          encoding="utf-8")

    # ── 审计 ─────────────────────────────────────────────────────────────

    def save_audit_log(self, tenant_id: str, entry: dict) -> None:
        tenant_id = _require_tenant(tenant_id)
        date_str = datetime.now().strftime("%Y%m%d")
        log_dir = self._data_path(tenant_id, "audit")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"audit_{date_str}.jsonl"
        # 原子追加（写入失败不阻断业务）
        line = json.dumps({**entry, "_ts": datetime.now().isoformat()},
                            ensure_ascii=False)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    # ── V1.1 Lifecycle 通用辅助 ──────────────────────────────────────────

    def _lifecycle_path(self, tenant_id: str, name: str) -> Path:
        return self._config_path(tenant_id, f"lifecycle_{name}.json")

    def _load_lifecycle(self, tenant_id: str, name: str) -> dict:
        path = self._lifecycle_path(tenant_id, name)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_lifecycle(self, tenant_id: str, name: str, data: dict) -> None:
        path = self._lifecycle_path(tenant_id, name)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    @staticmethod
    def _sort_items(items: list[dict], sort: str, allowed: set[str],
                     *, default_field: str, default_desc: bool) -> list[dict]:
        descending = sort.startswith("-")
        field = sort.lstrip("-")
        if field not in allowed:
            field, descending = default_field, default_desc
        return sorted(items, key=lambda x: (x.get(field) is None, x.get(field) or ""),
                       reverse=descending)

    @staticmethod
    def _paginate(items: list[dict], page: int, page_size: int) -> dict:
        total = len(items)
        limit = min(max(page_size, 1), 100)
        offset = max(page - 1, 0) * limit
        return {
            "items": items[offset:offset + limit],
            "total": total,
            "page": page,
            "page_size": limit,
            "has_more": (offset + limit) < total,
        }

    @staticmethod
    def _normalize_post_row(row: dict) -> dict:
        """Coerce pandas row → JSON-safe dict (lists for list-fields, None for NaN)."""
        out: dict[str, Any] = {}
        for k, v in row.items():
            # NaN / NaT detection (only on scalars)
            try:
                if v is None or (not isinstance(v, (list, dict, str)) and pd.isna(v)):
                    out[k] = None
                    continue
            except (TypeError, ValueError):
                pass
            if k in ("hashtags", "knowledge_refs", "memory_refs"):
                if isinstance(v, str):
                    try:
                        parsed = json.loads(v)
                        out[k] = parsed if isinstance(parsed, list) else []
                    except Exception:
                        try:
                            parsed = ast.literal_eval(v)
                            out[k] = list(parsed) if isinstance(parsed, (list, tuple)) else []
                        except Exception:
                            out[k] = []
                elif isinstance(v, (list, tuple)):
                    out[k] = list(v)
                else:
                    out[k] = []
            else:
                out[k] = v
        return out

    # ── 内容选题 (topics) ─────────────────────────────────────────────────

    def list_topics(self, tenant_id: str, *,
                     goal_id: Optional[str] = None,
                     status: Optional[str] = None,
                     page: int = 1,
                     page_size: int = 20,
                     sort: str = "-updated_at") -> dict:
        tenant_id = _require_tenant(tenant_id)
        data = self._load_lifecycle(tenant_id, "topics")
        items = list(data.values())
        if goal_id:
            items = [i for i in items if i.get("goal_id") == goal_id]
        if status:
            items = [i for i in items if i.get("status") == status]
        items = self._sort_items(
            items, sort, {"title", "created_at", "updated_at", "status"},
            default_field="updated_at", default_desc=True,
        )
        return self._paginate(items, page, page_size)

    def get_topic(self, tenant_id: str, topic_id: str) -> dict:
        tenant_id = _require_tenant(tenant_id)
        data = self._load_lifecycle(tenant_id, "topics")
        if topic_id not in data:
            raise KeyError(f"topic '{topic_id}' not found")
        return dict(data[topic_id])

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
        tenant_id = _require_tenant(tenant_id)
        data = self._load_lifecycle(tenant_id, "topics")
        topic_id = f"topic_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        item = {
            "topic_id": topic_id,
            "tenant_id": tenant_id,
            "goal_id": goal_id,
            "persona_id": persona_id,
            "title": title,
            "angle": angle,
            "funnel_stage": funnel_stage,
            "source": source,
            "source_refs": list(source_refs or []),
            "status": status,
            "created_by": created_by,
            "rev": 1,
            "created_at": now,
            "updated_at": now,
        }
        data[topic_id] = item
        self._save_lifecycle(tenant_id, "topics", data)
        return dict(item)

    def update_topic(self, tenant_id: str, topic_id: str, *,
                      expected_rev: int, **changes: Any) -> dict:
        tenant_id = _require_tenant(tenant_id)
        data = self._load_lifecycle(tenant_id, "topics")
        if topic_id not in data:
            raise KeyError(f"topic '{topic_id}' not found")
        item = data[topic_id]
        current_rev = int(item.get("rev", 0) or 0)
        if current_rev != expected_rev:
            raise RevMismatch(f"expected rev={expected_rev}, actual rev={current_rev}")
        allowed = {"goal_id", "persona_id", "title", "angle", "funnel_stage",
                   "source", "source_refs", "status", "created_by"}
        for k, v in changes.items():
            if k in allowed:
                item[k] = list(v) if k == "source_refs" and v is not None else v
        item["rev"] = current_rev + 1
        item["updated_at"] = datetime.now(timezone.utc).isoformat()
        data[topic_id] = item
        self._save_lifecycle(tenant_id, "topics", data)
        return dict(item)

    def delete_topic(self, tenant_id: str, topic_id: str, expected_rev: int) -> dict:
        return self.update_topic(tenant_id, topic_id, expected_rev=expected_rev,
                                  status="archived")

    # ── 内容日历 (calendar_items) ─────────────────────────────────────────

    def list_calendar_items(self, tenant_id: str, *,
                             date_from: Optional[str] = None,
                             date_to: Optional[str] = None,
                             status: Optional[str] = None,
                             include_deleted: bool = False,
                             page: int = 1,
                             page_size: int = 20,
                             sort: str = "scheduled_date") -> dict:
        tenant_id = _require_tenant(tenant_id)
        data = self._load_lifecycle(tenant_id, "calendar_items")
        items = list(data.values())
        if not include_deleted:
            items = [i for i in items if i.get("deleted_at") in (None, "")]
        if date_from:
            items = [i for i in items if (i.get("scheduled_date") or "") >= date_from]
        if date_to:
            items = [i for i in items if (i.get("scheduled_date") or "") <= date_to]
        if status:
            items = [i for i in items if i.get("status") == status]
        items = self._sort_items(
            items, sort, {"scheduled_date", "updated_at", "status"},
            default_field="scheduled_date", default_desc=False,
        )
        return self._paginate(items, page, page_size)

    def get_calendar_item(self, tenant_id: str, calendar_item_id: str) -> dict:
        tenant_id = _require_tenant(tenant_id)
        data = self._load_lifecycle(tenant_id, "calendar_items")
        if calendar_item_id not in data:
            raise KeyError(f"calendar_item '{calendar_item_id}' not found")
        return dict(data[calendar_item_id])

    def create_calendar_item(self, tenant_id: str, *,
                              scheduled_date: str,
                              scheduled_time: Optional[str] = None,
                              topic_id: Optional[str] = None,
                              funnel_stage: Optional[str] = None,
                              content_id: Optional[str] = None,
                              created_by: str = "user") -> dict:
        tenant_id = _require_tenant(tenant_id)
        data = self._load_lifecycle(tenant_id, "calendar_items")
        cid = f"cal_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        item = {
            "calendar_item_id": cid,
            "tenant_id": tenant_id,
            "topic_id": topic_id,
            "content_id": content_id,
            "scheduled_date": scheduled_date,
            "scheduled_time": scheduled_time,
            "funnel_stage": funnel_stage,
            "status": "planned",
            "delete_mode": None,
            "deleted_at": None,
            "created_by": created_by,
            "rev": 1,
            "created_at": now,
            "updated_at": now,
        }
        data[cid] = item
        self._save_lifecycle(tenant_id, "calendar_items", data)
        return dict(item)

    def update_calendar_item(self, tenant_id: str, calendar_item_id: str, *,
                              expected_rev: int, **changes: Any) -> dict:
        tenant_id = _require_tenant(tenant_id)
        data = self._load_lifecycle(tenant_id, "calendar_items")
        if calendar_item_id not in data:
            raise KeyError(f"calendar_item '{calendar_item_id}' not found")
        item = data[calendar_item_id]
        current_rev = int(item.get("rev", 0) or 0)
        if current_rev != expected_rev:
            raise RevMismatch(f"expected rev={expected_rev}, actual rev={current_rev}")
        allowed = {"topic_id", "content_id", "scheduled_date", "scheduled_time",
                   "funnel_stage", "status", "delete_mode", "deleted_at"}
        for k, v in changes.items():
            if k in allowed:
                item[k] = v
        item["rev"] = current_rev + 1
        item["updated_at"] = datetime.now(timezone.utc).isoformat()
        data[calendar_item_id] = item
        self._save_lifecycle(tenant_id, "calendar_items", data)
        return dict(item)

    def delete_calendar_item(self, tenant_id: str, calendar_item_id: str,
                              expected_rev: int, *, mode: str = "soft") -> dict:
        tenant_id = _require_tenant(tenant_id)
        if mode == "hard":
            data = self._load_lifecycle(tenant_id, "calendar_items")
            if calendar_item_id not in data:
                raise KeyError(f"calendar_item '{calendar_item_id}' not found")
            item = data[calendar_item_id]
            current_rev = int(item.get("rev", 0) or 0)
            if current_rev != expected_rev:
                raise RevMismatch(
                    f"expected rev={expected_rev}, actual rev={current_rev}")
            del data[calendar_item_id]
            self._save_lifecycle(tenant_id, "calendar_items", data)
            return {"deleted": True}
        return self.update_calendar_item(
            tenant_id, calendar_item_id, expected_rev=expected_rev,
            status="cancelled", delete_mode="soft",
            deleted_at=datetime.now(timezone.utc).isoformat(),
        )

    # ── 内容策略 (content_strategies) ─────────────────────────────────────

    def list_strategies(self, tenant_id: str, *,
                         topic_id: Optional[str] = None,
                         page: int = 1,
                         page_size: int = 20,
                         sort: str = "-created_at") -> dict:
        tenant_id = _require_tenant(tenant_id)
        data = self._load_lifecycle(tenant_id, "strategies")
        items = list(data.values())
        if topic_id:
            items = [i for i in items if i.get("topic_id") == topic_id]
        items = self._sort_items(
            items, sort, {"created_at", "topic_id"},
            default_field="created_at", default_desc=True,
        )
        return self._paginate(items, page, page_size)

    def get_strategy(self, tenant_id: str, strategy_id: str) -> dict:
        tenant_id = _require_tenant(tenant_id)
        data = self._load_lifecycle(tenant_id, "strategies")
        if strategy_id not in data:
            raise KeyError(f"strategy '{strategy_id}' not found")
        return dict(data[strategy_id])

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
        tenant_id = _require_tenant(tenant_id)
        if not topic_id and not manual_input_hint:
            raise ValueError("topic_id or manual_input_hint required")
        data = self._load_lifecycle(tenant_id, "strategies")
        sid = f"str_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        item = {
            "strategy_id": sid,
            "tenant_id": tenant_id,
            "topic_id": topic_id,
            "manual_input_hint": manual_input_hint,
            "target_reader": target_reader,
            "funnel_stage": funnel_stage,
            "angle": angle,
            "hook": hook,
            "key_points": list(key_points or []),
            "cta": cta,
            "avoid_points": list(avoid_points or []),
            "evidence_refs": list(evidence_refs or []),
            "memory_refs": list(memory_refs or []),
            "knowledge_refs": list(knowledge_refs or []),
            "created_by": created_by,
            "rev": 1,
            "created_at": now,
            "updated_at": now,
        }
        data[sid] = item
        self._save_lifecycle(tenant_id, "strategies", data)
        return dict(item)

    def update_strategy(self, tenant_id: str, strategy_id: str, *,
                         expected_rev: int, **changes: Any) -> dict:
        tenant_id = _require_tenant(tenant_id)
        data = self._load_lifecycle(tenant_id, "strategies")
        if strategy_id not in data:
            raise KeyError(f"strategy '{strategy_id}' not found")
        item = data[strategy_id]
        current_rev = int(item.get("rev", 0) or 0)
        if current_rev != expected_rev:
            raise RevMismatch(f"expected rev={expected_rev}, actual rev={current_rev}")
        allowed = {"topic_id", "manual_input_hint", "target_reader", "funnel_stage",
                   "angle", "hook", "key_points", "cta", "avoid_points",
                   "evidence_refs", "memory_refs", "knowledge_refs", "created_by"}
        list_fields = {"key_points", "avoid_points", "evidence_refs",
                       "memory_refs", "knowledge_refs"}
        for k, v in changes.items():
            if k in allowed:
                item[k] = list(v or []) if k in list_fields else v
        item["rev"] = current_rev + 1
        item["updated_at"] = datetime.now(timezone.utc).isoformat()
        data[strategy_id] = item
        self._save_lifecycle(tenant_id, "strategies", data)
        return dict(item)

    def delete_strategy(self, tenant_id: str, strategy_id: str) -> None:
        tenant_id = _require_tenant(tenant_id)
        data = self._load_lifecycle(tenant_id, "strategies")
        if strategy_id not in data:
            raise KeyError(f"strategy '{strategy_id}' not found")
        del data[strategy_id]
        self._save_lifecycle(tenant_id, "strategies", data)

    # ── 数据生命周期 ─────────────────────────────────────────────────────

    def cleanup_old_data(self, tenant_id: str, days: int) -> list[str]:
        tenant_id = _require_tenant(tenant_id)
        cutoff = datetime.now() - timedelta(days=days)
        deleted = []
        root = self._data_path(tenant_id)
        for pattern in [
            "spider_xhs_采集结果_*.xlsx",
            "hot_trends_????????.xlsx",
            "generated_content_*.xlsx",
        ]:
            for f in root.glob(pattern):
                try:
                    if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                        f.unlink()
                        deleted.append(f.name)
                except Exception:
                    pass
        return deleted

    # ── Insight Evidence Pool (P2) ──────────────────────────────────────

    def list_evidence(self, tenant_id: str, *,
                      angle: str | None = None,
                      funnel_stage: str | None = None,
                      limit: int = 3) -> list[dict]:
        tenant_id = _require_tenant(tenant_id)
        sidecar = self._load_lifecycle(tenant_id, "evidence")
        items: list[dict] = list(sidecar.values())

        if angle is not None:
            items = [i for i in items if i.get("angle") == angle]
        if funnel_stage is not None:
            items = [i for i in items if i.get("funnel_stage") == funnel_stage]

        # 按 ces_score DESC NULLS LAST 排序
        items.sort(key=lambda x: (
            x.get("ces_score") is None,
            -(x.get("ces_score") or 0),
        ))

        return items[:limit]

    def upsert_evidence(self, tenant_id: str, evidence: dict) -> dict:
        tenant_id = _require_tenant(tenant_id)
        sidecar = self._load_lifecycle(tenant_id, "evidence")

        source_note_id = evidence.get("source_note_id", "")
        if not source_note_id:
            raise ValueError("source_note_id is required")

        evidence_id = evidence.get("evidence_id") or f"{tenant_id}:{source_note_id}"
        now = datetime.now(timezone.utc).isoformat()

        entry = {
            "evidence_id": evidence_id,
            "tenant_id": tenant_id,
            "source_note_id": source_note_id,
            "angle": evidence.get("angle"),
            "funnel_stage": evidence.get("funnel_stage"),
            "hook": evidence.get("hook"),
            "key_insight": evidence.get("key_insight"),
            "ces_score": evidence.get("ces_score"),
            "extracted_at": evidence.get("extracted_at", now),
            "raw": evidence.get("raw"),
        }

        # 幂等: (tenant_id, source_note_id) 为 key
        sidecar[source_note_id] = entry
        self._save_lifecycle(tenant_id, "evidence", sidecar)
        return dict(entry)

    # ── Skills Hub ──────────────────────────────────────────────────────

    def _skills_dir(self, tenant_id: str | None = None) -> Path:
        """通用池用 _universal 前缀，租户私有用 memory/{tenant_id}/skills。"""
        if tenant_id is None:
            return self.memory_dir / "_universal" / "skills"
        return self.memory_dir / tenant_id / "skills"

    def _equipment_path(self, tenant_id: str) -> Path:
        return self.memory_dir / tenant_id / "agent_equipment.json"

    def _load_equipment(self, tenant_id: str) -> dict[str, list[str]]:
        path = self._equipment_path(tenant_id)
        if not path.exists():
            return {"intel": [], "content": [], "analyst": []}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"intel": [], "content": [], "analyst": []}

    def _save_equipment(self, tenant_id: str, data: dict[str, list[str]]) -> None:
        path = self._equipment_path(tenant_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_skill_bundle(self, skill_dir: Path) -> Optional[dict]:
        """从 skill 目录读 SKILL.md（YAML frontmatter + body），合并 runtime metadata 返回 dict。"""
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            return None
        try:
            from agents.skills import _split_frontmatter
            text = skill_md.read_text(encoding="utf-8")
            fm, body = _split_frontmatter(text)
            if not fm.get("name"):
                return None
        except Exception:
            return None
        result = {
            "name": fm.get("name", ""),
            "description": fm.get("description", ""),
            "version": fm.get("version", "1.0.0"),
            "suggested_for": fm.get("suggested_for", []),
            "allowed_tools": fm.get("allowed_tools", []),
            "license": fm.get("license", ""),
            "body": body,
        }
        # 从可选的 skill.json 合并运行时元数据
        skill_json = skill_dir / "skill.json"
        if skill_json.is_file():
            try:
                runtime = json.loads(skill_json.read_text(encoding="utf-8"))
                for k in ("id", "rev", "status", "tenant_id", "source_skill_id",
                           "created_at", "updated_at"):
                    if k in runtime:
                        result[k] = runtime[k]
            except Exception:
                pass
        return result

    def _list_skills_in_dir(self, skills_dir: Path, tenant_id: str | None) -> list[dict]:
        """枚举 skills_dir 下所有 skill bundle 并读 body。"""
        if not skills_dir.is_dir():
            return []
        result = []
        for entry in sorted(skills_dir.iterdir()):
            if not entry.is_dir():
                continue
            bundle = self._read_skill_bundle(entry)
            if bundle is not None:
                bundle.setdefault("tenant_id", tenant_id)
                result.append(bundle)
        return result

    def list_skills(self, *, tenant_id: Optional[str] = None,
                    owner: str = "all",
                    suggested_for: Optional[str] = None,
                    limit: int = 20,
                    cursor: Optional[str] = None) -> list[dict]:
        """owner ∈ {'universal', 'mine', 'all'}"""
        result: list[dict] = []

        if owner in ("universal", "all"):
            result.extend(self._list_skills_in_dir(
                self._skills_dir(None), None))

        if owner in ("mine", "all") and tenant_id:
            result.extend(self._list_skills_in_dir(
                self._skills_dir(tenant_id), tenant_id))

        if suggested_for:
            result = [s for s in result if suggested_for in s.get("suggested_for", [])]

        # 简单的 cursor 分页（基于 name 排序）
        if cursor:
            result = [s for s in result if s.get("name", "") > cursor]
        result = result[:limit]

        return result

    def get_skill(self, skill_id: str, tenant_id: str) -> dict:
        """按 id 查找 skill。先查租户私有，再查通用池。"""
        # 查租户私有
        t_dir = self._skills_dir(tenant_id) / skill_id
        bundle = self._read_skill_bundle(t_dir)
        if bundle is not None:
            return bundle
        # 查通用池
        u_dir = self._skills_dir(None) / skill_id
        bundle = self._read_skill_bundle(u_dir)
        if bundle is not None:
            return bundle
        raise KeyError(f"skill '{skill_id}' not found")

    def create_skill(self, *, tenant_id: Optional[str], name: str,
                     description: str, body: str,
                     suggested_for: list[str],
                     source_skill_id: Optional[str] = None,
                     extras: dict[str, str] = {}) -> dict:
        import uuid
        skill_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        # 构建 frontmatter + body → SKILL.md
        from agents.skills import ParsedSkill, _build_frontmatter
        _ps = ParsedSkill(name=name, description=description,
                          suggested_for=suggested_for)
        fm = _build_frontmatter(_ps)
        skill_md_content = f"---\n{fm}\n---\n\n{body}"

        target = self._skills_dir(tenant_id) / skill_id
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(skill_md_content, encoding="utf-8")

        # 写 skill.json（仅运行时元数据）
        runtime = {
            "id": skill_id,
            "tenant_id": tenant_id,
            "source_skill_id": source_skill_id,
            "status": "active",
            "rev": 1,
            "created_at": now,
            "updated_at": now,
        }
        (target / "skill.json").write_text(
            json.dumps(runtime, ensure_ascii=False, indent=2), encoding="utf-8")

        # 保存 extras 到 extras/ 目录
        if extras:
            extras_dir = target / "extras"
            extras_dir.mkdir(parents=True, exist_ok=True)
            ex_resolved = str(extras_dir.resolve())
            for relpath, content in extras.items():
                ep = str((extras_dir / relpath).resolve())
                if os.path.commonpath([ex_resolved, ep]) != ex_resolved:
                    continue  # path traversal attempt
                Path(ep).parent.mkdir(parents=True, exist_ok=True)
                Path(ep).write_text(content, encoding="utf-8")

        return {
            "id": skill_id,
            "tenant_id": tenant_id,
            "name": name,
            "description": description,
            "body": body,
            "suggested_for": suggested_for,
            "source_skill_id": source_skill_id,
            "status": "active",
            "version": "1.0.0",
            "rev": 1,
            "created_at": now,
            "updated_at": now,
        }

    def update_skill(self, skill_id: str, tenant_id: str, *,
                     expected_rev: int,
                     **changes) -> dict:
        # 先定位 skill
        t_dir = self._skills_dir(tenant_id) / skill_id
        bundle = self._read_skill_bundle(t_dir)
        if bundle is None:
            # 也检查通用池（但通用池写需要 is_admin，外部已拦截）
            u_dir = self._skills_dir(None) / skill_id
            bundle = self._read_skill_bundle(u_dir)
            if bundle is None:
                raise KeyError(f"skill '{skill_id}' not found")
            t_dir = u_dir

        current_rev = int(bundle.get("rev", 0) or 0)
        if current_rev != expected_rev:
            raise RevMismatch(
                f"expected rev={expected_rev}, actual rev={current_rev}")

        # 读取 skill.json 运行时元数据（不存在则初始化）
        meta_path = t_dir / "skill.json"
        if meta_path.exists():
            runtime = json.loads(meta_path.read_text(encoding="utf-8"))
        else:
            runtime = {"id": skill_id}

        new_rev = current_rev + 1
        runtime["rev"] = new_rev
        runtime["updated_at"] = datetime.now(timezone.utc).isoformat()
        for k in ("status", "tenant_id", "source_skill_id"):
            if k in changes:
                runtime[k] = changes[k]

        # 写回 skill.json
        meta_path.write_text(
            json.dumps(runtime, ensure_ascii=False, indent=2), encoding="utf-8")

        # 构建新 frontmatter
        name = changes.get("name", bundle.get("name", ""))
        description = changes.get("description", bundle.get("description", ""))
        if not name or not description:
            raise ValueError("name and description are required")
        version = changes.get("version", bundle.get("version", "1.0.0"))
        suggested_for = changes.get("suggested_for", bundle.get("suggested_for", []))

        from agents.skills import ParsedSkill, _build_frontmatter
        _ps = ParsedSkill(name=name, description=description,
                          version=version, suggested_for=suggested_for)
        fm = _build_frontmatter(_ps)

        body = changes.get("body", bundle.get("body", ""))
        skill_md_content = f"---\n{fm}\n---\n\n{body}"
        (t_dir / "SKILL.md").write_text(skill_md_content, encoding="utf-8")

        # 返回合并结果
        result = {
            "id": runtime.get("id", skill_id),
            "tenant_id": runtime.get("tenant_id", tenant_id),
            "name": name,
            "description": description,
            "body": body,
            "suggested_for": suggested_for,
            "version": version,
            "source_skill_id": runtime.get("source_skill_id"),
            "status": runtime.get("status", "active"),
            "rev": new_rev,
            "created_at": runtime.get("created_at"),
            "updated_at": runtime["updated_at"],
        }
        return result

    def delete_skill(self, skill_id: str, tenant_id: str) -> list[str]:
        """删除 skill，返回被级联 unequip 的 role 列表。"""
        t_dir = self._skills_dir(tenant_id) / skill_id
        if not t_dir.is_dir():
            u_dir = self._skills_dir(None) / skill_id
            if not u_dir.is_dir():
                raise KeyError(f"skill '{skill_id}' not found")
            t_dir = u_dir

        # 级联 unequip
        unequipped: list[str] = []
        equip = self._load_equipment(tenant_id)
        for role in list(equip.keys()):
            if skill_id in equip[role]:
                equip[role].remove(skill_id)
                unequipped.append(role)
        if unequipped:
            self._save_equipment(tenant_id, equip)

        # 删 bundle
        import shutil
        shutil.rmtree(t_dir)
        return unequipped

    def list_equipment(self, tenant_id: str, agent_role: str) -> list[dict]:
        """返回该 role 已装备的 skill summary 列表。"""
        equip = self._load_equipment(tenant_id)
        skill_ids = equip.get(agent_role, [])
        results = []
        for sid in skill_ids:
            try:
                s = self.get_skill(sid, tenant_id)
                results.append({
                    "id": s["id"],
                    "name": s["name"],
                    "description": s["description"],
                    "suggested_for": s.get("suggested_for", []),
                    "owner": "universal" if s.get("tenant_id") is None else "mine",
                    "source_skill_id": s.get("source_skill_id"),
                    "version": s.get("version", "1.0.0"),
                    "rev": int(s.get("rev", 0) or 0),
                })
            except KeyError:
                continue  # skill 已被删，跳过
        return results

    def equip(self, tenant_id: str, agent_role: str, skill_id: str) -> None:
        equip = self._load_equipment(tenant_id)
        if agent_role not in equip:
            equip[agent_role] = []
        if skill_id not in equip[agent_role]:
            equip[agent_role].append(skill_id)
        self._save_equipment(tenant_id, equip)

    def unequip(self, tenant_id: str, agent_role: str, skill_id: str) -> None:
        equip = self._load_equipment(tenant_id)
        if agent_role in equip and skill_id in equip[agent_role]:
            equip[agent_role].remove(skill_id)
            self._save_equipment(tenant_id, equip)

    # ── Orchestrator 会话（V1.3） ────────────────────────────────────────
    # sidecar: config/<tenant>/lifecycle_orchestrator_sessions.json
    # 读-改-写在 _SIDECAR_LOCK 内串行（对齐 generated_posts 的 OCC 写路径）。

    _SESSION_FIELDS = ("session_id", "tenant_id", "goal_id", "status",
                       "messages", "proposed_plan", "decision_cards", "dag_id",
                       "trace", "pending", "rev", "created_at", "updated_at")

    def create_session(self, tenant_id: str, *, session_id: str,
                        goal_id: Optional[str] = None,
                        status: str = "gathering",
                        messages: Optional[list] = None,
                        proposed_plan: Optional[list] = None,
                        decision_cards: Optional[list] = None,
                        dag_id: Optional[str] = None,
                        trace: Optional[list] = None,
                        pending: Optional[dict] = None) -> dict:
        tenant_id = _require_tenant(tenant_id)
        now = datetime.now(timezone.utc).isoformat()
        item = {
            "session_id": session_id,
            "tenant_id": tenant_id,
            "goal_id": goal_id,
            "status": status,
            "messages": list(messages or []),
            "proposed_plan": list(proposed_plan or []),
            "decision_cards": list(decision_cards or []),
            "dag_id": dag_id,
            "trace": list(trace or []),
            "pending": pending,
            "rev": 1,
            "created_at": now,
            "updated_at": now,
        }
        with _SIDECAR_LOCK:
            data = self._load_lifecycle(tenant_id, "orchestrator_sessions")
            data[session_id] = item
            self._save_lifecycle(tenant_id, "orchestrator_sessions", data)
        return dict(item)

    def get_session(self, tenant_id: str, session_id: str) -> Optional[dict]:
        tenant_id = _require_tenant(tenant_id)
        data = self._load_lifecycle(tenant_id, "orchestrator_sessions")
        item = data.get(session_id)
        # 租户隔离双保险：sidecar 已按租户分目录，再校验一次写入的 tenant_id
        if item is None or item.get("tenant_id") != tenant_id:
            return None
        return dict(item)

    def update_session(self, tenant_id: str, session_id: str, *,
                       expected_rev: int, **changes: Any) -> dict:
        tenant_id = _require_tenant(tenant_id)
        allowed = {"goal_id", "status", "messages", "proposed_plan",
                   "decision_cards", "dag_id", "trace", "pending"}
        list_keys = ("messages", "proposed_plan", "decision_cards", "trace")
        with _SIDECAR_LOCK:
            data = self._load_lifecycle(tenant_id, "orchestrator_sessions")
            item = data.get(session_id)
            if item is None or item.get("tenant_id") != tenant_id:
                raise KeyError(f"session '{session_id}' not found")
            current_rev = int(item.get("rev", 0) or 0)
            if current_rev != expected_rev:
                raise RevMismatch(f"expected rev={expected_rev}, actual rev={current_rev}")
            for k, v in changes.items():
                if k in allowed:
                    item[k] = list(v) if k in list_keys and v is not None else v
            item["rev"] = current_rev + 1
            item["updated_at"] = datetime.now(timezone.utc).isoformat()
            data[session_id] = item
            self._save_lifecycle(tenant_id, "orchestrator_sessions", data)
        return dict(item)

    def list_sessions(self, tenant_id: str, *, goal_id: Optional[str] = None,
                      limit: int = 20) -> list[dict]:
        tenant_id = _require_tenant(tenant_id)
        data = self._load_lifecycle(tenant_id, "orchestrator_sessions")
        items = [v for v in data.values() if v.get("tenant_id") == tenant_id]
        if goal_id is not None:
            items = [v for v in items if v.get("goal_id") == goal_id]
        items.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
        return [dict(i) for i in items[:limit]]

    def delete_session(self, tenant_id: str, session_id: str) -> bool:
        tenant_id = _require_tenant(tenant_id)
        with _SIDECAR_LOCK:
            data = self._load_lifecycle(tenant_id, "orchestrator_sessions")
            item = data.get(session_id)
            # 租户隔离双保险：sidecar 已按租户分目录，再校验一次 tenant_id
            if item is None or item.get("tenant_id") != tenant_id:
                return False
            del data[session_id]
            self._save_lifecycle(tenant_id, "orchestrator_sessions", data)
        return True

    # ── 线索雷达 leads（lead-intent-radar V1）─────────────────────────────
    # sidecar: config/<tenant>/lifecycle_leads.json
    # 独立于 collected_notes；仅持久化「通过意图判定的合格线索」。
    # 读-改-写在 _SIDECAR_LOCK 内串行（对齐 orchestrator_sessions 的 OCC 写路径）。

    _LEAD_FIELDS = ("lead_id", "tenant_id", "goal_id", "persona_id",
                    "source", "source_url", "signal_key", "author", "posted_at",
                    "post_text", "excerpt", "detected_at",
                    "is_intent", "match_score", "trigger_type", "judge_reason",
                    "draft_text", "check_lure_pass", "check_dup_pass",
                    "lead_status", "touched_at", "outcome",
                    "sent_at", "send_platform_id", "send_engine",
                    "meta", "rev", "created_at", "updated_at")

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
                    meta: Optional[dict] = None) -> dict:
        """创建合格线索。signal_key 幂等：同一原帖重复采集只生成一条 lead（返回已存在的）。"""
        tenant_id = _require_tenant(tenant_id)
        if not signal_key or not str(signal_key).strip():
            raise ValueError("signal_key is required (去重键)")
        signal_key = str(signal_key).strip()
        now = datetime.now(timezone.utc).isoformat()
        with _SIDECAR_LOCK:
            data = self._load_lifecycle(tenant_id, "leads")
            # 幂等去重：signal_key 已存在则直接返回，不新建
            for existing in data.values():
                if existing.get("signal_key") == signal_key:
                    return dict(existing)
            lead_id = f"lead_{uuid.uuid4().hex[:12]}"
            item = {
                "lead_id": lead_id,
                "tenant_id": tenant_id,
                "goal_id": goal_id,
                "persona_id": persona_id,
                "source": source,
                "source_url": source_url,
                "signal_key": signal_key,
                "author": author,
                "posted_at": posted_at,
                "post_text": post_text,
                "excerpt": excerpt,
                "detected_at": detected_at or now,
                "is_intent": bool(is_intent),
                "match_score": match_score,
                "trigger_type": trigger_type,
                "judge_reason": judge_reason,
                "draft_text": draft_text,
                "check_lure_pass": bool(check_lure_pass),
                "check_dup_pass": bool(check_dup_pass),
                "lead_status": lead_status,
                "touched_at": None,
                "outcome": None,
                "sent_at": None,           # V2: 真发时间戳（dryrun 不写）
                "send_platform_id": None,  # V2: 平台返回的评论 id
                "send_engine": None,       # V2: 真发引擎（reajason）
                "meta": meta,
                "rev": 1,
                "created_at": now,
                "updated_at": now,
            }
            data[lead_id] = item
            self._save_lifecycle(tenant_id, "leads", data)
        return dict(item)

    def get_lead(self, tenant_id: str, lead_id: str) -> Optional[dict]:
        tenant_id = _require_tenant(tenant_id)
        data = self._load_lifecycle(tenant_id, "leads")
        item = data.get(lead_id)
        # 租户隔离双保险
        if item is None or item.get("tenant_id") != tenant_id:
            return None
        return dict(item)

    def list_leads(self, tenant_id: str, *, goal_id: Optional[str] = None,
                   lead_status: Optional[str] = None,
                   trigger_type: Optional[str] = None,
                   limit: int = 50) -> list[dict]:
        """收件箱列表。默认按 检测延迟新鲜度 × 匹配度 排序（新鲜在前、匹配度高在前）。"""
        tenant_id = _require_tenant(tenant_id)
        data = self._load_lifecycle(tenant_id, "leads")
        items = [v for v in data.values() if v.get("tenant_id") == tenant_id]
        if goal_id is not None:
            items = [v for v in items if v.get("goal_id") == goal_id]
        if lead_status is not None:
            items = [v for v in items if v.get("lead_status") == lead_status]
        if trigger_type is not None:
            items = [v for v in items if v.get("trigger_type") == trigger_type]
        # 新鲜度（detected_at DESC）为主，匹配度（match_score DESC）为辅
        items.sort(key=lambda x: (
            x.get("detected_at") or "",
            x.get("match_score") or 0,
        ), reverse=True)
        return [dict(i) for i in items[:limit]]

    def update_lead(self, tenant_id: str, lead_id: str, *,
                    expected_rev: int, **changes: Any) -> dict:
        """OCC 更新。支持状态流转（pending/touched/skipped）+ outcome（沟通机会/成交）。"""
        tenant_id = _require_tenant(tenant_id)
        allowed = {"goal_id", "persona_id", "draft_text",
                   "check_lure_pass", "check_dup_pass",
                   "lead_status", "touched_at", "outcome",
                   "sent_at", "send_platform_id", "send_engine",
                   "match_score", "trigger_type", "judge_reason", "meta"}
        with _SIDECAR_LOCK:
            data = self._load_lifecycle(tenant_id, "leads")
            item = data.get(lead_id)
            if item is None or item.get("tenant_id") != tenant_id:
                raise KeyError(f"lead '{lead_id}' not found")
            current_rev = int(item.get("rev", 0) or 0)
            if current_rev != expected_rev:
                raise RevMismatch(f"expected rev={expected_rev}, actual rev={current_rev}")
            for k, v in changes.items():
                if k in allowed:
                    item[k] = v
            item["rev"] = current_rev + 1
            item["updated_at"] = datetime.now(timezone.utc).isoformat()
            data[lead_id] = item
            self._save_lifecycle(tenant_id, "leads", data)
        return dict(item)

    def delete_lead(self, tenant_id: str, lead_id: str) -> bool:
        tenant_id = _require_tenant(tenant_id)
        with _SIDECAR_LOCK:
            data = self._load_lifecycle(tenant_id, "leads")
            item = data.get(lead_id)
            if item is None or item.get("tenant_id") != tenant_id:
                return False
            del data[lead_id]
            self._save_lifecycle(tenant_id, "leads", data)
        return True
