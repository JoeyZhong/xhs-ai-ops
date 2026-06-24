"""Orchestrator 协调内核(V1 · orchestrator-coordinator P1)。

主 Agent = 一个 agent，子 agent = 它的"工具"。它理解意图 → 动态决定调不调、调谁、
调几次(看一步再定下一步)→ 信息不足主动追问 → 把结果解读成人话建议。

复用 `AgentBase` 的机器件(call_kimi_with_tools / scratch_pad 剥离 / 免疫压缩 /
REASONING+SAFETY 指令 / messages 协议 / 迭代+token 预算)，但不 subclass `run()`——
工具调度走自己的 meta-tool handler(run_subagent / ask_user / raise_decision_card / finish)。

对外唯一入口 `run_turn`(契约见 docs/handoff/orchestrator-coordinator-contracts.md §C)：
每步经 `emit(event)` 推送(§B)并落 session.trace；暂停(ask_user/decision)写 pending 后返回；
恢复 = 从 session.messages 重建上下文 + 本轮答复续跑(已跑子 agent 结果不重跑)。
"""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

from agent_tools.kimi import call_kimi_with_tools_stream
from agents.base import (
    AgentTask,
    REASONING_DIRECTIVE,
    SAFETY_DIRECTIVE,
    TOOL_CALL_DISCIPLINE_DIRECTIVE,
    _strip_scratch_pad,
)
from agents.compression import compress_messages, detect_immune_zone, should_compress
from agents.llm_heartbeat import call_with_heartbeat
from agents.orchestrator import _find_goal, _goal_methodology, _new_session_id

_ARCHETYPES = ("intel", "analyst", "content")
_MAX_ITERS = 12
_BUDGET_TOKENS = 60_000
_SUBAGENT_RESULT_CAP = 4000  # 回灌主回路的子 agent 结果上限

COORDINATION_SYSTEM = """你是小红书运营团队的主调度 Agent(Orchestrator)。
你不直接干活，而是理解用户意图、动态决定要不要以及怎样调度子 agent 完成任务，最后把结果解读成人话建议。

可调度的子 agent(通过 run_subagent 工具)：
  - intel：采集小红书笔记/热词/关键词监控(只读外部信号)
  - analyst：数据分析(CES/10-3-1/流量诊断/复盘)
  - content：生成笔记草稿(标题/正文/标签)

协调纪律：
- 按意图灵活决定：可能只需直接回答、只调一个子 agent、或多步串联；不要无脑三件套。
- 调一步、看结果、再决定下一步；后一步的 task 可基于前一步结论。
- 信息不足以推进时用 ask_user 追问，不要瞎猜。
- 高风险/不可逆动作(发布/对外/删除等)用 raise_decision_card 请用户拍板。
- 完成后**直接输出纯文本**的"人话建议 + 依据"：不要调用任何工具、不要输出 JSON、不要堆子 agent 原始输出；这段纯文本即最终答复。
- 一次只推进必要步骤，省着用预算。
"""

_TOOLS = [
    {"type": "function", "function": {
        "name": "run_subagent",
        "description": "调度一个子 agent 执行一项具体任务，返回其结果。",
        "parameters": {"type": "object", "required": ["archetype", "task"], "properties": {
            "archetype": {"type": "string", "enum": list(_ARCHETYPES), "description": "子 agent 类型"},
            "task": {"type": "string", "description": "交给子 agent 的具体任务(可引用前一步结论)"},
        }},
    }},
    {"type": "function", "function": {
        "name": "ask_user",
        "description": "信息不足时向用户追问一个问题；会暂停等待用户答复。",
        "parameters": {"type": "object", "required": ["question"], "properties": {
            "question": {"type": "string"}}},
    }},
    {"type": "function", "function": {
        "name": "raise_decision_card",
        "description": "就高风险/不可逆动作请用户确认；会暂停等待。",
        "parameters": {"type": "object", "required": ["title", "detail"], "properties": {
            "title": {"type": "string"}, "detail": {"type": "string"},
            "kind": {"type": "string", "default": "high_risk_step"}}},
    }},
    # 注：finish 工具已退役、不再 advertise——协调完直接输出纯文本最终答复（真流式）。
    # _dispatch 仍保留 finish handler 作防御兜底（模型偶发误调时优雅收尾）。
]


