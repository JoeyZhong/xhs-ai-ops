"use client";

import { Suspense, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { addDays, format } from "date-fns";
import { Button } from "@/components/ui/button";
import {
  ApiError,
  draftsApi,
  type ContentDraft,
  type DraftListParams,
  type DraftStatus,
  type DraftUpdate,
  type FunnelStage,
} from "@/lib/api";
import { useGoalsStore } from "@/stores/goals";

const INPUT_CLS = "w-full border border-[var(--border)] rounded-lg px-3 py-2 text-sm outline-none focus:border-[var(--brand)] focus:ring-1 focus:ring-[var(--brand)] bg-white";

const STATUS_LABEL: Record<DraftStatus, string> = {
  draft: "草稿",
  edited: "已编辑",
  scheduled: "已排期",
  published: "已发布",
  rejected: "已弃用",
};

const STATUS_COLOR: Record<DraftStatus, string> = {
  draft: "bg-gray-100 text-gray-700",
  edited: "bg-amber-50 text-amber-700",
  scheduled: "bg-purple-50 text-purple-600",
  published: "bg-green-50 text-green-600",
  rejected: "bg-red-50 text-red-600",
};

const DEFAULT_PAGE_SIZE = 20;
const DEFAULT_SCHEDULED_TIME = "20:30";

/* ── Helpers ───────────────────────────────────────────────── */

function isRevMismatch(error: unknown): error is ApiError & { current_rev: number } {
  return error instanceof ApiError && error.code === "rev_mismatch" && typeof error.current_rev === "number";
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "操作失败";
}

function tomorrowDate(): string {
  return format(addDays(new Date(), 1), "yyyy-MM-dd");
}

function formatTimestamp(iso: string | undefined | null): string {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return format(d, "MM-dd HH:mm");
  } catch {
    return iso;
  }
}

function regenerateHref(draft: ContentDraft): string {
  const sp = new URLSearchParams();
  if (draft.topic_id) sp.set("topicId", draft.topic_id);
  if (draft.calendar_item_id) sp.set("calendarItemId", draft.calendar_item_id);
  if (draft.strategy_id) sp.set("strategyId", draft.strategy_id);
  const q = sp.toString();
  return q ? `/admin/content?${q}` : "/admin/content";
}

/* ── Schedule Modal ────────────────────────────────────────── */

interface ScheduleDraft {
  scheduled_date: string;
  scheduled_time: string;
  funnel_stage: FunnelStage;
}

