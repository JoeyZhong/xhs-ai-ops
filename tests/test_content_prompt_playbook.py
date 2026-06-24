"""P3.4 · playbook_summary 注入测试。

- prompt_context.build_strategy_prompt_context 返回 playbook_summary
  （读 memory content/playbook.md 的 <!-- analyst-auto: v2 --> 块，截断 ~500 字）
- /content/strategy + /content/generate prompt 含 playbook 段；缺失时优雅省略
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from security.jwt import encode_token
from agents.playbook_learning import AUTO_BEGIN, AUTO_END

JWT = encode_token("default")
AUTH = {"Authorization": f"Bearer {JWT}"}


def _wauth() -> dict:
    return {**AUTH, "Idempotency-Key": uuid.uuid4().hex}


# ── 单元：prompt_context playbook_summary ──────────────────────────────

class _FakeBackend:
    def __init__(self, playbook_text: str | None):
        self._pb = playbook_text

    def load_memory(self, tenant_id, scope, file):
        if scope == "content" and file == "playbook.md":
            return self._pb
        return None

    def get_topic(self, tenant_id, topic_id):
        raise KeyError(topic_id)

    def list_evidence(self, tenant_id, **kw):
        return []


def _ctx(backend):
    from agent_tools.prompt_context import build_strategy_prompt_context
    return build_strategy_prompt_context(
        backend=backend, tenant_id="default", goal={"id": "g1"}, topic_id=None,
    )


def test_playbook_summary_extracts_auto_block():
    pb = f"# 手写区\n忽略我。\n\n{AUTO_BEGIN}\n反直觉型 — 已验证爆款\n{AUTO_END}\n"
    ctx = _ctx(_FakeBackend(pb))
    assert "反直觉型" in ctx["playbook_summary"]
    assert "忽略我" not in ctx["playbook_summary"]  # 只取自动区


def test_playbook_summary_empty_when_no_block():
    ctx = _ctx(_FakeBackend("# 只有手写区\n没有自动块。"))
    assert ctx["playbook_summary"] == ""


def test_playbook_summary_empty_when_no_playbook():
    ctx = _ctx(_FakeBackend(None))
    assert ctx["playbook_summary"] == ""


def test_playbook_summary_truncated():
    long_body = "角度" * 600  # 1200 chars
    pb = f"{AUTO_BEGIN}\n{long_body}\n{AUTO_END}"
    ctx = _ctx(_FakeBackend(pb))
    assert len(ctx["playbook_summary"]) <= 500


# ── 集成：content prompt 注入（照 test_content_prompt_evidence 范式：
#    llm_provider=kimi 让 strategy 端点真走 call_kimi，patch 才能拦到 prompt）──

_GOAL = {"id": "g1", "name": "t", "status": "active",
         "brand_position": "深圳售卖机", "target_audience": {"who": "工厂", "pain_points": "闲置"},
         "keywords": ["kw"], "keyword_library": []}


def _setup(tmp_path, monkeypatch, *, with_playbook: bool):
    from server.middleware.idempotency import clear_idempotency_caches_for_tests
    clear_idempotency_caches_for_tests()
    (tmp_path / "settings.json").write_text('{"llm_provider":"kimi"}', encoding="utf-8")
    from server.routers import content as content_router
    monkeypatch.setattr(content_router, "CONFIG_DIR", tmp_path)

    from storage.local_json import LocalJsonBackend
    import storage.factory
    backend = LocalJsonBackend(base_dir=str(tmp_path))
    monkeypatch.setattr(storage.factory, "get_backend", lambda: backend)
    backend.save_goals("default", {"active_goal_id": "g1", "goals": [_GOAL]})
    if with_playbook:
        backend.save_memory("default", "content", "playbook.md",
                            f"{AUTO_BEGIN}\nPLAYBOOK_MARKER 反直觉型已验证\n{AUTO_END}")
    return backend


def test_strategy_prompt_injects_playbook_after_packaging(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, with_playbook=True)
    captured = {}

    def fake_call_kimi(prompt, **kw):
        captured["prompt"] = prompt
        return '{"angle":"反直觉型","hook":"h","key_points":["k"],"cta":"c"}', None

    monkeypatch.setattr("agent_tools.kimi.call_kimi", fake_call_kimi)

    from server.main import app
    c = TestClient(app)
    r = c.post("/api/v1/content/strategy", headers=_wauth(),
               json={"goal_id": "g1", "keywords": ["kw"], "user_intent": "test"})
    assert r.status_code == 200, r.text
    prompt = captured["prompt"]
    assert "PLAYBOOK_MARKER" in prompt
    assert "── 已验证爆款规律（playbook）──" in prompt
    # 注入位置：包装规则段之后
    assert prompt.index("── 包装规则") < prompt.index("── 已验证爆款规律（playbook）──")


def test_strategy_prompt_omits_playbook_when_absent(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, with_playbook=False)
    captured = {}

    def fake_call_kimi(prompt, **kw):
        captured["prompt"] = prompt
        return '{"angle":"反直觉型","hook":"h","key_points":["k"],"cta":"c"}', None

    monkeypatch.setattr("agent_tools.kimi.call_kimi", fake_call_kimi)

    from server.main import app
    c = TestClient(app)
    r = c.post("/api/v1/content/strategy", headers=_wauth(),
               json={"goal_id": "g1", "keywords": ["kw"], "user_intent": "test"})
    assert r.status_code == 200, r.text
    assert "playbook" not in captured["prompt"].lower()
