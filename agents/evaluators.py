"""
AnalystEvaluator — scheduler-driven weekly evaluation (P3.2).

Flow:
  1. Read audit logs (7d), performance data, existing playbook
  2. Assemble analyst prompt with has_perf → confidence branch
  3. Submit via Master → Analyst Agent generates insights
  4. Write draft entry to content/playbook.md (status=draft)
  5. Write human-readable weekly report to xhs_data/weekly_reports/<date>.md
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from agents.base import AgentTask
from agents.master import HermesMaster
from agents.memory import parse_entries
from agents.playbook_learning import (
    classify_angles,
    render_auto_block,
    merge_playbook,
    TRISTATE_THRESHOLDS,
)
from agents.used_angles import normalize_used_angles


MIN_PERF_POSTS = 3  # spec: < 3 → skip playbook


class AnalystEvaluator:
    """Weekly evaluation run triggered by scheduler cron (Monday 09:00)."""

    def __init__(
        self,
        tenant_id: str = "default",
        settings: Optional[dict] = None,
        data_dir: Optional[str] = None,
        config_dir: Optional[str] = None,
    ) -> None:
        self._master = HermesMaster(tenant_id=tenant_id, settings=settings)
        self._memory = self._master._memory
        self._tenant_id = tenant_id
        self._data_dir = Path(data_dir) if data_dir else Path("xhs_data")
        self._config_dir = Path(config_dir) if config_dir else Path("config")

    # ── data gathering ──────────────────────────────────────────────────

    def _read_audit_summary(self) -> str:
        """Read last 7 days of audit JSONL logs, return compact summary."""
        cutoff = datetime.now() - timedelta(days=7)
        lines: list[str] = []
        for d in range(8):
            day = cutoff + timedelta(days=d)
            path = (self._data_dir / "audit" / f"audit_{day.strftime('%Y%m%d')}.jsonl")
            if path.exists():
                lines.extend(path.read_text(encoding="utf-8").splitlines())
        events = []
        for line in lines[-500:]:  # cap to last 500 entries
            try:
                events.append(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                continue

        summary_parts: list[str] = []
        kinds: dict[str, int] = {}
        for e in events:
            k = e.get("kind", "unknown")
            kinds[k] = kinds.get(k, 0) + 1
        summary_parts.append(f"Past 7d event counts: {json.dumps(kinds, ensure_ascii=False)}")

        # highlight agent_complete events
        completes = [e for e in events if e.get("kind") == "agent_complete"]
        if completes:
            by_agent: dict[str, int] = {}
            for c in completes:
                by_agent[c.get("agent", "?")] = by_agent.get(c.get("agent", "?"), 0) + 1
            summary_parts.append(f"Agent runs: {json.dumps(by_agent, ensure_ascii=False)}")
            ok_runs = sum(1 for c in completes if c.get("ok", True))
            total_runs = len(completes)
            summary_parts.append(f"Success rate: {ok_runs}/{total_runs}")

        return "\n".join(summary_parts)

    def _read_performance(self) -> tuple[list[dict], bool]:
        """Read performance data from goals.json. Returns (posts, has_enough)."""
        goals_path = self._config_dir / "goals.json"
        if not goals_path.exists():
            return [], False
        try:
            data = json.loads(goals_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            return [], False
        all_posts: list[dict] = []
        for goal in data.get("goals", []):
            perf = goal.get("performance", {})
            posts = perf.get("posts", [])
            for p in posts:
                p["goal_name"] = goal.get("name", "")
                all_posts.append(p)
        return all_posts, len(all_posts) >= MIN_PERF_POSTS

    def _read_playbook(self) -> str:
        """Read active playbook entries."""
        content = self._memory.read(self._tenant_id, "content", "playbook.md") or ""
        _, entries = parse_entries(content)
        parts = []
        for eid, entry in entries.items():
            parts.append(f"[{eid}] {entry.body}")
        return "\n".join(parts) if parts else "(empty)"

    # ── prompt assembly (D4: has_perf branch) ───────────────────────────

    def assemble_prompt(self) -> tuple[str, str]:
        """Assemble analyst prompt. Returns (prompt, confidence)."""
        audit_summary = self._read_audit_summary()
        perf_posts, has_enough = self._read_performance()
        playbook = self._read_playbook()

        date_range = (
            f"{(datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')}"
            f" ~ {datetime.now().strftime('%Y-%m-%d')}"
        )

        if has_enough:
            # 有性能数据 → full prompt
            perf_text = json.dumps(perf_posts[-20:], ensure_ascii=False, indent=2)
            prompt = (
                f"## 任务：本周运营复盘（{date_range}）\n\n"
                "你是分析 Agent，请基于以下数据完成本周复盘，"
                "并把可执行的洞察通过 `memory__write_playbook_entry` 工具"
                "（op=replace, status=draft）写入 playbook。\n\n"
                "### 审计摘要\n"
                f"{audit_summary}\n\n"
                "### 性能数据（过去 20 条帖子）\n"
                f"{perf_text}\n\n"
                "### 现有 playbook\n"
                f"{playbook}\n\n"
                "### 要求\n"
                "1. 分析 CES 计算、互动趋势、高互动共性\n"
                "2. 找出 Top 3 可执行改进建议（每条 ≤ 80 字）\n"
                "3. 用 memory__write_playbook_entry 持久化建议\n"
                "4. 最后用中文给出本周复盘总结"
            )
            confidence = "high"
        else:
            # 数据不足 → audit-only fallback，禁止写 playbook
            prompt = (
                f"## 任务：本周运营回顾（{date_range}）\n\n"
                f"⚠️ 本周已发布笔记 < {MIN_PERF_POSTS} 篇，性能数据不足。\n"
                "**禁止调用 memory__write_playbook_entry 工具**。\n"
                "仅在最终回复中总结审计活动 + 提示用户补数据，不要尝试沉淀洞察。\n\n"
                "### 审计摘要\n"
                f"{audit_summary}\n\n"
                "### 现有 playbook（仅供参考，不要修改）\n"
                f"{playbook}\n\n"
                "### 要求\n"
                "1. 总结本周 Agent 活动概况（≤200 字）\n"
                "2. 提示用户补充至少 3 篇 performance 数据以便下周复盘"
            )
            confidence = "low"

        return prompt, confidence

    # ── main run ────────────────────────────────────────────────────────

    def run(self) -> dict:
        """Execute one evaluation cycle.

        Returns dict with keys: ok, entry_id, confidence, playbook_written, error (optional).
        """
        _, has_enough = self._read_performance()
        prompt, confidence = self.assemble_prompt()
        date_str = datetime.now().strftime("%Y-%m-%d")
        entry_id = f"weekly-{date_str}"

        result = self._master.submit(AgentTask(
            type="analyst", prompt=prompt, max_iterations=15,
            tenant_id=self._tenant_id,
        ))

        if result.ok:
            tristate_updated = False
            if has_enough:
                self._write_draft_entry(entry_id, result.content, confidence)
                # P3.3: CES → playbook 学习闭环（失败不阻断主流程）
                try:
                    learn = self._update_playbook(self._tenant_id)
                    tristate_updated = bool(learn.get("updated"))
                except Exception:
                    tristate_updated = False
            else:
                self._audit_insufficient_data(
                    entry_id, posts_count=len(self._read_performance()[0]),
                )
            self._write_report(entry_id, result.content)
            return {
                "ok": True,
                "entry_id": entry_id,
                "confidence": confidence,
                "playbook_written": has_enough,
                "tristate_updated": tristate_updated,
            }

        return {"ok": False, "error": result.error or "unknown", "entry_id": entry_id}

    def _audit_insufficient_data(self, entry_id: str, posts_count: int) -> None:
        """Record insufficient_data audit event when playbook write is skipped."""
        from agents.audit import make_logger  # noqa: PLC0415

        logger = make_logger(
            self._master._storage,
            tenant_id=self._tenant_id,
            task_id=f"evaluator_{entry_id}",
        )
        logger.write({
            "kind": "insufficient_data",
            "evaluator": "AnalystEvaluator",
            "entry_id": entry_id,
            "posts_count": posts_count,
            "min_required": MIN_PERF_POSTS,
        })

    # ── output helpers ─────────────────────────────────────────────────

    def _write_draft_entry(self, entry_id: str, content: str, confidence: str) -> None:
        """Write analyst output as a draft playbook entry."""
        from agent_tools.kimi import call_kimi  # noqa: PLC0415

        # Shorten the content to a concise actionable summary (≤500 chars)
        summary, _ = call_kimi(
            f"将以下复盘内容浓缩为 3-5 条可执行改进建议，每条 ≤60 字，"
            f"简洁、具体、不说空话：\n\n{content[:2000]}",
            max_tokens=500,
        )
        body = (summary or content)[:600]

        from agent_tools.memory_tools import _get_memory_layer  # noqa: PLC0415
        from agent_tools.registry import ToolContext  # noqa: PLC0415

        ctx = ToolContext(
            tenant_id=self._tenant_id,
            storage=self._master._storage,
            extra={"memory": self._memory, "agent_role": "analyst"},
        )
        mem = _get_memory_layer(ctx)
        meta = {"status": "draft", "source": "scheduler", "confidence": confidence}
        try:
            # Try replace first (OCC retry), fall back to add
            from agent_tools.memory_tools import _replace_with_retry  # noqa: PLC0415

            _replace_with_retry(
                mem, self._tenant_id, "content", "playbook.md",
                entry_id, body, "analyst",
                entry_meta=meta, max_retries=3,
            )
        except Exception:
            try:
                mem.add_entry(
                    self._tenant_id, "content", "playbook.md",
                    entry_id, body, "analyst",
                    entry_meta=meta,
                )
            except Exception:
                pass

    def _write_report(self, entry_id: str, content: str) -> None:
        """Write human-readable weekly report to xhs_data/weekly_reports/."""
        reports_dir = self._data_dir / "weekly_reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        path = reports_dir / f"{entry_id}.md"
        header = (
            f"# Weekly Report — {entry_id}\n\n"
            f"**Generated**: {datetime.now().isoformat(timespec='seconds')}\n"
            f"**Source**: scheduler (AnalystEvaluator)\n\n"
            f"---\n\n"
        )
        path.write_text(header + content, encoding="utf-8")

    # ── P3.3: CES → playbook 学习闭环 ───────────────────────────────────

    def _read_generated_for_learning(self, tenant_id: str) -> list[dict]:
        """读最近窗口的 generated posts，归一成 [{angle, ces_score}]。

        ces_score 优先取 meta.ces_score（performance 回填写入的真实 CES）。
        """
        backend = self._master._storage
        window = timedelta(days=TRISTATE_THRESHOLDS["window_days"])
        since = datetime.now() - window
        try:
            df = backend.list_generated_posts(tenant_id, since=since)
        except TypeError:
            df = backend.list_generated_posts(tenant_id)
        if df is None or df.empty:
            return []

        posts: list[dict] = []
        for rec in df.to_dict("records"):
            angle = str(rec.get("angle") or "").strip()
            if not angle:
                continue
            meta = rec.get("meta") or {}
            if isinstance(meta, dict) and meta.get("ces_score") is not None:
                ces = meta["ces_score"]
            else:
                ces = rec.get("ces_score")
            if ces is None:
                continue  # 无 CES 的草稿不参与判定
            posts.append({"angle": angle, "ces_score": ces})
        return posts

    def _update_playbook(self, tenant_id: str) -> dict:
        """按角度 CES 表现更新 playbook.md 自动区 + goals.used_angles 三态。

        防污染（design §6）：只替换 <!-- analyst-auto: v2 --> 块，保留手写区；
        改写前 backup 到 playbook.md.bak。
        """
        backend = self._master._storage
        posts = self._read_generated_for_learning(tenant_id)
        verdicts = classify_angles(posts)
        if not verdicts:
            return {"updated": False, "reason": "no_qualified_angles"}

        # 1. playbook.md：backup → merge auto block
        existing = backend.load_memory(tenant_id, "content", "playbook.md") or ""
        backend.save_memory(tenant_id, "content", "playbook.md.bak", existing)
        merged = merge_playbook(existing, render_auto_block(verdicts))
        backend.save_memory(tenant_id, "content", "playbook.md", merged)

        # 2. goals.used_angles：把判定写回对应 angle 的 status
        data = backend.load_goals(tenant_id)
        changed = False
        for goal in data.get("goals", []):
            ua = normalize_used_angles(goal.get("used_angles", []))
            for entry in ua:
                v = verdicts.get(entry["angle"])
                if v and entry["status"] != v["status"]:
                    entry["status"] = v["status"]
                    changed = True
            if ua != goal.get("used_angles"):
                goal["used_angles"] = ua
                changed = True
        if changed:
            backend.save_goals(tenant_id, data)

        return {"updated": True, "verdicts": verdicts}