class _PauseSignal(Exception):
    """ask_user / raise_decision_card → 暂停回路。"""

    def __init__(self, status: str, pending: dict):
        self.status = status      # awaiting_user | awaiting_decision
        self.pending = pending


class _Finish(Exception):
    """finish → 收尾。"""

    def __init__(self, summary: str):
        self.summary = summary


def _system_prompt(goal: Optional[dict]) -> str:
    parts = [COORDINATION_SYSTEM]
    if goal:
        meth = _goal_methodology(goal)
        if meth:
            parts.append(f"\n【当前运营目标上下文】\n{meth}")
    parts.append(REASONING_DIRECTIVE)
    parts.append(TOOL_CALL_DISCIPLINE_DIRECTIVE)
    parts.append(SAFETY_DIRECTIVE)
    return "\n".join(p for p in parts if p)


class _Driver:
    """单轮协调驱动。持有 session 状态 + OCC rev，逐步落库 + emit。"""

    def __init__(self, *, backend: Any, tenant_id: str,
                 emit: Callable[[dict], None],
                 llm: Callable[..., tuple],
                 master: Any,
                 max_iterations: int, budget_tokens: int):
        self.backend = backend
        self.tenant_id = tenant_id
        self.emit = emit
        self.llm = llm
        self._master = master
        self.max_iterations = max_iterations
        self.budget_tokens = budget_tokens
        self.sess: dict = {}
        self.rev: int = 0
        self.seq: int = 0

    # ── 落库辅助 ──────────────────────────────────────────────────────────

    def _update(self, **changes: Any) -> None:
        self.sess = self.backend.update_session(
            self.tenant_id, self.sess["session_id"], expected_rev=self.rev, **changes)
        self.rev = self.sess["rev"]

    def _emit_step(self, event: dict) -> None:
        """emit 事件 + 追加进 session.trace 并落库。"""
        self.seq += 1
        event = {**event, "seq": self.seq}
        self.emit(event)
        trace = list(self.sess.get("trace") or [])
        trace.append(event)
        self._update(trace=trace)

    def _append_trace_only(self, event: dict) -> None:
        """只追加进 trace 落库、不经 SSE emit。

        用于 user_message：实时端本轮已本地显示用户气泡，无需再推；但要落进 trace，
        供刷新/返回后恢复时重建用户气泡，避免"此前发出的问题消失"。
        """
        self.seq += 1
        event = {**event, "seq": self.seq}
        trace = list(self.sess.get("trace") or [])
        trace.append(event)
        self._update(trace=trace)

    def _get_master(self):
        if self._master is None:
            from agents.master import HermesMaster
            self._master = HermesMaster(tenant_id=self.tenant_id)
        return self._master

    # ── 元工具 handler ───────────────────────────────────────────────────

    def _emit_heartbeat(self, archetype: str, stage: Any,
                        iteration: Any, detail: Any) -> None:
        """子 agent 执行进度心跳：只推前端（喂活空闲计时器 + 显示进度），
        不入 trace、不落库（绕开 _emit_step）。

        心跳源自子 agent 主循环的 progress_cb，故子 agent 真卡死（卡在某次
        call_kimi_with_tools 或某个工具调用里）时心跳自然停摆，前端空闲超时仍会
        触发——不会被传输层假心跳掩盖。seq=0 标记其为非 trace 元素。"""
        self.emit({"type": "heartbeat", "seq": 0, "archetype": archetype,
                   "stage": str(stage), "iteration": int(iteration or 0),
                   "detail": str(detail or "")})

    def _emit_final_delta(self, text: str) -> None:
        """最终回答的增量 token：只推前端累积成 live 气泡，不入 trace、不落库
        （传输层信号，类 heartbeat；seq=0 标记非 trace 元素）。整段权威文本随后由
        final 事件给出，故刷新/恢复时据 trace 里的 final 整段还原，不丢内容。"""
        if not text:
            return
        self.emit({"type": "final_delta", "seq": 0, "text": text})

    def _h_run_subagent(self, args: dict, goal_id: Optional[str]) -> str:
        archetype = (args.get("archetype") or "").strip()
        task = (args.get("task") or "").strip()
        if archetype not in _ARCHETYPES:
            return json.dumps({"ok": False, "error": f"unknown archetype: {archetype}"},
                              ensure_ascii=False)
        self._emit_step({"type": "subagent_start", "archetype": archetype, "task": task})

        def _progress(stage: Any, iteration: Any, detail: Any) -> None:
            self._emit_heartbeat(archetype, stage, iteration, detail)

        result = self._get_master().submit(AgentTask(
            type=archetype, prompt=task, tenant_id=self.tenant_id, goal_id=goal_id or "",
            extra={"source_session_id": self.sess.get("session_id", "")}),
            progress_cb=_progress)
        content = (result.content or "")[:_SUBAGENT_RESULT_CAP]
        self._emit_step({"type": "subagent_result", "archetype": archetype,
                         "ok": bool(result.ok),
                         "summary": content[:600] or (result.error or "")})
        return json.dumps({"ok": bool(result.ok), "agent": archetype,
                           "content": content, "error": result.error}, ensure_ascii=False)

    # ── 主循环 ────────────────────────────────────────────────────────────

    def run(self, *, message: str, session_id: Optional[str],
            goal_id: Optional[str]) -> dict:
        sess = None
        if session_id:
            sess = self.backend.get_session(self.tenant_id, session_id)
        if sess is None:
            sess = self.backend.create_session(
                self.tenant_id, session_id=_new_session_id(), goal_id=goal_id,
                status="thinking", messages=[], proposed_plan=[], decision_cards=[],
                dag_id=None)
        self.sess = sess
        self.rev = sess["rev"]
        goal_id_eff = goal_id or sess.get("goal_id")
        goal = _find_goal(self.backend, self.tenant_id, goal_id_eff)

        # 切目标 = 开新对话：续接同一 session 却换了目标时，丢弃旧消息/旧 trace，
        # 避免上一目标的上下文渗入新目标推理（前端切目标通常已开新会话，此为服务端兜底）。
        prior_goal = sess.get("goal_id")
        goal_switched = bool(goal_id) and bool(prior_goal) and goal_id != prior_goal
        prior_convo = [] if goal_switched else list(sess.get("messages") or [])
        self.seq = 0 if goal_switched else len(sess.get("trace") or [])

        # 重建工作上下文：system(每轮再生) + 历史(不含 system) + 本轮用户输入
        convo = prior_convo + [{"role": "user", "content": message}]
        messages = [{"role": "system", "content": _system_prompt(goal)}] + convo
        changes: dict = {"goal_id": goal_id_eff, "status": "thinking",
                         "messages": convo, "pending": None}
        if goal_switched:
            changes["trace"] = []
        self._update(**changes)

        # 用户本轮提问写进 trace（仅落库、不经 SSE）：供恢复时重建用户气泡。
        self._append_trace_only({"type": "user_message", "content": message})

        tokens = 0
        try:
            for _ in range(self.max_iterations):
                if tokens >= self.budget_tokens:
                    return self._finalize(messages, "(已达 token 预算，先给出目前的结论)")
                if should_compress(messages):
                    immune = detect_immune_zone(messages)
                    messages, _meta = compress_messages(messages, immune)

                # 主 Agent 自己的 LLM 调用可能很慢（重试累计可达数分钟），期间无任何事件
                # 会触发前端 120s 超时；用心跳 ticker 在调用期间周期发"正在思考"喂活计时器。
                # on_delta：最终答复 token 边生成边流（真流式）。出现 tool_call 的轮次
                # 流式函数自动不吐 content，故只有"纯文本最终答复"的那轮才真正流出 final_delta。
                msg, err, used = call_with_heartbeat(
                    lambda: self.llm(messages=messages, tools=_TOOLS,
                                     max_tokens=2000, temperature=0.5,
                                     on_delta=self._emit_final_delta),
                    lambda n: self._emit_heartbeat("", "thinking", n, "正在思考…"),
                )
                tokens += used or 1500
                if err or msg is None:
                    self._emit_step({"type": "error", "message": err or "LLM error"})
                    return self._close("done")

                tool_calls = getattr(msg, "tool_calls", None) or []
                clean = _strip_scratch_pad(msg.content or "")
                if not tool_calls:
                    # 无 tool_call → 当作最终答复(直接回答类意图)
                    return self._finalize(messages, clean or "(无输出)")

                if clean:
                    self._emit_step({"type": "thinking", "summary": clean[:400]})
                messages.append({"role": "assistant", "content": clean,
                                 "tool_calls": [_ser_tc(tc) for tc in tool_calls]})

                try:
                    self._dispatch(tool_calls, messages, goal_id_eff)
                except _Finish as f:
                    return self._finalize(messages, f.summary, already_appended=True)
                except _PauseSignal as p:
                    self._persist_convo(messages)
                    self._update(status=p.status, pending=p.pending)
                    if p.status == "awaiting_user":
                        self._emit_step({"type": "awaiting_user",
                                         "question": p.pending.get("question", "")})
                    else:
                        self._emit_step({"type": "decision_card",
                                         "card": p.pending.get("card", {})})
                    self._emit_step({"type": "done", "status": p.status,
                                     "session_id": self.sess["session_id"]})
                    return self._view()

            # 迭代上限 → 优雅收尾
            return self._finalize(messages, "(已达协调步数上限，先给出目前的结论)")
        finally:
            pass

    def _dispatch(self, tool_calls, messages: list, goal_id: Optional[str]) -> None:
        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}

            if name == "run_subagent":
                out = self._h_run_subagent(args, goal_id)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": out})
            elif name == "ask_user":
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": "已向用户提问，等待答复。"})
                raise _PauseSignal("awaiting_user",
                                   {"kind": "question", "question": args.get("question", "")})
            elif name == "raise_decision_card":
                card = {"card_id": f"dc-{self.seq + 1}",
                        "kind": args.get("kind", "high_risk_step"),
                        "title": args.get("title", ""), "detail": args.get("detail", ""),
                        "options": ["approve", "reject"], "status": "pending"}
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": "已出决策卡，等待用户确认。"})
                raise _PauseSignal("awaiting_decision", {"kind": "decision", "card": card})
            elif name == "finish":
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": "ok"})
                raise _Finish(args.get("summary", ""))
            else:
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": json.dumps({"ok": False,
                                 "error": f"unknown tool: {name}"}, ensure_ascii=False)})

    # ── 收尾 / 视图 ───────────────────────────────────────────────────────

    def _finalize(self, messages: list, summary: str,
                  already_appended: bool = False) -> dict:
        if not already_appended:
            messages.append({"role": "assistant", "content": summary})
        self._persist_convo(messages)
        self._emit_step({"type": "final", "summary": summary})
        return self._close("done")

    def _persist_convo(self, messages: list) -> None:
        # 存历史(不含 system，下轮重建)
        self._update(messages=[m for m in messages if m.get("role") != "system"])

    def _close(self, status: str) -> dict:
        self._update(status=status, pending=None)
        self._emit_step({"type": "done", "status": status,
                         "session_id": self.sess["session_id"]})
        return self._view()

    def _view(self) -> dict:
        s = self.sess
        return {"session_id": s["session_id"], "status": s["status"],
                "goal_id": s.get("goal_id"), "messages": s.get("messages"),
                "trace": s.get("trace"), "pending": s.get("pending"),
                "decision_cards": s.get("decision_cards"), "dag_id": s.get("dag_id")}


def _ser_tc(tc) -> dict:
    return {"id": tc.id, "type": "function",
            "function": {"name": tc.function.name, "arguments": tc.function.arguments}}


def run_turn(*, backend: Any, tenant_id: str, message: str,
             session_id: Optional[str] = None, goal_id: Optional[str] = None,
             emit: Optional[Callable[[dict], None]] = None,
             llm: Optional[Callable[..., tuple]] = None,
             master: Any = None,
             max_iterations: int = _MAX_ITERS,
             budget_tokens: int = _BUDGET_TOKENS) -> dict:
    """驱动一轮协调(契约 §C)。emit 默认 no-op；llm 默认 call_kimi_with_tools_stream（真流式）。"""
    drv = _Driver(backend=backend, tenant_id=tenant_id,
                  emit=emit or (lambda e: None),
                  llm=llm or call_kimi_with_tools_stream, master=master,
                  max_iterations=max_iterations, budget_tokens=budget_tokens)
    return drv.run(message=message, session_id=session_id, goal_id=goal_id)
