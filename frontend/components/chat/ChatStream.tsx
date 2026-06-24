"use client";

import { useEffect, useRef } from "react";
import { ChatBubble, type BubbleActions, type ChatEntry } from "./bubbles";

export function ChatStream({
  entries,
  isStreaming,
  progressHint,
  actions,
}: {
  entries: ChatEntry[];
  isStreaming?: boolean;
  progressHint?: string | null;
  actions?: BubbleActions;
}) {
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const stickToBottomRef = useRef(true);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport || !stickToBottomRef.current) return;
    viewport.scrollTo({ top: viewport.scrollHeight, behavior: "smooth" });
  }, [entries.length, isStreaming]);

  return (
    <div
      ref={viewportRef}
      onScroll={(event) => {
        const el = event.currentTarget;
        stickToBottomRef.current =
          el.scrollHeight - el.scrollTop - el.clientHeight < 40;
      }}
      className="h-full overflow-y-auto px-4 py-6"
    >
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-4">
        {entries.map((entry) => (
          <ChatBubble key={entry.id} entry={entry} actions={actions} />
        ))}
        {isStreaming && (
          <div className="flex items-center justify-start gap-2">
            <div className="flex items-center gap-1 rounded-lg border border-[var(--border2)] bg-[var(--card-warm)] px-3 py-2">
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-[var(--text3)]" />
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-[var(--text3)] [animation-delay:120ms]" />
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-[var(--text3)] [animation-delay:240ms]" />
            </div>
            {progressHint && (
              <span className="text-xs text-[var(--text3)]">{progressHint}</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
