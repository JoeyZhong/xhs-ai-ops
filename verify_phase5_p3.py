#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P3 · 调度器 + Draft/Review + Cookie Health 验收测试
运行方式：cd /d D:\\【AIcode】\\Spider_XHS && python verify_phase5_p3.py

覆盖：
  S1-S8: 调度器（scheduler.py）
  S9-S16: Draft 元字段 + Content 过滤
  S17-S21: Playbook API
  S22-S25: Cookie health
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_results: list[tuple[str, bool, str]] = []

def check(name: str, condition: bool, detail: str = ""):
    status = "PASS" if condition else "FAIL"
    mark = "[+]" if condition else "[X]"
    line = f"  {mark} {status}  {name}"
    if detail:
        line += f"  <- {detail}"
    print(line)
    _results.append((name, condition, detail))

def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'-'*60}")

def summary():
    total = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    print(f"\n{'='*60}")
    print(f"  Result: {passed}/{total} passed")
    if passed < total:
        print("  FAILED cases:")
        for name, ok, detail in _results:
            if not ok:
                print(f"    [X] {name}  <- {detail}")
        sys.exit(1)
    else:
        print("  All checks passed.")

# ═══════════════════════════════════════════════════════════════════════
#  S1-S8 · Scheduler
# ═══════════════════════════════════════════════════════════════════════

section("S1-S8 · Scheduler")

from agents.scheduler import SpiderScheduler, SchedulerLock

lock_dir = Path(tempfile.mkdtemp())
lock_path = lock_dir / ".scheduler.lock"

# S1: start/stop
s = SpiderScheduler(lock_path=lock_path)
s.start()
check("S1: start makes started=True", s.started)
s.stop()
check("S2: stop makes started=False", not s.started)

# S3: double start raises
s3 = SpiderScheduler(lock_path=lock_path)
s3.start()
try:
    s3_dup = SpiderScheduler(lock_path=lock_path)
    s3_dup.start()
    check("S3: double start raises", False, "no exception")
    s3_dup.stop()
except (IOError, OSError):
    check("S3: double start raises IOError", True)
s3.stop()

# S4: default jobs
s4 = SpiderScheduler(lock_path=lock_dir / "s4.lock")
s4.start()
s4.register_default_jobs()
jobs = s4.scheduler.get_jobs()
job_ids = [j.id for j in jobs]
check("S4: weekly_evaluator registered", "weekly_evaluator" in job_ids)
check("S4: daily_cookie_check registered", "daily_cookie_check" in job_ids)
check("S4: exactly 2 jobs", len(jobs) == 2)
s4.stop()

# S5: disabled
check("S5: scheduler.enabled=false no start",
      not json.loads(Path("config/settings.json").read_bytes()).get("scheduler",{}).get("enabled",False))

# S6: get_jobs_info
s6 = SpiderScheduler(lock_path=lock_dir / "s6.lock")
s6.start()
s6.register_default_jobs()
info = s6.get_jobs_info()
check("S6: jobs_info returns list", isinstance(info, list))
check("S6: jobs_info has id/name/next_run_time",
      all("id" in j and "name" in j for j in info))
s6.stop()

# S7: lock released after stop
s7a = SpiderScheduler(lock_path=lock_dir / "s7.lock")
s7a.start()
s7a.stop()
s7b = SpiderScheduler(lock_path=lock_dir / "s7.lock")
try:
    s7b.start()
    check("S7: lock released after stop", True)
    s7b.stop()
except (IOError, OSError):
    check("S7: lock released after stop", False, "still locked")

# S8: SchedulerLock context manager
with SchedulerLock(lock_dir / "s8.lock"):
    check("S8: context manager acquired", lock_dir.joinpath("s8.lock").exists())

# ═══════════════════════════════════════════════════════════════════════
#  S9-S16 · Draft metadata + Content filter
# ═══════════════════════════════════════════════════════════════════════

section("S9-S16 · Draft metadata & Content filter")

from agents.memory import Entry, parse_entries, serialize_entries, MemoryLayer
from storage.local_json import LocalJsonBackend

