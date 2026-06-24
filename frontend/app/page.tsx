"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChatTopbar } from "@/components/ChatTopbar";
import { ChatStream } from "@/components/chat/ChatStream";
import { Composer } from "@/components/chat/Composer";
import { EmptyState } from "@/components/chat/EmptyState";
import { HistoryDrawer } from "@/components/chat/HistoryDrawer";
import type { ChatEntry, PauseStatus } from "@/components/chat/bubbles";
import {
  apiFetch,
  getToken,
  orchestratorApi,
  type OrchCard,
  type OrchEvent,
  type OrchPending,
} from "@/lib/api";
import { consumeOrchestratorStream } from "@/lib/orchestratorStream";
import { useGoalsStore } from "@/stores/goals";

interface GoalsResponse {
  goals: { id: string; name: string }[];
  active_goal_id: string;
}

const ORCH_SESSION_KEY = "spider-xhs-orch-session";

function readStoredSessionId(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(ORCH_SESSION_KEY);
}

function storeSessionId(sessionId: string | null) {
  if (typeof window === "undefined") return;
  if (sessionId) window.localStorage.setItem(ORCH_SESSION_KEY, sessionId);
  else window.localStorage.removeItem(ORCH_SESSION_KEY);
}

function pauseStatusFromPending(pending: OrchPending): PauseStatus {
  if (pending?.kind === "question") return "awaiting_user";
  if (pending?.kind === "decision") return "awaiting_decision";
  return null;
}

const AGENT_LABEL: Record<string, string> = {
  intel: "情报采集",
  analyst: "数据分析",
  content: "内容创作",
};

function heartbeatHint(archetype?: string, stage?: string, detail?: string): string {
  const who = archetype ? AGENT_LABEL[archetype] ?? archetype : "协作";
  const text = detail?.trim() || (stage === "starting" ? "启动中" : "处理中");
  return `${who} · ${text}`;
}

function traceWithPending(trace: OrchEvent[], pending: OrchPending): OrchEvent[] {
  if (pending?.kind === "question") {
    const hasQuestion = trace.some(
      (event) => event.type === "awaiting_user" && event.question === pending.question,
    );
    if (!hasQuestion) {
      return [
        ...trace,
        { type: "awaiting_user", seq: trace.length + 1, question: pending.question },
      ];
    }
  }
  if (pending?.kind === "decision") {
    const hasDecision = trace.some(
      (event) =>
        event.type === "decision_card" &&
        event.card.card_id === pending.card.card_id,
    );
    if (!hasDecision) {
      return [
        ...trace,
        { type: "decision_card", seq: trace.length + 1, card: pending.card },
      ];
    }
  }
  return trace;
}

function entriesFromTrace(trace: OrchEvent[]): ChatEntry[] {
  return trace
    .filter((event) => event.type !== "done")
    .map((event, index): ChatEntry => {
      // 用户提问还原成用户气泡，避免恢复后只剩助手侧、看不到自己问过什么。
      if (event.type === "user_message") {
        return { kind: "user", id: `restored-user-${event.seq}-${index}`, content: event.content };
      }
      return {
        kind: "event",
        id: `restored-${event.type}-${event.seq}-${index}`,
        event,
      };
    });
}