function ScheduleModal({ draft, onClose, onConfirm, busy }: {
  draft: ContentDraft | null;
  onClose: () => void;
  onConfirm: (data: ScheduleDraft) => void;
  busy: boolean;
}) {
  const [date, setDate] = useState(tomorrowDate);
  const [time, setTime] = useState(DEFAULT_SCHEDULED_TIME);
  const [stage, setStage] = useState<FunnelStage>("trust");
  if (!draft) return null;

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-white rounded-xl p-5 w-96 shadow-lg" onClick={(e) => e.stopPropagation()}>
        <div className="text-sm font-semibold text-[var(--text1)] mb-3">排期到日历</div>
        <div className="space-y-3">
          <div className="text-xs text-[var(--text3)] line-clamp-2">{draft.title || "（无标题）"}</div>
          <div>
            <label className="block text-xs text-[var(--text2)] mb-1">发布日期</label>
            <input type="date" className={INPUT_CLS} value={date} onChange={(e) => setDate(e.target.value)} />
          </div>
          <div>
            <label className="block text-xs text-[var(--text2)] mb-1">发布时间</label>
            <input type="time" className={INPUT_CLS} value={time} onChange={(e) => setTime(e.target.value)} />
          </div>
          <div>
            <label className="block text-xs text-[var(--text2)] mb-1">漏斗阶段</label>
            <select className={INPUT_CLS} value={stage} onChange={(e) => setStage(e.target.value as FunnelStage)}>
              <option value="traffic">引流（30%）</option>
              <option value="trust">信任（40%）</option>
              <option value="conversion">转化（30%）</option>
            </select>
          </div>
          <div className="flex gap-2 pt-1">
            <Button size="sm" variant="outline" className="flex-1" onClick={onClose} disabled={busy}>取消</Button>
            <Button
              size="sm"
              className="flex-1"
              style={{ background: "var(--brand)", color: "white" }}
              disabled={busy}
              onClick={() =>
                onConfirm({
                  scheduled_date: date,
                  scheduled_time: time || null as unknown as string,
                  funnel_stage: stage,
                })
              }
            >
              {busy ? "排期中…" : "确认排期"}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── Reject Modal ──────────────────────────────────────────── */

function RejectModal({ draft, onClose, onConfirm, busy }: {
  draft: ContentDraft | null;
  onClose: () => void;
  onConfirm: (reason: string) => void;
  busy: boolean;
}) {
  const [reason, setReason] = useState("");
  if (!draft) return null;

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-white rounded-xl p-5 w-96 shadow-lg" onClick={(e) => e.stopPropagation()}>
        <div className="text-sm font-semibold text-[var(--text1)] mb-3">标记为不采用</div>
        <div className="space-y-3">
          <div className="text-xs text-[var(--text3)] line-clamp-2">{draft.title || "（无标题）"}</div>
          <div>
            <label className="block text-xs text-[var(--text2)] mb-1">原因（可选）</label>
            <textarea
              className={`${INPUT_CLS} h-20 resize-none`}
              placeholder="例：角度与本周重复 / 数据偏离 / 不符合品牌调性"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </div>
          <div className="flex gap-2 pt-1">
            <Button size="sm" variant="outline" className="flex-1" onClick={onClose} disabled={busy}>取消</Button>
            <Button
              size="sm"
              className="flex-1 bg-red-600 hover:bg-red-700 text-white"
              disabled={busy}
              onClick={() => onConfirm(reason.trim())}
            >
              {busy ? "处理中…" : "确认弃用"}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── Draft Row ─────────────────────────────────────────────── */

interface DraftRowProps {
  draft: ContentDraft;
  expanded: boolean;
  onToggle: () => void;
  onSave: (draft: ContentDraft, patch: Omit<DraftUpdate, "rev">) => Promise<void>;
  onDuplicate: (draft: ContentDraft) => void;
  onSchedule: (draft: ContentDraft) => void;
  onReject: (draft: ContentDraft) => void;
  saving: boolean;
}

function DraftRow({ draft, expanded, onToggle, onSave, onDuplicate, onSchedule, onReject, saving }: DraftRowProps) {
  const [editing, setEditing] = useState(false);
  const [editTitle, setEditTitle] = useState(draft.title ?? "");
  const [editBody, setEditBody] = useState(draft.body ?? "");
  const [editHashtags, setEditHashtags] = useState((draft.hashtags || []).join(" "));

  function startEdit() {
    setEditTitle(draft.title ?? "");
    setEditBody(draft.body ?? "");
    setEditHashtags((draft.hashtags || []).join(" "));
    setEditing(true);
  }

  async function commitEdit() {
    const patch: Omit<DraftUpdate, "rev"> = {};
    if (editTitle !== (draft.title ?? "")) patch.title = editTitle;
    if (editBody !== (draft.body ?? "")) patch.body = editBody;
    const hashtags = editHashtags
      .split(/[\s,，]+/)
      .map((t) => t.replace(/^#/, "").trim())
      .filter(Boolean);
    if (JSON.stringify(hashtags) !== JSON.stringify(draft.hashtags || [])) {
      patch.hashtags = hashtags;
    }
    if (Object.keys(patch).length === 0) {
      setEditing(false);
      return;
    }
    if (draft.status === "draft") patch.status = "edited";
    await onSave(draft, patch);
    setEditing(false);
  }

  const refs = [
    draft.topic_id ? { label: "选题", value: draft.topic_id, href: `/admin/topics?topicId=${encodeURIComponent(draft.topic_id)}` } : null,
    draft.strategy_id ? { label: "策略", value: draft.strategy_id } : null,
    draft.calendar_item_id ? { label: "日历", value: draft.calendar_item_id } : null,
  ].filter((r): r is { label: string; value: string; href?: string } => r !== null);

  return (
    <div className="bg-white border border-[var(--border)] rounded-xl overflow-hidden">
      <button
        type="button"
        className="w-full px-4 py-3 flex items-start gap-3 text-left hover:bg-gray-50 transition-colors"
        onClick={onToggle}
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-semibold text-[var(--text1)] truncate">{draft.title || "（无标题）"}</span>
            <span className={`text-[10px] px-2 py-0.5 rounded-full ${STATUS_COLOR[draft.status]}`}>
              {STATUS_LABEL[draft.status]}
            </span>
            {refs.map((r) => (
              <span
                key={`${r.label}-${r.value}`}
                className="text-[10px] px-2 py-0.5 rounded-full bg-[var(--brand)]/10 text-[var(--brand)] font-mono"
                title={`${r.label}: ${r.value}`}
              >
                {r.label}·{r.value.slice(0, 8)}
              </span>
            ))}
          </div>
          <div className="flex items-center gap-3 mt-1 text-[10px] text-[var(--text3)]">
            <span>更新于 {formatTimestamp(draft.updated_at)}</span>
            {draft.publish_at && <span>· 建议发布 {draft.publish_at}</span>}
            <span className="font-mono">{draft.content_id.slice(0, 8)}</span>
          </div>
        </div>
        <span className="text-[var(--text3)] text-xs mt-0.5">{expanded ? "▲" : "▼"}</span>
      </button>

      {expanded && (
        <div className="px-4 pb-4 border-t border-[var(--border)] space-y-3 pt-3">
          {editing ? (
            <div className="space-y-2">
              <div>
                <label className="block text-xs text-[var(--text2)] mb-1">标题</label>
                <input className={INPUT_CLS} value={editTitle} onChange={(e) => setEditTitle(e.target.value)} />
              </div>
              <div>
                <label className="block text-xs text-[var(--text2)] mb-1">正文</label>
                <textarea
                  className={`${INPUT_CLS} h-56 font-mono text-xs`}
                  value={editBody}
                  onChange={(e) => setEditBody(e.target.value)}
                />
              </div>
              <div>
                <label className="block text-xs text-[var(--text2)] mb-1">标签（空格分隔）</label>
                <input className={INPUT_CLS} value={editHashtags} onChange={(e) => setEditHashtags(e.target.value)} />
              </div>
              <div className="flex gap-2">
                <Button size="sm" style={{ background: "var(--brand)", color: "white" }} onClick={commitEdit} disabled={saving}>
                  {saving ? "保存中…" : "✓ 保存"}
                </Button>
                <Button size="sm" variant="outline" onClick={() => setEditing(false)} disabled={saving}>
                  取消
                </Button>
              </div>
            </div>
          ) : (
            <>
              <div className="bg-gray-50 rounded-lg p-3 text-sm text-[var(--text1)] leading-relaxed max-h-72 overflow-y-auto whitespace-pre-wrap">
                {draft.body || "（无正文）"}
              </div>
              {(draft.hashtags?.length ?? 0) > 0 && (
                <div className="flex flex-wrap gap-1">
                  {draft.hashtags.map((t) => (
                    <span key={t} className="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-[var(--text2)]">#{t}</span>
                  ))}
                </div>
              )}
              <div className="flex flex-wrap gap-2">
                <Button size="sm" variant="outline" onClick={startEdit} disabled={draft.status === "published" || draft.status === "rejected"}>
                  ✎ 编辑
                </Button>
                <Button size="sm" variant="outline" onClick={() => onDuplicate(draft)}>
                  ⎘ 复制
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => onSchedule(draft)}
                  disabled={draft.status === "rejected" || draft.status === "published"}
                >
                  📅 加入日历
                </Button>
                <Link
                  href={regenerateHref(draft)}
                  className="text-xs px-3 py-1.5 rounded-lg border border-[var(--border)] text-[var(--text2)] hover:border-[var(--brand)] hover:text-[var(--brand)] transition-colors"
                >
                  🔄 重新生成
                </Link>
                <Button
                  size="sm"
                  variant="outline"
                  className="border-red-200 text-red-600 hover:bg-red-50"
                  onClick={() => onReject(draft)}
                  disabled={draft.status === "rejected"}
                >
                  ✕ 标记不采用
                </Button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

/* ── Filters Bar ───────────────────────────────────────────── */

interface FilterState {
  status: DraftStatus | "";
  topicId: string;
  personaId: string;
  dateFrom: string;
  dateTo: string;
  pageSize: number;
}

function FiltersBar({ filters, onChange, onReset }: {
  filters: FilterState;
  onChange: (next: FilterState) => void;
  onReset: () => void;
}) {
  return (
    <div className="bg-white border border-[var(--border)] rounded-xl p-4 mb-4 space-y-3">
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        <div>
          <label className="block text-xs text-[var(--text2)] mb-1">状态</label>
          <select
            className={INPUT_CLS}
            value={filters.status}
            onChange={(e) => onChange({ ...filters, status: e.target.value as DraftStatus | "" })}
          >
            <option value="">全部</option>
            {(Object.keys(STATUS_LABEL) as DraftStatus[]).map((s) => (
              <option key={s} value={s}>{STATUS_LABEL[s]}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs text-[var(--text2)] mb-1">选题 ID</label>
          <input
            className={INPUT_CLS}
            placeholder="topic_xxx"
            value={filters.topicId}
            onChange={(e) => onChange({ ...filters, topicId: e.target.value })}
          />
        </div>
        <div>
          <label className="block text-xs text-[var(--text2)] mb-1">人设 ID</label>
          <input
            className={INPUT_CLS}
            placeholder="persona_xxx"
            value={filters.personaId}
            onChange={(e) => onChange({ ...filters, personaId: e.target.value })}
          />
        </div>
        <div>
          <label className="block text-xs text-[var(--text2)] mb-1">起始日期</label>
          <input
            type="date"
            className={INPUT_CLS}
            value={filters.dateFrom}
            onChange={(e) => onChange({ ...filters, dateFrom: e.target.value })}
          />
        </div>
        <div>
          <label className="block text-xs text-[var(--text2)] mb-1">截止日期</label>
          <input
            type="date"
            className={INPUT_CLS}
            value={filters.dateTo}
            onChange={(e) => onChange({ ...filters, dateTo: e.target.value })}
          />
        </div>
        <div>
          <label className="block text-xs text-[var(--text2)] mb-1">每页</label>
          <select
            className={INPUT_CLS}
            value={filters.pageSize}
            onChange={(e) => onChange({ ...filters, pageSize: Number(e.target.value) })}
          >
            {[10, 20, 50, 100].map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </div>
      </div>
      <div className="flex justify-end">
        <Button size="xs" variant="ghost" onClick={onReset}>重置筛选</Button>
      </div>
    </div>
  );
}

/* ── Main Page ─────────────────────────────────────────────── */

function DraftsPageInner() {
  const { activeGoalId } = useGoalsStore();
  const searchParams = useSearchParams();
  const qc = useQueryClient();

  const initialContentId = searchParams.get("content_id");

  const [filters, setFilters] = useState<FilterState>({
    status: "",
    topicId: searchParams.get("topicId") ?? "",
    personaId: "",
    dateFrom: "",
    dateTo: "",
    pageSize: DEFAULT_PAGE_SIZE,
  });
  const [page, setPage] = useState(1);
  const [expandedId, setExpandedId] = useState<string | null>(initialContentId);

  const [scheduleTarget, setScheduleTarget] = useState<ContentDraft | null>(null);
  const [rejectTarget, setRejectTarget] = useState<ContentDraft | null>(null);
  const [savingId, setSavingId] = useState<string | null>(null);

  const params: DraftListParams = useMemo(() => {
    const p: DraftListParams = { page, page_size: filters.pageSize };
    if (activeGoalId) p.goal_id = activeGoalId;
    if (filters.status) p.status = filters.status;
    if (filters.topicId) p.topic_id = filters.topicId;
    if (filters.personaId) p.persona_id = filters.personaId;
    if (filters.dateFrom) p.date_from = filters.dateFrom;
    if (filters.dateTo) p.date_to = filters.dateTo;
    return p;
  }, [activeGoalId, filters, page]);

  const listQuery = useQuery({
    queryKey: ["drafts-list", params],
    queryFn: () => draftsApi.list(params),
  });

  async function reloadList() {
    await qc.invalidateQueries({ queryKey: ["drafts-list"] });
  }

  /* ── OCC-aware mutators ─────────────────────────────────── */

  async function updateDraftWithRetry(draft: ContentDraft, patch: Omit<DraftUpdate, "rev">) {
    const rev = draft.rev ?? 1;
    try {
      return await draftsApi.update(draft.content_id, { ...patch, rev });
    } catch (error) {
      if (isRevMismatch(error)) {
        await reloadList();
        return draftsApi.update(draft.content_id, { ...patch, rev: error.current_rev });
      }
      throw error;
    }
  }

  const duplicateMut = useMutation({
    mutationFn: (draft: ContentDraft) => draftsApi.duplicate(draft.content_id, {}),
    onSuccess: async (newDraft) => {
      await reloadList();
      setExpandedId(newDraft.content_id);
    },
    onError: (err) => alert(`复制失败：${errorMessage(err)}`),
  });

  const scheduleMut = useMutation({
    mutationFn: ({ draft, data }: { draft: ContentDraft; data: ScheduleDraft }) =>
      draftsApi.schedule(draft.content_id, data),
    onSuccess: async () => {
      setScheduleTarget(null);
      await reloadList();
    },
    onError: (err) => alert(`排期失败：${errorMessage(err)}`),
  });

  const rejectMut = useMutation({
    mutationFn: ({ draft, reason }: { draft: ContentDraft; reason: string }) =>
      draftsApi.reject(draft.content_id, reason ? { reason } : {}),
    onSuccess: async () => {
      setRejectTarget(null);
      await reloadList();
    },
    onError: (err) => alert(`标记不采用失败：${errorMessage(err)}`),
  });

  async function handleSave(draft: ContentDraft, patch: Omit<DraftUpdate, "rev">) {
    setSavingId(draft.content_id);
    try {
      await updateDraftWithRetry(draft, patch);
      await reloadList();
    } catch (err) {
      alert(`保存失败：${errorMessage(err)}`);
    } finally {
      setSavingId(null);
    }
  }

  function resetFilters() {
    setFilters({
      status: "",
      topicId: "",
      personaId: "",
      dateFrom: "",
      dateTo: "",
      pageSize: DEFAULT_PAGE_SIZE,
    });
    setPage(1);
  }

  function changeFilters(next: FilterState) {
    setFilters(next);
    setPage(1);
  }

  const data = listQuery.data;
  const items = data?.items ?? [];
  const total = data?.total ?? null;
  const hasMore = data?.has_more ?? false;

  return (
    <div className="max-w-4xl mx-auto">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-bold text-[var(--text1)]">草稿箱</h1>
        <Link
          href="/admin/content"
          className="text-xs px-3 py-1.5 rounded-lg border border-[var(--border)] text-[var(--text2)] hover:border-[var(--brand)] hover:text-[var(--brand)] transition-colors"
        >
          ＋ 新建内容
        </Link>
      </div>

      <FiltersBar filters={filters} onChange={changeFilters} onReset={resetFilters} />

      {listQuery.isLoading && (
        <div className="bg-white border border-[var(--border)] rounded-xl p-8 text-center text-sm text-[var(--text2)]">
          加载草稿中…
        </div>
      )}

      {listQuery.isError && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700">
          加载失败：{errorMessage(listQuery.error)}
        </div>
      )}

      {!listQuery.isLoading && !listQuery.isError && items.length === 0 && (
        <div className="bg-white border border-[var(--border)] rounded-xl p-12 text-center text-[var(--text2)]">
          <div className="text-3xl mb-2">🗂</div>
          <p className="text-sm font-medium text-[var(--text1)] mb-1">还没有草稿</p>
          <p className="text-xs">去「内容创作」生成第一篇，或调整筛选条件</p>
        </div>
      )}

      {items.length > 0 && (
        <div className="space-y-2">
          <div className="text-xs text-[var(--text3)] mb-1">
            共 {total ?? "?"} 条 · 第 {page} 页
            {data && <span> · 每页 {data.page_size}</span>}
          </div>
          {items.map((draft) => (
            <DraftRow
              key={draft.content_id}
              draft={draft}
              expanded={expandedId === draft.content_id}
              onToggle={() =>
                setExpandedId((prev) => (prev === draft.content_id ? null : draft.content_id))
              }
              onSave={handleSave}
              onDuplicate={(d) => duplicateMut.mutate(d)}
              onSchedule={(d) => setScheduleTarget(d)}
              onReject={(d) => setRejectTarget(d)}
              saving={savingId === draft.content_id}
            />
          ))}

          <div className="flex items-center justify-between pt-2">
            <Button
              size="sm"
              variant="outline"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              ← 上一页
            </Button>
            <span className="text-xs text-[var(--text3)]">第 {page} 页</span>
            <Button
              size="sm"
              variant="outline"
              disabled={!hasMore}
              onClick={() => setPage((p) => p + 1)}
            >
              下一页 →
            </Button>
          </div>
        </div>
      )}

      <ScheduleModal
        draft={scheduleTarget}
        onClose={() => setScheduleTarget(null)}
        onConfirm={(data) => {
          if (scheduleTarget) scheduleMut.mutate({ draft: scheduleTarget, data });
        }}
        busy={scheduleMut.isPending}
      />

      <RejectModal
        draft={rejectTarget}
        onClose={() => setRejectTarget(null)}
        onConfirm={(reason) => {
          if (rejectTarget) rejectMut.mutate({ draft: rejectTarget, reason });
        }}
        busy={rejectMut.isPending}
      />

    </div>
  );
}

export default function DraftsPage() {
  return (
    <Suspense fallback={<div className="py-16 text-center text-sm text-[var(--text2)]">加载中…</div>}>
      <DraftsPageInner />
    </Suspense>
  );
}