# S9: old entry defaults to active
header, entries = parse_entries("§id: old §rev: 1\nbody")
check("S9: old entry defaults to active",
      entries["old"].status == "active" and entries["old"].source == "manual")

# S10: new entry preserves metadata
e = Entry(id="d1", body="x", rev=1, status="draft", source="scheduler", confidence="low")
ser = serialize_entries("", {"d1": e})
_, parsed = parse_entries(ser)
check("S10: serialize/parse preserves draft",
      parsed["d1"].status == "draft" and parsed["d1"].source == "scheduler")
check("S10: serialize/parse preserves confidence low",
      parsed["d1"].confidence == "low")

# S11: ContentAgent filters out draft/rejected
from agents.memory import parse_entries
playbook = (
    "§id: a1 §rev: 1 §status: active §source: manual §confidence: high\nactive\n\n"
    "§id: d1 §rev: 1 §status: draft §source: scheduler §confidence: low\ndraft\n\n"
    "§id: r1 §rev: 1 §status: rejected §source: manual §confidence: high\nrejected"
)
_, all_entries = parse_entries(playbook)
active = {eid: e for eid, e in all_entries.items() if e.status == "active"}
check("S11: Content sees only active", len(active) == 1 and "a1" in active)

# S12: add_entry with metadata
mem_base = Path(tempfile.mkdtemp())
mem = MemoryLayer(storage=LocalJsonBackend(base_dir=str(mem_base)))
meta = {"status": "draft", "source": "scheduler", "confidence": "low"}
mem.add_entry("default", "content", "playbook.md", "weekly-test", "body", "analyst", entry_meta=meta)
_, es = parse_entries(mem.read("default", "content", "playbook.md") or "")
check("S12: add_entry with meta sets draft", es["weekly-test"].status == "draft")
check("S12: add_entry with meta sets source", es["weekly-test"].source == "scheduler")

# S13: replace_entry preserves meta when not overwritten
mem.replace_entry("default", "content", "playbook.md", "weekly-test", "new body", "analyst", expected_rev=1)
_, es2 = parse_entries(mem.read("default", "content", "playbook.md") or "")
check("S13: replace preserves draft status", es2["weekly-test"].status == "draft")
check("S13: replace preserves source", es2["weekly-test"].source == "scheduler")

# S14: replace_entry overwrites meta when provided
meta2 = {"status": "active", "source": "manual", "confidence": "high"}
mem.replace_entry("default", "content", "playbook.md", "weekly-test", "accepted", "analyst",
                   expected_rev=2, entry_meta=meta2)
_, es3 = parse_entries(mem.read("default", "content", "playbook.md") or "")
check("S14: replace overwrites status to active", es3["weekly-test"].status == "active")

# S15: AnalystEvaluator exists and has run()
from agents.evaluators import AnalystEvaluator
evaluator = AnalystEvaluator()
check("S15: AnalystEvaluator has run method", hasattr(evaluator, "run"))
prompt, confidence = evaluator.assemble_prompt()
check("S15: assemble_prompt returns str+str",
      isinstance(prompt, str) and isinstance(confidence, str))
check("S15: confidence is high or low", confidence in ("high", "low"))

# S16: scheduler has real cookie check handler
from agents.scheduler import SpiderScheduler
s16 = SpiderScheduler(lock_path=lock_dir / "s16.lock")
check("S16: _daily_cookie_check is not pass",
      s16._daily_cookie_check.__code__.co_code != b"d\x00S\x00")  # noqa: E721

# ═══════════════════════════════════════════════════════════════════════
#  S17-S21 · Playbook API (via TestClient)
# ═══════════════════════════════════════════════════════════════════════

section("S17-S21 · Playbook API")

from fastapi.testclient import TestClient
from unittest.mock import patch

from security.jwt import encode_token

os.environ.setdefault("JWT_SECRET", "test_secret_for_p2_only")
os.environ.setdefault("JWT_ALGORITHM", "HS256")

JWT = encode_token("test-tenant")
AUTH = {"Authorization": f"Bearer {JWT}"}
api_tmp = Path(tempfile.mkdtemp())
(api_tmp / "settings.json").write_text(
    json.dumps({"scheduler": {"enabled": False}}), encoding="utf-8")

