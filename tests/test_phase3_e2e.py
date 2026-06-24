"""Phase 3 P0 e2e: 3.2.6 skip-when-insufficient + 3.4.2 mock dataflow closure."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from agents.base import AgentResult
from agents.content import ContentAgent
from agents.evaluators import AnalystEvaluator
from agents.memory import parse_entries


@pytest.fixture
def env(tmp_path):
    """Isolated tenant: config + xhs_data in tmp_path."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    data_dir = tmp_path / "xhs_data"
    data_dir.mkdir()

    (config_dir / "settings.json").write_text(json.dumps({
        "llm_provider": "mock",
        "kimi_api_key": "test-key",
        "kimi_model": "moonshot-v1-32k",
    }))

    (config_dir / "personas.json").write_text(json.dumps({
        "active_id": "p1",
        "personas": [{"id": "p1", "name": "test", "tone": "test"}],
    }))

    (config_dir / "goals.json").write_text(json.dumps({
        "active_goal_id": "g1",
        "goals": [{
            "id": "g1", "name": "test", "persona_id": "p1",
            "performance": {"posts": []},
        }],
    }))

    settings = {"local_storage_root": str(tmp_path)}
    return {
        "config_dir": config_dir,
        "data_dir": data_dir,
        "tmp_path": tmp_path,
        "settings": settings,
    }


def _set_posts(env, count: int) -> None:
    """Inject N performance posts into goals.json."""
    goals_path = env["config_dir"] / "goals.json"
    data = json.loads(goals_path.read_text(encoding="utf-8"))
    data["goals"][0]["performance"]["posts"] = [
        {"title": f"post_{i}", "likes": 100 + i, "comments": 10 + i,
         "shares": 1, "favorites": 5, "follows": 0}
        for i in range(count)
    ]
    goals_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _make_evaluator(env):
    return AnalystEvaluator(
        tenant_id="default",
        settings=env["settings"],
        data_dir=str(env["data_dir"]),
        config_dir=str(env["config_dir"]),
    )


# ── 3.4.2: Happy path ────────────────────────────────────────────────────

def test_analyst_writes_then_content_reads(env, monkeypatch):
    """3 posts -> analyst writes draft -> promote active -> Content prompt has entry."""
    _set_posts(env, count=3)

    monkeypatch.setattr(
        "agents.master.HermesMaster.submit",
        lambda self, task: AgentResult(
            ok=True,
            content="analysis: high interaction notes use number hooks (like '3 tips')",
        ),
    )
    monkeypatch.setattr(
        "agent_tools.kimi.call_kimi",
        lambda prompt, max_tokens=500: ("- use number hooks in titles\n- guide comments", None),
    )

    evaluator = _make_evaluator(env)
    result = evaluator.run()

    assert result["ok"] is True
    assert result["playbook_written"] is True

    memory = evaluator._memory
    playbook = memory.read("default", "content", "playbook.md") or ""
    assert "number hooks" in playbook
    assert result["entry_id"] in playbook

    # Promote draft -> active (simulate user accept)
    _, entries = parse_entries(playbook)
    target = entries[result["entry_id"]]
    memory.replace_entry(
        "default", "content", "playbook.md",
        result["entry_id"], target.body, "analyst",
        entry_meta={"status": "active", "source": target.source, "confidence": target.confidence},
    )

    # ContentAgent system prompt should contain active playbook entry
    snapshot = {
        "content": {"playbook.md": memory.read("default", "content", "playbook.md") or ""},
        "shared": {},
    }
    sys_prompt = ContentAgent.build_system_prompt(None, snapshot)
    assert "number hooks" in sys_prompt
    assert "playbook" in sys_prompt.lower()


# ── 3.2.6: Skip when insufficient data ──────────────────────────────────

def test_insufficient_data_skips_playbook(env, monkeypatch):
    """2 posts -> skip playbook write + audit insufficient_data."""
    _set_posts(env, count=2)

    monkeypatch.setattr(
        "agents.master.HermesMaster.submit",
        lambda self, task: AgentResult(
            ok=True, content="only 2 posts this week, suggest adding more data",
        ),
    )

    evaluator = _make_evaluator(env)
    result = evaluator.run()

    assert result["ok"] is True
    assert result["playbook_written"] is False

    playbook = evaluator._memory.read("default", "content", "playbook.md") or ""
    assert result["entry_id"] not in playbook

    audit_dir = env["data_dir"] / "audit"
    today = datetime.now().strftime("%Y%m%d")
    audit_file = audit_dir / f"audit_{today}.jsonl"
    assert audit_file.exists()
    events = [json.loads(line) for line in audit_file.read_text(encoding="utf-8").splitlines()]
    insufficient = [e for e in events if e.get("kind") == "insufficient_data"]
    assert len(insufficient) >= 1
    assert insufficient[-1]["posts_count"] == 2
    assert insufficient[-1]["min_required"] == 3


# ── 3.2.6 boundary: exactly 3 posts ──────────────────────────────────────

def test_exactly_three_posts_triggers_write(env, monkeypatch):
    """Exactly 3 posts -> threshold is >=3, should write."""
    _set_posts(env, count=3)

    monkeypatch.setattr(
        "agents.master.HermesMaster.submit",
        lambda self, task: AgentResult(
            ok=True,
            content="analysis: number hooks are effective",
        ),
    )
    monkeypatch.setattr(
        "agent_tools.kimi.call_kimi",
        lambda prompt, max_tokens=500: ("- number hooks\n- comment guides", None),
    )

    evaluator = _make_evaluator(env)
    result = evaluator.run()

    assert result["ok"] is True
    assert result["playbook_written"] is True
    playbook = evaluator._memory.read("default", "content", "playbook.md") or ""
    assert result["entry_id"] in playbook


# ── 3.2.6 edge: zero posts ──────────────────────────────────────────────

def test_zero_posts_skips_with_audit(env, monkeypatch):
    """0 posts -> insufficient_data, not silent skip."""
    _set_posts(env, count=0)

    monkeypatch.setattr(
        "agents.master.HermesMaster.submit",
        lambda self, task: AgentResult(
            ok=True, content="no data",
        ),
    )

    evaluator = _make_evaluator(env)
    result = evaluator.run()

    assert result["ok"] is True
    assert result["playbook_written"] is False

    audit_dir = env["data_dir"] / "audit"
    today = datetime.now().strftime("%Y%m%d")
    audit_file = audit_dir / f"audit_{today}.jsonl"
    assert audit_file.exists()
    events = [json.loads(line) for line in audit_file.read_text(encoding="utf-8").splitlines()]
    assert any(e.get("kind") == "insufficient_data" and e["posts_count"] == 0 for e in events)
