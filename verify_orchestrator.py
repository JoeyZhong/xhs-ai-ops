#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Orchestrator-Coordinator V1 端到端验收（orchestrator-coordinator P3.2.1）

重写自旧 MVP 版（gathering/planned/dispatched + DAG 确认），对齐**真·协调 Agent** 契约：
  契约：docs/handoff/orchestrator-coordinator-contracts.md §A/§B/§C/§D

覆盖：
  S1 动态分支·纯问答      → 不调任何子 agent，done 收尾
  S2 动态分支·只析        → 只调 analyst
  S3 动态分支·多步        → intel→analyst→content 顺序
  S4 追问暂停 + 答复恢复  → awaiting_user / pending 不被清 / 续跑不重跑
  S5 决策卡暂停           → awaiting_decision / pending.card
  S6 防失控·迭代上限      → 优雅收尾，不无限调
  S7 非法 archetype 拦截  → 不落到 master
  S8 trace 落库可恢复     → reload backend，trace 末尾是 done
  S9 done 终止符 + 结果解读 → 每轮 emit 末尾恰一个 done 且 status 一致；final 是人话非 JSON
  S10 真实 SSE 端点冒烟    → 走 /converse/stream，事件序列以 done 收尾、status 正确

桩 LLM（脚本化）+ 桩 Master（不真跑子 Agent）+ tmp LocalJsonBackend。不打真实 XHS/LLM/PG。

运行：python -X utf8 verify_orchestrator.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from unittest import mock

os.environ.setdefault("JWT_SECRET", "verify-secret-orchestrator")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("STORAGE_BACKEND", "local")

_results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    mark = "[+]" if cond else "[X]"
    s = "PASS" if cond else "FAIL"
    line = f"  {mark} {s}  {name}"
    if detail:
        line += f"  <- {detail}"
    print(line)
    _results.append((name, bool(cond), detail))


def section(title):
    print(f"\n{'='*60}\n  {title}\n{'-'*60}")


def summary():
    total = len(_results)
    ok = sum(1 for _, c, _ in _results if c)
    print(f"\n{'='*60}\n  结果：{ok}/{total} 通过")
    if ok != total:
        print("  失败清单：")
        for n, c, d in _results:
            if not c:
                print(f"    [X] {n}" + (f": {d}" if d else ""))
    else:
        print("  全部通过")
    print('='*60)
    return ok == total


# ── 桩 LLM / tool_call 构件（对齐 tests/test_orchestrator_agent.py） ──────────

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
    """每次调用按序返回一个 (_FakeMsg, None, tokens)；耗尽后回退 finish 兜底。"""
    box = {"i": 0}

    def _llm(*, messages, tools, **kw):
        i = box["i"]
        box["i"] += 1
        if i < len(responses):
            return responses[i], None, 100
        return _FakeMsg(tool_calls=[_tc("finish", summary="(脚本耗尽兜底)")]), None, 100

    return _llm


# ── 桩 Master ────────────────────────────────────────────────────────────────

class _Result:
    def __init__(self, ok=True, content="结果", error=None):
        self.ok = ok
        self.content = content
        self.error = error


class _StubMaster:
    def __init__(self, *a, **k):
        self.calls = []

    def submit(self, task, progress_cb=None):
        self.calls.append(task)
        return _Result(content=f"{task.type} 子 agent 产出")


class _HeartbeatStubMaster:
    """模拟子 agent 执行中按轮回调 progress_cb（驱动心跳）。"""

    def __init__(self, *a, **k):
        self.calls = []

    def submit(self, task, progress_cb=None):
        self.calls.append(task)
        if progress_cb:
            progress_cb("starting", 0, "")
            progress_cb("running", 1, "第 1/3 轮")
            progress_cb("running", 2, "第 2/3 轮")
        return _Result(content=f"{task.type} 子 agent 产出")


_GOAL = {
    "id": "goal_001", "name": "B端点位招商",
    "brand_position": "深圳本土自助售卖机运营商",
    "overall_strategy": {"core_message": "用闲置场地换被动收入"},
}


def _backend():
    from storage.local_json import LocalJsonBackend
    tmp = Path(tempfile.mkdtemp(prefix="verify_orch_"))
    b = LocalJsonBackend(base_dir=str(tmp))
    b.save_goals("default", {"active_goal_id": "goal_001", "goals": [_GOAL]})
    return b


