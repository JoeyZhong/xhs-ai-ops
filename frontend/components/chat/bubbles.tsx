"use client";

import { useState } from "react";
import Link from "next/link";
import { Search, BarChart3, Pencil, Settings } from "lucide-react";
import type { OrchCard, OrchEvent } from "@/lib/api";

export type ChatEntry =
  | { kind: "user"; id: string; content: string }
  | { kind: "event"; id: string; event: OrchEvent };

type AgentIcon = React.ComponentType<{ className?: string }>;

const AGENT_META: Record<string, { icon: AgentIcon; label: string }> = {
  intel: { icon: Search, label: "情报采集" },
  analyst: { icon: BarChart3, label: "数据分析" },
  content: { icon: Pencil, label: "内容创作" },
};

function agentLabel(archetype: string): string {
  return AGENT_META[archetype]?.label ?? archetype;
}

function parseContentRecords(raw: string): Array<Record<string, string>> | null {
  try {
    const parsed = JSON.parse(raw);
    if (
      Array.isArray(parsed) &&
      parsed.length > 0 &&
      parsed.some((item) => item && typeof item === "object" && "主标题" in item)
    ) {
      return parsed as Array<Record<string, string>>;
    }
  } catch {
    return null;
  }
  return null;
}

function ContentCards({ summary }: { summary: string }) {
  const records = parseContentRecords(summary);
  if (!records) return null;

  return (
    <div className="mt-3 space-y-2">
      {records.map((record, index) => (
        <div
          key={`${record.主标题 ?? "content"}-${index}`}
          className="rounded-lg border border-[var(--border)] bg-white px-3 py-3"
        >
          <div className="text-sm font-semibold text-[var(--text1)]">
            {record.主标题 || "（无标题）"}
          </div>
          {record.本次角度 && (
            <div className="mt-1 text-xs font-medium text-[var(--brand)]">
              角度：{record.本次角度}
            </div>
          )}
          {record.正文 && (
            <div className="mt-2 line-clamp-3 text-xs leading-relaxed text-[var(--text2)]">
              {record.正文}
            </div>
          )}
        </div>
      ))}
      <Link
        href="/admin/content"
        className="inline-flex items-center rounded-lg border border-[var(--border)] px-3 py-2 text-xs font-medium text-[var(--text2)] transition-colors hover:border-[var(--brand)] hover:text-[var(--brand)]"
      >
        去内容创作精修 →
      </Link>
    </div>
  );
}

function AssistantBubble({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex justify-start">
      <div className="max-w-[78%] rounded-lg border border-[var(--border2)] bg-[var(--card-warm)] px-4 py-3 text-sm leading-relaxed text-[var(--text1)] shadow-[0_4px_16px_rgba(60,50,30,.04)]">
        {children}
      </div>
    </div>
  );
}

export function UserBubble({ content }: { content: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[72%] rounded-lg bg-[var(--brand)] px-4 py-3 text-sm leading-relaxed text-white shadow-[0_8px_20px_rgba(232,68,90,.18)]">
        {content}
      </div>
    </div>
  );
}

function SubagentCard({
  archetype,
  title,
  children,
  tone,
}: {
  archetype: string;
  title: string;
  children: React.ReactNode;
  tone: "running" | "ok" | "failed";
}) {
  const toneClass =
    tone === "running"
      ? "text-amber-700 bg-amber-50 border-amber-200"
      : tone === "ok"
        ? "text-green-700 bg-green-50 border-green-200"
        : "text-red-700 bg-red-50 border-red-200";
  const Icon = AGENT_META[archetype]?.icon ?? Settings;

  return (
    <AssistantBubble>
      <div className="flex items-center gap-2 text-xs font-semibold text-[var(--text2)]">
        <Icon className="w-3.5 h-3.5" />
        <span>{agentLabel(archetype)}</span>
        <span className={`ml-auto rounded-full border px-2 py-0.5 ${toneClass}`}>
          {title}
        </span>
      </div>
      <div className="mt-2 text-[var(--text1)]">{children}</div>
    </AssistantBubble>
  );
}

export type PauseStatus = "awaiting_user" | "awaiting_decision" | null;

export interface BubbleActions {
  pauseStatus: PauseStatus;
  isStreaming?: boolean;
  onAnswerQuestion?: (answer: string) => void;
  onDecision?: (card: OrchCard, decision: "approve" | "reject") => void;
}