export default function ChatPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { activeGoalId } = useGoalsStore();
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [input, setInput] = useState("");
  const [goalId, setGoalId] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(() => readStoredSessionId());
  const [pauseStatus, setPauseStatus] = useState<PauseStatus>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [progressHint, setProgressHint] = useState<string | null>(null);
  const idSeq = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const restoredRef = useRef(false);
  // 真流式：当前正在累积增量 token 的 live 最终气泡 id（null=无）。
  const liveFinalIdRef = useRef<string | null>(null);

  const { data: goalsData } = useQuery<GoalsResponse>({
    queryKey: ["goals"],
    queryFn: () => apiFetch<GoalsResponse>("/api/v1/goals"),
    retry: false,
  });

  useEffect(() => {
    if (!getToken()) router.replace("/login");
  }, [router]);

  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  // 把 session view 应用到本地状态（mount 恢复 / 点历史条目共用）。
  function applySessionView(
    view: Awaited<ReturnType<typeof orchestratorApi.getSession>>,
  ) {
    const restoredTrace = traceWithPending(view.trace ?? [], view.pending);
    const restoredEntries = entriesFromTrace(restoredTrace);
    idSeq.current = Math.max(idSeq.current, restoredEntries.length);
    setSessionId(view.session_id);
    storeSessionId(view.session_id);
    setGoalId(view.goal_id);
    setEntries(restoredEntries);
    setPauseStatus(pauseStatusFromPending(view.pending));
  }

  // 点击历史条目：中止在飞流 → 拉该会话 → 应用。
  function loadSession(id: string) {
    abortRef.current?.abort();
    abortRef.current = null;
    setProgressHint(null);
    setIsStreaming(false);
    liveFinalIdRef.current = null;
    orchestratorApi.getSession(id).then(applySessionView).catch(() => {});
  }

  useEffect(() => {
    if (restoredRef.current) return;
    restoredRef.current = true;

    const storedSessionId = readStoredSessionId();
    if (!storedSessionId) return;

    let ignore = false;
    orchestratorApi
      .getSession(storedSessionId)
      .then((view) => {
        if (ignore) return;
        // 管理后台/别处已把激活目标切到别的目标 → 不恢复旧对话，开新的。
        // （读 store 最新值，而非 effect 闭包，避免 hydration 时序问题。）
        const activeNow = useGoalsStore.getState().activeGoalId;
        if (activeNow && view.goal_id && view.goal_id !== activeNow) {
          // 激活目标已与该会话不符（如管理后台切换）→ 丢弃旧会话，开新对话。
          // ★ sessionId state 在 mount 时已从 localStorage 取了旧值，必须一并清 state，
          //   否则下一轮仍带旧 session_id 续接旧记忆（只清 localStorage 不够）。
          setSessionId(null);
          storeSessionId(null);
          setGoalId(null);
          setPauseStatus(null);
          return;
        }
        applySessionView(view);
      })
      .catch(() => {
        if (ignore) return;
        setSessionId(null);
        storeSessionId(null);
        setPauseStatus(null);
      });

    return () => {
      ignore = true;
    };
  }, []);

  const selectedGoalId = goalId ?? activeGoalId ?? null;

  // 开新对话：清掉当前会话上下文（中止在飞流、清气泡、清 session、清待答/进度）。
  function resetConversation() {
    abortRef.current?.abort();
    abortRef.current = null;
    setEntries([]);
    setSessionId(null);
    storeSessionId(null);
    setPauseStatus(null);
    setProgressHint(null);
    setIsStreaming(false);
    idSeq.current = 0;
    liveFinalIdRef.current = null;
  }

  // 切换运营目标 = 开新对话，新目标从零开始（用户确认的行为）。
  // 仅响应用户在下拉框的显式切换；会话恢复走 setGoalId 直接设值、不经此函数，避免误清。
  function handleGoalChange(newGoalId: string | null) {
    if (newGoalId !== selectedGoalId && (entries.length > 0 || sessionId)) {
      resetConversation();
    }
    setGoalId(newGoalId);
  }

  function nextId(prefix: string): string {
    idSeq.current += 1;
    return `${prefix}-${idSeq.current}`;
  }

  function captureSessionId(event: OrchEvent) {
    const maybeSessionId = (event as OrchEvent & { session_id?: unknown }).session_id;
    if (typeof maybeSessionId === "string" && maybeSessionId) {
      setSessionId(maybeSessionId);
      storeSessionId(maybeSessionId);
    }
  }

  function appendEvent(event: OrchEvent) {
    captureSessionId(event);
    if (event.type === "done") return;
    if (event.type === "heartbeat") {
      // 心跳不入气泡流：只更新「正在做什么」提示（消费器已据此重置空闲计时器）。
      setProgressHint(heartbeatHint(event.archetype, event.stage, event.detail));
      return;
    }

    // 真流式：最终回答的增量 token → 累积进同一个 live 气泡（首个 delta 建气泡，后续追加）。
    if (event.type === "final_delta") {
      setProgressHint(null);
      if (liveFinalIdRef.current === null) {
        const id = nextId("event-final");
        liveFinalIdRef.current = id;
        setEntries((prev) => [
          ...prev,
          { kind: "event", id, event: { type: "final", seq: event.seq, summary: event.text } },
        ]);
      } else {
        const id = liveFinalIdRef.current;
        setEntries((prev) =>
          prev.map((e) =>
            e.id === id && e.kind === "event" && e.event.type === "final"
              ? { ...e, event: { ...e.event, summary: e.event.summary + event.text } }
              : e,
          ),
        );
      }
      return;
    }

    setProgressHint(null); // 真实事件落地 → 清掉上一段进度提示

    // 定稿：用 final 的权威全文替换 live 气泡（若有），否则作为普通最终气泡追加。
    if (event.type === "final") {
      const liveId = liveFinalIdRef.current;
      liveFinalIdRef.current = null;
      if (liveId) {
        setEntries((prev) =>
          prev.map((e) =>
            e.id === liveId && e.kind === "event"
              ? { ...e, event: { type: "final", seq: event.seq, summary: event.summary } }
              : e,
          ),
        );
      } else {
        setEntries((prev) => [...prev, { kind: "event", id: nextId("event-final"), event }]);
      }
      return;
    }

    // 其它事件：若存在尚未定稿的 live 最终气泡 → 丢弃它（那是被误当最终答案流出的思考前言，
    // 真实内容会以 thinking/subagent_* 等正常事件呈现），再追加本事件。
    const staleLiveId = liveFinalIdRef.current;
    liveFinalIdRef.current = null;
    const newId = nextId(`event-${event.type}`);
    setEntries((prev) => {
      const base = staleLiveId ? prev.filter((e) => e.id !== staleLiveId) : prev;
      return [...base, { kind: "event", id: newId, event }];
    });
  }

  function appendError(message: string) {
    appendEvent({
      type: "error",
      seq: Date.now(),
      message,
    });
  }

  function isPauseStatus(status: string): status is Exclude<PauseStatus, null> {
    return status === "awaiting_user" || status === "awaiting_decision";
  }

  async function startTurn(
    message: string,
    options: { requireSession?: boolean } = {},
  ) {
    const trimmed = message.trim();
    if (!trimmed || isStreaming) return;
    if (options.requireSession && !sessionId) {
      appendError("当前会话缺少 session_id，无法续接。请重新发起一轮对话。");
      setPauseStatus(null);
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    liveFinalIdRef.current = null; // 新一轮开始，清掉上一轮可能残留的 live 气泡引用

    setEntries((prev) => [
      ...prev,
      { kind: "user", id: nextId("user"), content: trimmed },
    ]);
    setInput("");
    setPauseStatus(null);
    setProgressHint(null);
    setIsStreaming(true);

    try {
      const result = await consumeOrchestratorStream(
        {
          message: trimmed,
          goal_id: selectedGoalId,
          session_id: sessionId,
        },
        appendEvent,
        controller.signal,
      );

      if (isPauseStatus(result.status)) {
        setPauseStatus(result.status);
      } else if (result.status === "cancelled") {
        appendError("本轮已取消。");
      }
    } catch (error) {
      if (controller.signal.aborted) return;
      appendError(error instanceof Error ? error.message : "流式对话失败");
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
      setIsStreaming(false);
      setProgressHint(null);
      // 本轮可能新建会话 / 改了标题与 updated_at → 让左侧历史列表重拉。
      queryClient.invalidateQueries({ queryKey: ["sessions"] });
    }
  }

  function sendInput() {
    void startTurn(input);
  }

  function answerQuestion(answer: string) {
    void startTurn(answer, { requireSession: true });
  }

  function answerDecision(card: OrchCard, decision: "approve" | "reject") {
    const label = decision === "approve" ? "批准" : "拒绝";
    void startTurn(`${label}：${card.title || card.detail}`, {
      requireSession: true,
    });
  }

  return (
    <div className="flex h-screen w-full">
      <HistoryDrawer
        goalId={selectedGoalId}
        activeSessionId={sessionId}
        onSelect={loadSession}
        onNewChat={resetConversation}
      />
      <div className="flex min-w-0 flex-1 flex-col bg-[var(--bg)] text-[var(--text1)]">
        <ChatTopbar />
        <main className="flex min-h-0 flex-1 flex-col overflow-hidden">
          {entries.length === 0 ? (
            <EmptyState
              goalId={selectedGoalId}
              onGoalChange={handleGoalChange}
              onSend={startTurn}
              goals={goalsData?.goals}
            />
          ) : (
            <>
              <div className="min-h-0 flex-1">
                <ChatStream
                  entries={entries}
                  isStreaming={isStreaming}
                  progressHint={progressHint}
                  actions={{
                    pauseStatus,
                    isStreaming,
                    onAnswerQuestion: answerQuestion,
                    onDecision: answerDecision,
                  }}
                />
              </div>
              <div className="shrink-0 border-t border-[var(--border2)] bg-[var(--bg)] px-4 py-4">
                <div className="mx-auto max-w-3xl">
                  <Composer
                    variant="docked"
                    value={input}
                    onChange={setInput}
                    onSend={sendInput}
                    goalId={selectedGoalId}
                    onGoalChange={handleGoalChange}
                    goals={goalsData?.goals}
                  />
                </div>
              </div>
            </>
          )}
        </main>
      </div>
    </div>
  );
}