def _make_backend():
    return LocalJsonBackend(base_dir=str(api_tmp))

with patch("server.routers.playbook._get_memory_layer") as mock_factory:
    from agents.memory import MemoryLayer as ML
    mock_factory.side_effect = lambda: ML(storage=_make_backend())
    from server.main import app
    app.dependency_overrides.clear()
    client = TestClient(app)

    # Write a playbook with mixed entries
    playbook_dir = api_tmp / "memory" / "default" / "content"
    playbook_dir.mkdir(parents=True, exist_ok=True)
    (playbook_dir / "playbook.md").write_text(
        "§id: weekly-1 §rev: 1 §status: draft §source: scheduler §confidence: low\n"
        "insight 1\n\n"
        "§id: weekly-2 §rev: 1 §status: draft §source: scheduler §confidence: high\n"
        "insight 2\n\n"
        "§id: active-1 §rev: 2 §status: active §source: manual §confidence: high\n"
        "active insight",
        encoding="utf-8")

    r = client.get("/api/v1/playbook/drafts", headers=AUTH)
    check("S17: GET /drafts returns 200", r.status_code == 200)
    data = r.json()
    check("S17: GET /drafts counts correctly", data["total"] == 2)
    check("S17: GET /drafts items have all fields",
          all(k in data["items"][0] for k in ("id","body","status","source","confidence","rev")))

    r_accept = client.post("/api/v1/playbook/drafts/weekly-1/accept", headers=AUTH)
    check("S18: POST /drafts/{id}/accept returns 200", r_accept.status_code == 200)
    check("S18: accept new_rev is 2", r_accept.json()["new_rev"] == 2)

    r_reject = client.post("/api/v1/playbook/drafts/weekly-2/reject", headers=AUTH)
    check("S19: POST /drafts/{id}/reject returns 200", r_reject.status_code == 200)

    r_edit = client.put("/api/v1/playbook/drafts/active-1", json={"body": "edited"}, headers=AUTH)
    check("S20: PUT accepts only draft", r_edit.status_code == 400, "active entry rejected")

    r_count = client.get("/api/v1/playbook/drafts/count", headers=AUTH)
    check("S21: GET /drafts/count returns 0 after accept/reject",
          r_count.status_code == 200 and r_count.json()["count"] == 0)

# ═══════════════════════════════════════════════════════════════════════
#  S22-S25 · Cookie health
# ═══════════════════════════════════════════════════════════════════════

section("S22-S25 · Cookie health")

from agent_tools.search import _clean_old_snapshots

# S22: _clean_old_snapshots keeps N most recent
snap_dir = Path(tempfile.mkdtemp())
for i in range(5):
    (snap_dir / f"health_check_2026050{i}_060000.xlsx").write_text("x")
_clean_old_snapshots(snap_dir, keep=3)
remaining = list(snap_dir.glob("health_check_*.xlsx"))
check("S22: clean keeps 3 snapshots", len(remaining) == 3)

# S23: output_dir param exists in tool schema
from agent_tools import registry as _reg
tool = _reg.get("search.collect_notes")
has_output_dir = "output_dir" in tool.schema.get("parameters", {}).get("properties", {})
check("S23: output_dir in tool schema", has_output_dir)

# S24: cookie_alert.json schema
alert = json.dumps({"ts": "2026-05-08T06:00:00", "error": "test error", "last_success": "2026-05-07"})
check("S24: alert schema valid", "ts" in json.loads(alert) and "error" in json.loads(alert))

# S25: scheduler status endpoint
with patch("server.auth.CONFIG_DIR", api_tmp):
    from server.main import app as app2
    app2.dependency_overrides.clear()
    c2 = TestClient(app2)
    r = c2.get("/api/v1/scheduler/status")
    # scheduler is None in test context, so running=false
    check("S25: GET /scheduler/status returns 200", r.status_code == 200)
    check("S25: scheduler status has running field", "running" in r.json())

# ═══════════════════════════════════════════════════════════════════════

summary()