function DecisionPreview({
  card,
  canRespond,
  isStreaming,
  onDecision,
}: {
  card: OrchCard;
  canRespond: boolean;
  isStreaming?: boolean;
  onDecision?: (card: OrchCard, decision: "approve" | "reject") => void;
}) {
  return (
    <AssistantBubble>
      <div className="text-xs font-semibold text-amber-700">等待决策</div>
      <div className="mt-1 font-medium text-[var(--text1)]">{card.title || "需要确认"}</div>
      <div className="mt-2 whitespace-pre-wrap text-[var(--text2)]">{card.detail}</div>
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          disabled={!canRespond || isStreaming}
          onClick={() => onDecision?.(card, "approve")}
          className="rounded-lg bg-[var(--brand)] px-3 py-1.5 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
        >
          批准
        </button>
        <button
          type="button"
          disabled={!canRespond || isStreaming}
          onClick={() => onDecision?.(card, "reject")}
          className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs font-medium text-[var(--text2)] disabled:cursor-not-allowed disabled:opacity-40"
        >
          拒绝
        </button>
      </div>
    </AssistantBubble>
  );
}

function AwaitingUserBubble({
  question,
  canRespond,
  isStreaming,
  onAnswer,
}: {
  question: string;
  canRespond: boolean;
  isStreaming?: boolean;
  onAnswer?: (answer: string) => void;
}) {
  const [answer, setAnswer] = useState("");
  const disabled = !canRespond || isStreaming || !answer.trim();

  return (
    <AssistantBubble>
      <div className="text-xs font-semibold text-amber-700">等待补充</div>
      <div className="mt-2 whitespace-pre-wrap">{question}</div>
      <form
        className="mt-3 flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          const trimmed = answer.trim();
          if (!trimmed || disabled) return;
          setAnswer("");
          onAnswer?.(trimmed);
        }}
      >
        <input
          value={answer}
          onChange={(event) => setAnswer(event.target.value)}
          disabled={!canRespond || isStreaming}
          placeholder={canRespond ? "补充回答…" : "等待本轮收流完成…"}
          className="min-w-0 flex-1 rounded-lg border border-[var(--border)] bg-white px-3 py-2 text-sm outline-none focus:border-[var(--brand)] disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={disabled}
          className="rounded-lg bg-[var(--brand)] px-3 py-2 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
        >
          答复
        </button>
      </form>
    </AssistantBubble>
  );
}

export function EventBubble({
  event,
  actions,
}: {
  event: OrchEvent;
  actions?: BubbleActions;
}) {
  // done=收流终止符；heartbeat=存活/进度信号；user_message=恢复时已转成用户气泡；
  // final_delta=真流式增量，由 page.tsx 累积进 final 气泡，不单独成气泡。
  if (
    event.type === "done" ||
    event.type === "heartbeat" ||
    event.type === "user_message" ||
    event.type === "final_delta"
  ) {
    return null;
  }

  if (event.type === "thinking") {
    return (
      <AssistantBubble>
        <div className="flex items-center gap-2 text-xs font-semibold text-[var(--text2)]">
          <span className="h-2 w-2 animate-pulse rounded-full bg-[var(--brand)]" />
          正在思考
        </div>
        <div className="mt-2 whitespace-pre-wrap">{event.summary}</div>
      </AssistantBubble>
    );
  }

  if (event.type === "subagent_start") {
    return (
      <SubagentCard archetype={event.archetype} title="进行中" tone="running">
        <div className="whitespace-pre-wrap text-[var(--text2)]">{event.task}</div>
      </SubagentCard>
    );
  }

  if (event.type === "subagent_result") {
    return (
      <SubagentCard
        archetype={event.archetype}
        title={event.ok ? "完成" : "失败"}
        tone={event.ok ? "ok" : "failed"}
      >
        <div className="whitespace-pre-wrap">{event.summary}</div>
        <ContentCards summary={event.summary} />
      </SubagentCard>
    );
  }

  if (event.type === "decision_card") {
    return (
      <DecisionPreview
        card={event.card}
        canRespond={actions?.pauseStatus === "awaiting_decision"}
        isStreaming={actions?.isStreaming}
        onDecision={actions?.onDecision}
      />
    );
  }

  if (event.type === "awaiting_user") {
    return (
      <AwaitingUserBubble
        question={event.question}
        canRespond={actions?.pauseStatus === "awaiting_user"}
        isStreaming={actions?.isStreaming}
        onAnswer={actions?.onAnswerQuestion}
      />
    );
  }

  if (event.type === "final") {
    // 真流式：summary 由 final_delta 增量累积、React 增量渲染即逐字浮现，无需打字机模拟。
    return (
      <AssistantBubble>
        <div className="whitespace-pre-wrap">{event.summary}</div>
        <ContentCards summary={event.summary} />
      </AssistantBubble>
    );
  }

  return (
    <div className="flex justify-start">
      <div className="max-w-[78%] rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
        {event.message}
      </div>
    </div>
  );
}

export function ChatBubble({
  entry,
  actions,
}: {
  entry: ChatEntry;
  actions?: BubbleActions;
}) {
  if (entry.kind === "user") return <UserBubble content={entry.content} />;
  return <EventBubble event={entry.event} actions={actions} />;
}