def _types(master):
    return [t.type for t in master.calls]


def _last_is_done(events):
    return bool(events) and events[-1].get("type") == "done"


def _done_count(events):
    return sum(1 for e in events if e.get("type") == "done")


def _heartbeat_between_start_and_result(events):
    """心跳应落在某次 subagent_start 与其后的 subagent_result 之间。"""
    kinds = [e.get("type") for e in events]
    if "subagent_start" not in kinds or "heartbeat" not in kinds:
        return False
    start = kinds.index("subagent_start")
    hb = kinds.index("heartbeat")
    rest = kinds[hb:]
    return hb > start and "subagent_result" in rest


def main():
    from agents.orchestrator_agent import run_turn

    # ── S1 纯问答：不调子 agent ───────────────────────────────────────────────
    section("S1 · 动态分支：纯问答不调子 agent")
    b = _backend(); m = _StubMaster(); ev = []
    llm = _scripted(_FakeMsg(content="点位招商关键是选对人流入口。"))
    v = run_turn(backend=b, tenant_id="default", message="点位招商要注意啥？",
                 goal_id="goal_001", emit=ev.append, llm=llm, master=m)
    check("S1.1 status=done", v["status"] == "done", str(v["status"]))
    check("S1.2 未调任何子 agent", m.calls == [], str(_types(m)))
    check("S1.3 无 subagent_start 事件", "subagent_start" not in [e["type"] for e in ev])
    check("S1.4 末尾事件是 done", _last_is_done(ev), str([e["type"] for e in ev][-3:]))

    # ── S2 只析：只调 analyst ─────────────────────────────────────────────────
    section("S2 · 动态分支：单一意图只调 analyst")
    b = _backend(); m = _StubMaster(); ev = []
    llm = _scripted(
        _FakeMsg(tool_calls=[_tc("run_subagent", archetype="analyst", task="复盘上周数据")]),
        _FakeMsg(tool_calls=[_tc("finish", summary="上周反直觉型角度 CES 最高，建议沿用。")]),
    )
    v = run_turn(backend=b, tenant_id="default", message="看看上周哪篇数据最好",
                 goal_id="goal_001", emit=ev.append, llm=llm, master=m)
    check("S2.1 status=done", v["status"] == "done", str(v["status"]))
    check("S2.2 只调 analyst", _types(m) == ["analyst"], str(_types(m)))
    check("S2.3 有 final 事件", any(e["type"] == "final" for e in ev))

    # ── S3 多步：intel→analyst→content ───────────────────────────────────────
    section("S3 · 动态分支：复合意图多步串联")
    b = _backend(); m = _StubMaster(); ev = []
    llm = _scripted(
        _FakeMsg(tool_calls=[_tc("run_subagent", archetype="intel", task="采集工厂物业笔记")]),
        _FakeMsg(tool_calls=[_tc("run_subagent", archetype="analyst", task="找高CES共性")]),
        _FakeMsg(tool_calls=[_tc("run_subagent", archetype="content", task="写3篇草稿")]),
        _FakeMsg(tool_calls=[_tc("finish", summary="已产出3篇贴合爆款角度的草稿。")]),
    )
    v = run_turn(backend=b, tenant_id="default", message="规划并写一批面向工厂物业的内容",
                 goal_id="goal_001", emit=ev.append, llm=llm, master=m)
    check("S3.1 status=done", v["status"] == "done", str(v["status"]))
    check("S3.2 顺序 intel→analyst→content", _types(m) == ["intel", "analyst", "content"], str(_types(m)))
    starts = [e for e in ev if e["type"] == "subagent_start"]
    check("S3.3 三个 subagent_start", len(starts) == 3, str(len(starts)))

    # ── S4 追问暂停 + 答复恢复（不重跑） ──────────────────────────────────────
    section("S4 · 追问暂停 + 答复恢复（pending 不被清 / 不重跑）")
    b = _backend(); m = _StubMaster()
    ev1 = []
    llm1 = _scripted(
        _FakeMsg(tool_calls=[_tc("run_subagent", archetype="intel", task="先采集")]),
        _FakeMsg(tool_calls=[_tc("ask_user", question="主推园区物业还是写字楼行政？")]),
    )
    v1 = run_turn(backend=b, tenant_id="default", message="帮我搞批内容",
                  goal_id="goal_001", emit=ev1.append, llm=llm1, master=m)
    check("S4.1 status=awaiting_user", v1["status"] == "awaiting_user", str(v1["status"]))
    check("S4.2 pending.question 设置", (v1.get("pending") or {}).get("question", "").startswith("主推"),
          str(v1.get("pending")))
    check("S4.3 暂停轮末尾仍是 done", _last_is_done(ev1), str([e["type"] for e in ev1][-2:]))
    check("S4.4 暂停轮 done.status=awaiting_user（非终态）",
          ev1[-1].get("status") == "awaiting_user", str(ev1[-1]))
    check("S4.4b 暂停轮 done 带 session_id（供前端续接捕获）",
          ev1[-1].get("session_id") == v1["session_id"], str(ev1[-1].get("session_id")))
    reloaded = b.get_session("default", v1["session_id"])
    check("S4.5 pending 落库未被 done 清掉", (reloaded.get("pending") or {}).get("question", "") != "",
          str(reloaded.get("pending")))
    check("S4.6 intel 跑了一次", _types(m) == ["intel"], str(_types(m)))

    ev2 = []
    llm2 = _scripted(
        _FakeMsg(tool_calls=[_tc("run_subagent", archetype="content", task="按园区物业写")]),
        _FakeMsg(tool_calls=[_tc("finish", summary="已按园区物业产出草稿。")]),
    )
    v2 = run_turn(backend=b, tenant_id="default", message="园区物业",
                  session_id=v1["session_id"], emit=ev2.append, llm=llm2, master=m)
    check("S4.7 恢复后 status=done", v2["status"] == "done", str(v2["status"]))
    check("S4.8 intel 不重跑、新增 content", _types(m) == ["intel", "content"], str(_types(m)))
    check("S4.9 pending 清空", v2.get("pending") is None, str(v2.get("pending")))

    # ── S5 决策卡暂停 ─────────────────────────────────────────────────────────
    section("S5 · 决策卡暂停（awaiting_decision / pending.card）")
    b = _backend(); m = _StubMaster(); ev = []
    llm = _scripted(_FakeMsg(tool_calls=[_tc("raise_decision_card", title="确认发布？", detail="将对外发3篇")]))
    v = run_turn(backend=b, tenant_id="default", message="直接发出去",
                 goal_id="goal_001", emit=ev.append, llm=llm, master=m)
    check("S5.1 status=awaiting_decision", v["status"] == "awaiting_decision", str(v["status"]))
    check("S5.2 pending.card.title 设置",
          ((v.get("pending") or {}).get("card") or {}).get("title") == "确认发布？", str(v.get("pending")))
    check("S5.3 有 decision_card 事件", any(e["type"] == "decision_card" for e in ev))
    check("S5.4 暂停轮 done.status=awaiting_decision",
          _last_is_done(ev) and ev[-1].get("status") == "awaiting_decision", str(ev[-1] if ev else None))

    # ── S6 防失控·迭代上限 ───────────────────────────────────────────────────
    section("S6 · 防失控：迭代上限优雅收尾")
    b = _backend(); m = _StubMaster(); ev = []
    forever = [_FakeMsg(tool_calls=[_tc("run_subagent", archetype="intel", task="再采")]) for _ in range(10)]
    v = run_turn(backend=b, tenant_id="default", message="一直采", goal_id="goal_001",
                 emit=ev.append, llm=_scripted(*forever), master=m, max_iterations=3)
    check("S6.1 收敛到终态 done", v["status"] == "done", str(v["status"]))
    check("S6.2 未失控无限调（<=3）", len(m.calls) <= 3, f"calls={len(m.calls)}")
    check("S6.3 末尾仍是 done", _last_is_done(ev))

    # ── S7 非法 archetype 拦截 ────────────────────────────────────────────────
    section("S7 · 非法 archetype 不落到 master")
    b = _backend(); m = _StubMaster(); ev = []
    llm = _scripted(
        _FakeMsg(tool_calls=[_tc("run_subagent", archetype="evil", task="干坏事")]),
        _FakeMsg(tool_calls=[_tc("finish", summary="该能力不可用。")]),
    )
    v = run_turn(backend=b, tenant_id="default", message="用 evil", goal_id="goal_001",
                 emit=ev.append, llm=llm, master=m)
    check("S7.1 status=done", v["status"] == "done", str(v["status"]))
    check("S7.2 非法 archetype 未落 master", m.calls == [], str(_types(m)))

    # ── S8 trace 落库可恢复 ──────────────────────────────────────────────────
    section("S8 · trace 落库可恢复")
    b = _backend(); m = _StubMaster(); ev = []
    llm = _scripted(
        _FakeMsg(tool_calls=[_tc("run_subagent", archetype="analyst", task="析")]),
        _FakeMsg(tool_calls=[_tc("finish", summary="已完成分析。")]),
    )
    v = run_turn(backend=b, tenant_id="default", message="析一下", goal_id="goal_001",
                 emit=ev.append, llm=llm, master=m)
    reloaded = b.get_session("default", v["session_id"])
    kinds = [e["type"] for e in (reloaded.get("trace") or [])]
    check("S8.1 trace 含 subagent_start", "subagent_start" in kinds, str(kinds))
    check("S8.2 trace 含 final", "final" in kinds)
    check("S8.3 trace 末尾是 done", kinds[-1:] == ["done"], str(kinds[-2:]))

    # ── S9 done 终止符唯一性 + 结果解读 ──────────────────────────────────────
    section("S9 · done 终止符唯一性 + final 结果解读为人话")
    b = _backend(); m = _StubMaster(); ev = []
    llm = _scripted(
        _FakeMsg(tool_calls=[_tc("run_subagent", archetype="intel", task="采")]),
        _FakeMsg(tool_calls=[_tc("finish", summary="建议先切深圳工厂物业角度，借餐饮选址引流。")]),
    )
    v = run_turn(backend=b, tenant_id="default", message="给个方向", goal_id="goal_001",
                 emit=ev.append, llm=llm, master=m)
    check("S9.1 整轮恰一个 done 事件", _done_count(ev) == 1, f"done_count={_done_count(ev)}")
    check("S9.2 done.status 与 view.status 一致", ev[-1].get("status") == v["status"], str(ev[-1]))
    check("S9.2b done 带 session_id 与 view 一致",
          ev[-1].get("session_id") == v["session_id"], str(ev[-1].get("session_id")))
    finals = [e for e in ev if e["type"] == "final"]
    summary_txt = finals[-1]["summary"] if finals else ""
    check("S9.3 final 是人话非 JSON", bool(summary_txt) and "{" not in summary_txt, summary_txt[:40])

    # ── S10 真实 SSE 端点冒烟（/converse/stream） ─────────────────────────────
    section("S10 · 真实 SSE 端点：事件序列以 done 收尾")
    try:
        import storage.factory
        b = _backend()
        http_llm = _scripted(
            _FakeMsg(tool_calls=[_tc("run_subagent", archetype="intel", task="采")]),
            _FakeMsg(tool_calls=[_tc("run_subagent", archetype="analyst", task="析")]),
            _FakeMsg(tool_calls=[_tc("finish", summary="已给出协调建议。")]),
        )
        from security.jwt import encode_token
        headers = {"Authorization": f"Bearer {encode_token('default')}",
                   "Idempotency-Key": uuid.uuid4().hex}
        with mock.patch.object(storage.factory, "get_backend", lambda *a, **k: b), \
             mock.patch("agents.orchestrator_agent.call_kimi_with_tools_stream", http_llm), \
             mock.patch("agents.master.HermesMaster", _StubMaster):
            from fastapi.testclient import TestClient
            from server.main import app
            c = TestClient(app)
            got: list[dict] = []
            with c.stream("POST", "/api/v1/orchestrator/converse/stream", headers=headers,
                          json={"message": "规划一批工厂物业内容", "goal_id": "goal_001"}) as resp:
                check("S10.1 HTTP 200", resp.status_code == 200, str(resp.status_code))
                for line in resp.iter_lines():
                    if line and line.startswith("data:"):
                        got.append(json.loads(line[5:].strip()))
        kinds = [e.get("type") for e in got]
        check("S10.2 收到事件流", len(got) > 0, f"n={len(got)}")
        check("S10.3 末尾事件是 done", kinds[-1:] == ["done"], str(kinds[-3:]))
        check("S10.4 整流恰一个 done", kinds.count("done") == 1, f"done={kinds.count('done')}")
        check("S10.5 done.status=done（终态）", got and got[-1].get("status") == "done", str(got[-1] if got else None))
        check("S10.5b done 带 session_id（前端可捕获续接）",
              bool(got and got[-1].get("session_id")), str(got[-1].get("session_id") if got else None))
        check("S10.6 序列含 subagent_start 与 final",
              "subagent_start" in kinds and "final" in kinds, str(kinds))
    except Exception as exc:  # noqa: BLE001 — 端点冒烟异常转 FAIL，不崩整脚本
        check("S10.0 SSE 端点冒烟未抛异常", False, f"{type(exc).__name__}: {exc}")

    # ── S11 子 agent 进度心跳：推前端但不入 trace ──────────────────────────────
    section("S11 · 子 agent 进度心跳（喂活前端空闲计时器，不污染 trace）")
    b = _backend(); m = _HeartbeatStubMaster(); ev = []
    llm = _scripted(
        _FakeMsg(tool_calls=[_tc("run_subagent", archetype="intel", task="慢采集")]),
        _FakeMsg(tool_calls=[_tc("finish", summary="采集完成，给出建议。")]),
    )
    v = run_turn(backend=b, tenant_id="default", message="跑个慢任务", goal_id="goal_001",
                 emit=ev.append, llm=llm, master=m)
    hbeats = [e for e in ev if e.get("type") == "heartbeat"]
    check("S11.1 子 agent 执行期间发出心跳", len(hbeats) >= 1, f"hb={len(hbeats)}")
    check("S11.2 心跳带 archetype/detail", bool(hbeats) and hbeats[0].get("archetype") == "intel",
          str(hbeats[0] if hbeats else None))
    reloaded = b.get_session("default", v["session_id"])
    trace_kinds = [e.get("type") for e in (reloaded.get("trace") or [])]
    check("S11.3 心跳不入 trace（不落库）", "heartbeat" not in trace_kinds, str(trace_kinds))
    check("S11.4 心跳出现在 subagent_start 之后、result 之前",
          _heartbeat_between_start_and_result(ev), str([e.get("type") for e in ev]))
    check("S11.5 整轮仍恰一个 done", _done_count(ev) == 1, f"done={_done_count(ev)}")

    # ── S12 切目标 = 服务端丢弃旧对话上下文 ───────────────────────────────────
    section("S12 · 续接同一 session 换目标 → 丢弃旧消息/旧 trace（不带旧记忆）")
    b = _backend(); m = _StubMaster()
    seen_msgs: list[list[str]] = []

    def _rec_llm(messages, tools, **k):
        seen_msgs.append([str(mm.get("content", "")) for mm in messages])
        return _FakeMsg(tool_calls=[_tc("finish", summary="ok")]), None, 100

    v1 = run_turn(backend=b, tenant_id="default", message="学校点位怎么搞",
                  goal_id="goal_001", emit=[].append, llm=_rec_llm, master=m)
    seen_msgs.clear()
    v2 = run_turn(backend=b, tenant_id="default", message="如何获取客户",
                  session_id=v1["session_id"], goal_id="goal_002",
                  emit=[].append, llm=_rec_llm, master=m)
    first_call = " ".join(seen_msgs[0]) if seen_msgs else ""
    check("S12.1 切目标后首个 LLM 调用不含上一目标用户消息",
          "学校点位怎么搞" not in first_call, first_call[:80])
    reloaded = b.get_session("default", v1["session_id"])
    msgs_txt = " ".join(str(mm.get("content", "")) for mm in (reloaded.get("messages") or []))
    check("S12.2 落库 messages 不含旧对话", "学校点位怎么搞" not in msgs_txt, msgs_txt[:80])
    check("S12.3 session goal 更新为新目标", reloaded.get("goal_id") == "goal_002",
          str(reloaded.get("goal_id")))
    check("S12.4 同目标续接不误伤（仍带历史）",
          _same_goal_keeps_history(b, m), "同目标续接应保留历史")

    # ── S13 用户提问落 trace（供恢复重建气泡）但不经 SSE ──────────────────────
    section("S13 · 用户提问写入 trace 供恢复，但不经 SSE 实时推送")
    b = _backend(); m = _StubMaster(); ev = []
    v = run_turn(backend=b, tenant_id="default", message="我的问题ABC",
                 goal_id="goal_001", emit=ev.append,
                 llm=_scripted(_FakeMsg(content="直接回答。")), master=m)
    reloaded = b.get_session("default", v["session_id"])
    trace = reloaded.get("trace") or []
    ums = [e for e in trace if e.get("type") == "user_message"]
    check("S13.1 trace 含 1 条 user_message", len(ums) == 1,
          str([e.get("type") for e in trace]))
    check("S13.2 user_message 带提问内容", bool(ums) and ums[0].get("content") == "我的问题ABC",
          str(ums[0] if ums else None))
    check("S13.3 user_message 不经 SSE 推送",
          "user_message" not in [e.get("type") for e in ev],
          str([e.get("type") for e in ev]))
    check("S13.4 user_message 落在本轮 trace 首位",
          bool(trace) and trace[0].get("type") == "user_message",
          str(trace[0].get("type") if trace else None))

    # ── S14 LLM 调用心跳：慢调用期间周期喂心跳 ────────────────────────────────
    section("S14 · LLM 调用心跳（慢调用期间喂活前端空闲计时器）")
    import time as _t
    from agents.llm_heartbeat import call_with_heartbeat
    beats: list[int] = []
    out = call_with_heartbeat(lambda: (_t.sleep(0.35), "RESULT")[1],
                              lambda n: beats.append(n), interval=0.1)
    check("S14.1 调用结果原样返回", out == "RESULT", str(out))
    check("S14.2 慢调用期间发出 ≥2 次心跳", len(beats) >= 2, f"beats={len(beats)}")
    fast: list[int] = []
    call_with_heartbeat(lambda: "x", lambda n: fast.append(n), interval=0.5)
    check("S14.3 快调用不发多余心跳", len(fast) == 0, f"fast_beats={len(fast)}")

    # ── S15 真流式：纯文本最终答复经 final_delta 增量推送 ─────────────────────
    section("S15 · 真流式 final_delta（纯文本最终答复逐 token 推送）")
    b = _backend(); m = _StubMaster(); ev = []
    answer = "点位招商先看人流入口，再谈分成结构，最后落地试运营。"

    def _stream_llm(*, messages, tools, on_delta=None, **kw):
        # 模拟逐 token 流：把最终答复切成 5 段经 on_delta 吐出；返回与非流式同形的 msg。
        size = max(1, len(answer) // 5)
        if on_delta:
            for i in range(0, len(answer), size):
                on_delta(answer[i:i + size])
        return _FakeMsg(content=answer), None, 100

    v = run_turn(backend=b, tenant_id="default", message="给个招商思路",
                 goal_id="goal_001", emit=ev.append, llm=_stream_llm, master=m)
    deltas = [e for e in ev if e["type"] == "final_delta"]
    check("S15.1 收到 ≥2 个 final_delta 增量", len(deltas) >= 2, f"count={len(deltas)}")
    check("S15.2 增量拼接 == 完整答复",
          "".join(d["text"] for d in deltas) == answer)
    finals = [e for e in ev if e["type"] == "final"]
    check("S15.3 恰一个 final 收尾(权威全文)", len(finals) == 1, f"count={len(finals)}")
    check("S15.4 final 全文 == 答复", bool(finals) and finals[0]["summary"] == answer)
    got = b.get_session("default", v["session_id"])
    trace_types = [e.get("type") for e in (got.get("trace") or [])]
    check("S15.5 final_delta 不落 trace(传输层信号)", "final_delta" not in trace_types, str(trace_types))
    check("S15.6 trace 含 final(供刷新整段还原)", "final" in trace_types)
    check("S15.7 末尾事件是 done", _last_is_done(ev))

    return summary()


def _same_goal_keeps_history(backend, master) -> bool:
    """对照：同一目标续接时，历史消息应保留（不被 goal_switch 误清）。"""
    from agents.orchestrator_agent import run_turn
    seen: list[list[str]] = []

    def _llm(messages, tools, **k):
        seen.append([str(mm.get("content", "")) for mm in messages])
        return _FakeMsg(tool_calls=[_tc("finish", summary="ok")]), None, 100

    v1 = run_turn(backend=backend, tenant_id="default", message="第一句话XYZ",
                  goal_id="goal_001", emit=[].append, llm=_llm, master=master)
    seen.clear()
    run_turn(backend=backend, tenant_id="default", message="第二句",
             session_id=v1["session_id"], goal_id="goal_001",
             emit=[].append, llm=_llm, master=master)
    return bool(seen) and any("第一句话XYZ" in " ".join(c) for c in seen)


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
