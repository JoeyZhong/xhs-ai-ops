"""P1 测试 · orchestrator-coordinator 协调内核(agents/orchestrator_agent.py)

用脚本化 fake LLM 驱动不同分支，证明**不僵化**：
纯问答→不调子 agent；单一意图→只调一个；复合→多步串联；信息不足→追问暂停可恢复；防失控。
子 agent 用 stub master(不真跑 Agent)。会话走 tmp LocalJsonBackend。
"""
from __future__ import annotations

import json

from agents.orchestrator_agent import run_turn
from storage.local_json import LocalJsonBackend


# ── fake LLM / tool_call 构件 ──────────────────────────────────────────────

class _FakeFunc:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeTC:
    def __init__(self, name, args):
        self.id = f"call_{name}_{id(self) & 0xffff}"
        self.function = _FakeFunc(name, json.dumps(args, ensure_ascii=False))


class _FakeMsg:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


def _tc(name, **args):
    return _FakeTC(name, args)


def _scripted(*responses):
    """每次调用按序返回一个 _FakeMsg；耗尽后回退到一个 finish 兜底。"""
    box = {"i": 0}

    def _llm(*, messages, tools, **kw):
        i = box["i"]
        box["i"] += 1
        if i < len(responses):
            return responses[i], None, 100
        return _FakeMsg(tool_calls=[_tc("finish", summary="(脚本耗尽兜底)")]), None, 100

    return _llm


# ── stub master ────────────────────────────────────────────────────────────

class _Result:
    def __init__(self, ok=True, content="结果", error=None):
        self.ok = ok
        self.content = content
        self.error = error


class _StubMaster:
    def __init__(self):
        self.calls = []

    def submit(self, task, progress_cb=None):
        self.calls.append(task)
        return _Result(content=f"{task.type} 子 agent 产出")


def _backend(tmp_path, with_goal=True):
    b = LocalJsonBackend(base_dir=str(tmp_path))
    goals = [{"id": "goal_001", "name": "B端招商",
              "overall_strategy": {"core_message": "闲置场地换被动收入"}}] if with_goal else []
    b.save_goals("default", {"active_goal_id": "goal_001", "goals": goals})
    return b


def _types(master):
    return [t.type for t in master.calls]


# ── 1. 纯问答：不调任何子 agent ───────────────────────────────────────────

def test_pure_qa_no_subagent(tmp_path):
    b = _backend(tmp_path)
    master = _StubMaster()
    events = []
    llm = _scripted(_FakeMsg(content="自助售卖机点位招商的关键是选对人流。"))  # 无 tool_call
    view = run_turn(backend=b, tenant_id="default", message="点位招商要注意啥？",
                    goal_id="goal_001", emit=events.append, llm=llm, master=master)
    assert view["status"] == "done"
    assert master.calls == []                              # 没调子 agent
    kinds = [e["type"] for e in events]
    assert "subagent_start" not in kinds
    assert "final" in kinds and kinds[-1] == "done"


# ── 2. 单一意图：只调 analyst ─────────────────────────────────────────────

def test_single_subagent_only_analyst(tmp_path):
    b = _backend(tmp_path)
    master = _StubMaster()
    events = []
    llm = _scripted(
        _FakeMsg(tool_calls=[_tc("run_subagent", archetype="analyst", task="复盘上周数据")]),
        _FakeMsg(tool_calls=[_tc("finish", summary="上周反直觉型角度 CES 最高，建议沿用。")]),
    )
    view = run_turn(backend=b, tenant_id="default", message="看看上周哪篇数据最好",
                    goal_id="goal_001", emit=events.append, llm=llm, master=master)
    assert view["status"] == "done"
    assert _types(master) == ["analyst"]                  # 只调了 analyst
    assert any(e["type"] == "final" for e in events)


# ── 3. 复合意图：intel→analyst→content 多步 ───────────────────────────────

def test_multistep_pipeline(tmp_path):
    b = _backend(tmp_path)
    master = _StubMaster()
    events = []
    llm = _scripted(
        _FakeMsg(tool_calls=[_tc("run_subagent", archetype="intel", task="采集工厂物业笔记")]),
        _FakeMsg(tool_calls=[_tc("run_subagent", archetype="analyst", task="找高CES共性")]),
        _FakeMsg(tool_calls=[_tc("run_subagent", archetype="content", task="写3篇草稿")]),
        _FakeMsg(tool_calls=[_tc("finish", summary="已产出3篇贴合爆款角度的草稿。")]),
    )
    view = run_turn(backend=b, tenant_id="default", message="规划并写一批面向工厂物业的内容",
                    goal_id="goal_001", emit=events.append, llm=llm, master=master)
    assert view["status"] == "done"
    assert _types(master) == ["intel", "analyst", "content"]   # 顺序多步


