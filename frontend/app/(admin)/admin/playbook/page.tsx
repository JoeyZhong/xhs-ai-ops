"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";

interface DraftItem {
  id: string;
  body: string;
  status: string;
  source: string;
  confidence: string;
  rev: number;
}

interface DraftListResponse {
  items: DraftItem[];
  total: number;
}

/* ── Draft Card ─────────────────────────────────────────────────────────── */

function DraftCard({ item }: { item: DraftItem }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draftBody, setDraftBody] = useState(item.body);

  const acceptMut = useMutation({
    mutationFn: () =>
      apiFetch(`/api/v1/playbook/drafts/${item.id}/accept`, { method: "POST" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["playbook-drafts"] });
      qc.invalidateQueries({ queryKey: ["playbook-drafts-count"] });
    },
  });

  const rejectMut = useMutation({
    mutationFn: () =>
      apiFetch(`/api/v1/playbook/drafts/${item.id}/reject`, { method: "POST" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["playbook-drafts"] });
      qc.invalidateQueries({ queryKey: ["playbook-drafts-count"] });
    },
  });

  const editMut = useMutation({
    mutationFn: (body: string) =>
      apiFetch(`/api/v1/playbook/drafts/${item.id}`, {
        method: "PUT",
        body: JSON.stringify({ body }),
      }),
    onSuccess: () => {
      setEditing(false);
      qc.invalidateQueries({ queryKey: ["playbook-drafts"] });
      qc.invalidateQueries({ queryKey: ["playbook-drafts-count"] });
    },
  });

  const isPending = acceptMut.isPending || rejectMut.isPending || editMut.isPending;

  return (
    <div className="bg-white border border-[var(--border)] rounded-xl overflow-hidden">
      {/* Header */}
      <div className="px-4 py-3 flex items-center gap-3 border-b border-[var(--border)]">
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-[var(--text1)] truncate">{item.id}</p>
          <div className="flex items-center gap-2 mt-0.5 flex-wrap">
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-100 text-amber-700">
              {item.source}
            </span>
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-[var(--text2)]">
              rev:{item.rev}
            </span>
          </div>
        </div>
      </div>

      {/* Body */}
      <div className="px-4 py-3">
        {editing ? (
          <textarea
            className="w-full border border-[var(--border)] rounded-lg px-3 py-2 text-sm outline-none focus:border-[var(--brand)] focus:ring-1 focus:ring-[var(--brand)] bg-white h-40 font-mono text-xs resize-y"
            value={draftBody}
            onChange={(e) => setDraftBody(e.target.value)}
          />
        ) : (
          <div className="text-sm text-[var(--text1)] leading-relaxed whitespace-pre-wrap max-h-60 overflow-y-auto">
            {item.body}
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="px-4 py-3 border-t border-[var(--border)] flex items-center gap-2">
        {editing ? (
          <>
            <Button
              size="sm"
              style={{ background: "var(--brand)", color: "white" }}
              onClick={() => editMut.mutate(draftBody)}
              disabled={isPending}
            >
              {editMut.isPending ? "保存中…" : "✓ 保存并采纳"}
            </Button>
            <Button size="sm" variant="outline" onClick={() => { setEditing(false); setDraftBody(item.body); }} disabled={isPending}>
              取消
            </Button>
          </>
        ) : (
          <>
            <Button
              size="sm"
              style={{ background: "var(--brand)", color: "white" }}
              onClick={() => acceptMut.mutate()}
              disabled={isPending}
            >
              {acceptMut.isPending ? "处理中…" : "✓ 采纳"}
            </Button>
            <Button size="sm" variant="outline" onClick={() => setEditing(true)} disabled={isPending}>
              ✎ 编辑后采纳
            </Button>
            <Button size="sm" variant="destructive" onClick={() => rejectMut.mutate()} disabled={isPending}>
              {rejectMut.isPending ? "处理中…" : "✕ 驳回"}
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

/* ── Main Page ──────────────────────────────────────────────────────────── */

export default function PlaybookPage() {
  const { data, isLoading, isError } = useQuery<DraftListResponse>({
    queryKey: ["playbook-drafts"],
    queryFn: () => apiFetch<DraftListResponse>("/api/v1/playbook/drafts"),
  });

  return (
    <div className="max-w-2xl mx-auto">
      <div className="flex items-center justify-between mb-5">
        <h1 className="text-xl font-bold text-[var(--text1)]">Playbook 审阅</h1>
        {data && data.total > 0 && (
          <span className="text-xs px-2.5 py-1 rounded-full bg-amber-100 text-amber-700">
            待审阅 {data.total} 条
          </span>
        )}
      </div>

      {isLoading && (
        <div className="text-center py-16 text-sm text-[var(--text2)]">加载中…</div>
      )}

      {isError && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700">
          加载失败，请检查后端是否启动
        </div>
      )}

      {data && data.total === 0 && (
        <div className="text-center py-16 text-[var(--text2)]">
          <div className="text-4xl mb-3">📖</div>
          <p className="text-sm font-medium text-[var(--text1)] mb-1">暂无待审阅条目</p>
          <p className="text-xs">Scheduler 生成的周报 draft 会出现在这里</p>
        </div>
      )}

      {data && data.total > 0 && (
        <div className="space-y-3">
          {data.items.map((item) => (
            <DraftCard key={item.id} item={item} />
          ))}
        </div>
      )}
    </div>
  );
}
