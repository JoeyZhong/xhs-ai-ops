"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Plus, Trash2, ChevronLeft, ChevronRight, MessageSquare } from "lucide-react";
import { orchestratorApi, type OrchSessionListItem } from "@/lib/api";

const COLLAPSE_KEY = "spider-xhs-history-collapsed";

function readCollapsed(): boolean {
  if (typeof window === "undefined") return false;
  return window.localStorage.getItem(COLLAPSE_KEY) === "1";
}

function statusMeta(status: string): { color: string; label: string } {
  if (status === "done") return { color: "var(--agent-intel)", label: "已完成" };
  if (status === "cancelled") return { color: "var(--border2)", label: "已取消" };
  if (status === "awaiting_user" || status === "awaiting_decision")
    return { color: "var(--agent-analyst)", label: "待补充" };
  return { color: "var(--agent-analyst)", label: "进行中" };
}

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const diff = Date.now() - then;
  const MIN = 60_000, HR = 3_600_000, DAY = 86_400_000;
  if (diff < MIN) return "刚刚";
  if (diff < HR) return `${Math.floor(diff / MIN)} 分钟前`;
  if (diff < DAY) return `${Math.floor(diff / HR)} 小时前`;
  const days = Math.floor(diff / DAY);
  if (days === 1) return "昨天";
  if (days < 7) return `${days} 天前`;
  return new Date(iso).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}

function isToday(iso: string): boolean {
  const d = new Date(iso);
  const n = new Date();
  return (
    d.getFullYear() === n.getFullYear() &&
    d.getMonth() === n.getMonth() &&
    d.getDate() === n.getDate()
  );
}

export function HistoryDrawer({
  goalId,
  activeSessionId,
  onSelect,
  onNewChat,
}: {
  goalId: string | null;
  activeSessionId: string | null;
  onSelect: (sessionId: string) => void;
  onNewChat: () => void;
}) {
  const [collapsed, setCollapsed] = useState<boolean>(() => readCollapsed());

  function toggleCollapsed() {
    setCollapsed((prev) => {
      const next = !prev;
      window.localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0");
      return next;
    });
  }

  const { data, refetch } = useQuery({
    queryKey: ["sessions", goalId],
    queryFn: () => orchestratorApi.listSessions(goalId),
    retry: false,
  });

  const sessions = data?.sessions ?? [];
  const today = sessions.filter((s) => isToday(s.updated_at));
  const earlier = sessions.filter((s) => !isToday(s.updated_at));

  async function handleDelete(event: React.MouseEvent, sessionId: string) {
    event.stopPropagation();
    if (!window.confirm("删除这条对话？")) return;
    await orchestratorApi.deleteSession(sessionId);
    await refetch();
    if (sessionId === activeSessionId) onNewChat();
  }

  if (collapsed) {
    return (
      <div className="flex w-14 shrink-0 flex-col items-center gap-4 border-r border-[var(--border2)] bg-[var(--card-warm)] pb-[60px] pt-4">
        <button
          type="button"
          onClick={onNewChat}
          aria-label="新建对话"
          className="grid h-9 w-9 place-items-center rounded-lg border border-[var(--brand)] text-[var(--brand)] transition-colors hover:bg-[var(--brand)] hover:text-white"
        >
          <Plus className="h-[18px] w-[18px]" />
        </button>
        <MessageSquare className="h-[18px] w-[18px] text-[var(--text3)]" />
        <button
          type="button"
          onClick={toggleCollapsed}
          aria-label="展开历史"
          className="mt-auto grid h-8 w-8 place-items-center rounded-lg text-[var(--text2)] hover:bg-[var(--bg)]"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>
    );
  }

  return (
    <div className="flex w-60 shrink-0 flex-col border-r border-[var(--border2)] bg-[var(--card-warm)]">
      <div className="flex items-center justify-between px-3 py-3">
        <span className="text-xs font-semibold uppercase tracking-wide text-[var(--text3)]">
          对话历史
        </span>
        <button
          type="button"
          onClick={toggleCollapsed}
          aria-label="收起历史"
          className="grid h-7 w-7 place-items-center rounded-lg text-[var(--text2)] hover:bg-[var(--bg)]"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
      </div>

      <div className="px-3">
        <button
          type="button"
          onClick={onNewChat}
          className="flex w-full items-center justify-center gap-2 rounded-lg border border-[var(--brand)] bg-white px-3 py-2 text-sm font-semibold text-[var(--brand)] transition-colors hover:bg-[var(--brand)] hover:text-white"
        >
          <Plus className="h-[15px] w-[15px]" />
          新建对话
        </button>
      </div>

      <div className="mt-3 min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        {sessions.length === 0 ? (
          <p className="px-2 py-6 text-center text-xs text-[var(--text3)]">
            还没有历史对话
          </p>
        ) : (
          <>
            <SessionGroup label="今天" items={today} activeSessionId={activeSessionId} onSelect={onSelect} onDelete={handleDelete} />
            <SessionGroup label="更早" items={earlier} activeSessionId={activeSessionId} onSelect={onSelect} onDelete={handleDelete} />
          </>
        )}
      </div>
    </div>
  );
}

function SessionGroup({
  label,
  items,
  activeSessionId,
  onSelect,
  onDelete,
}: {
  label: string;
  items: OrchSessionListItem[];
  activeSessionId: string | null;
  onSelect: (sessionId: string) => void;
  onDelete: (event: React.MouseEvent, sessionId: string) => void;
}) {
  if (items.length === 0) return null;
  return (
    <div className="mb-2">
      <div className="px-2 py-1 text-[10px] uppercase tracking-wider text-[var(--text3)]">
        {label}
      </div>
      {items.map((item) => {
        const meta = statusMeta(item.status);
        const active = item.session_id === activeSessionId;
        return (
          <div
            key={item.session_id}
            onClick={() => onSelect(item.session_id)}
            className={`group relative cursor-pointer rounded-lg px-2.5 py-2 ${
              active ? "bg-white shadow-[inset_3px_0_0_var(--brand)]" : "hover:bg-white"
            }`}
          >
            <div className="truncate pr-5 text-[13px] font-medium text-[var(--text1)]">
              {item.title || "（未命名对话）"}
            </div>
            <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-[var(--text3)]">
              <span className="h-1.5 w-1.5 rounded-full" style={{ background: meta.color }} />
              {meta.label} · {relativeTime(item.updated_at)}
            </div>
            <button
              type="button"
              onClick={(event) => onDelete(event, item.session_id)}
              aria-label="删除对话"
              className="absolute right-1.5 top-1.5 opacity-0 transition-opacity group-hover:opacity-100"
            >
              <Trash2 className="h-3.5 w-3.5 text-[var(--text3)] hover:text-[var(--brand)]" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