# ── 4. 信息不足→追问暂停→答复恢复(不重跑) ─────────────────────────────────

def test_ask_user_pause_and_resume(tmp_path):
    b = _backend(tmp_path)
    master = _StubMaster()

    # 第一轮：先采集，再追问
    llm1 = _scripted(
        _FakeMsg(tool_calls=[_tc("run_subagent", archetype="intel", task="先采集")]),
        _FakeMsg(tool_calls=[_tc("ask_user", question="主推园区物业还是写字楼行政？")]),
    )
    v1 = run_turn(backend=b, tenant_id="default", message="帮我搞批内容",
                  goal_id="goal_001", llm=llm1, master=master)
    assert v1["status"] == "awaiting_user"
    assert v1["pending"]["question"].startswith("主推")
    assert _types(master) == ["intel"]                    # 采集跑了一次
    sid = v1["session_id"]

    # 第二轮：带 session_id + 答复 → 续跑到 finish；intel 不重跑
    llm2 = _scripted(
        _FakeMsg(tool_calls=[_tc("run_subagent", archetype="content", task="按园区物业写")]),
        _FakeMsg(tool_calls=[_tc("finish", summary="已按园区物业产出草稿。")]),
    )
    v2 = run_turn(backend=b, tenant_id="default", message="园区物业",
                  session_id=sid, llm=llm2, master=master)
    assert v2["status"] == "done"
    assert _types(master) == ["intel", "content"]         # intel 没重跑，新增 content
    assert v2["pending"] is None


# ── 5. 决策卡暂停 ─────────────────────────────────────────────────────────

def test_decision_card_pause(tmp_path):
    b = _backend(tmp_path)
    master = _StubMaster()
    llm = _scripted(
        _FakeMsg(tool_calls=[_tc("raise_decision_card", title="确认发布？", detail="将对外发3篇")]),
    )
    v = run_turn(backend=b, tenant_id="default", message="直接发出去",
                 goal_id="goal_001", llm=llm, master=master)
    assert v["status"] == "awaiting_decision"
    assert v["pending"]["card"]["title"] == "确认发布？"


# ── 6. 防失控：迭代上限优雅收尾 ───────────────────────────────────────────

def test_iteration_cap_graceful(tmp_path):
    b = _backend(tmp_path)
    master = _StubMaster()
    # llm 永远要 run_subagent，从不 finish；max_iterations=3 → 必须优雅收尾
    forever = [_FakeMsg(tool_calls=[_tc("run_subagent", archetype="intel", task="再采")])
               for _ in range(10)]
    llm = _scripted(*forever)
    v = run_turn(backend=b, tenant_id="default", message="一直采",
                 goal_id="goal_001", llm=llm, master=master, max_iterations=3)
    assert v["status"] == "done"
    assert len(master.calls) <= 3                          # 没失控无限调


# ── 7. 未知 archetype 被拒(不调 master) ───────────────────────────────────

def test_unknown_archetype_rejected(tmp_path):
    b = _backend(tmp_path)
    master = _StubMaster()
    llm = _scripted(
        _FakeMsg(tool_calls=[_tc("run_subagent", archetype="evil", task="干坏事")]),
        _FakeMsg(tool_calls=[_tc("finish", summary="该能力不可用。")]),
    )
    v = run_turn(backend=b, tenant_id="default", message="用 evil",
                 goal_id="goal_001", llm=llm, master=master)
    assert v["status"] == "done"
    assert master.calls == []                              # 非法 archetype 没落到 master


# ── 8. trace 落库可恢复 ───────────────────────────────────────────────────

def test_trace_persisted_for_recovery(tmp_path):
    b = _backend(tmp_path)
    master = _StubMaster()
    llm = _scripted(
        _FakeMsg(tool_calls=[_tc("run_subagent", archetype="analyst", task="析")]),
        _FakeMsg(tool_calls=[_tc("finish", summary="done")]),
    )
    v = run_turn(backend=b, tenant_id="default", message="析一下",
                 goal_id="goal_001", llm=llm, master=master)
    # 重新从 backend 读 session，trace 应可恢复
    reloaded = b.get_session("default", v["session_id"])
    kinds = [e["type"] for e in reloaded["trace"]]
    assert "subagent_start" in kinds and "final" in kinds and kinds[-1] == "done"
