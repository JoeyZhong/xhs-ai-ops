"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { addDays, format, isSameDay } from "date-fns";
import { zhCN } from "date-fns/locale";
import { CalendarPlus, FileText, Save, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import {
  ApiError,
  apiFetch,
  calendarApi,
  topicsApi,
  type CalendarItem as ApiCalendarItem,
  type CalendarItemCreate,
  type CalendarItemUpdate,
  type CalendarStatus,
  type FunnelStage,
  type Topic as ApiTopic,
  type TopicStatus,
  type TopicUpdate,
} from "@/lib/api";
import { useGoalsStore } from "@/stores/goals";

/* ── Types ─────────────────────────────────────────────────────────────── */

interface GeneratedTopic {
  title: string;
  angle: string;
  formula?: string;
  keywords: string[];
}

interface TopicsResponse {
  topics: GeneratedTopic[];
  goal_id: string;
  error?: string;
}

type TopicChanges = Omit<TopicUpdate, "rev">;
type CalendarChanges = Omit<CalendarItemUpdate, "rev">;

interface CalendarDraft {
  topicId?: string;
  scheduledDate: string;
  scheduledTime: string;
  funnelStage: FunnelStage;
}

/* ── Helpers ───────────────────────────────────────────────────────────── */

const DEFAULT_SCHEDULED_TIME = "20:30";

const ANGLE_COLOR: Record<string, string> = {
  "反直觉型": "#e67e22",
  "数字清单型": "#2980b9",
  "本地汇总型": "#27ae60",
  "工具型": "#8e44ad",
  "焦虑共鸣型": "#c0392b",
};

const TOPIC_STATUS_LABEL: Record<TopicStatus, string> = {
  idea: "想法",
  planned: "已计划",
  drafting: "草稿中",
  drafted: "已成稿",
  scheduled: "已排期",
  published: "已发布",
  archived: "已归档",
};

const TOPIC_STATUS_COLOR: Record<TopicStatus, string> = {
  idea: "bg-gray-100 text-gray-600",
  planned: "bg-blue-50 text-blue-600",
  drafting: "bg-amber-50 text-amber-700",
  drafted: "bg-indigo-50 text-indigo-600",
  scheduled: "bg-purple-50 text-purple-600",
  published: "bg-green-50 text-green-600",
  archived: "bg-gray-100 text-gray-400",
};

const FUNNEL_STAGE_LABEL: Record<FunnelStage, string> = {
  traffic: "引流",
  trust: "信任",
  conversion: "转化",
};

const FUNNEL_STAGE_COLOR: Record<FunnelStage, string> = {
  traffic: "bg-orange-50 text-orange-600 border-orange-200",
  trust: "bg-blue-50 text-blue-600 border-blue-200",
  conversion: "bg-green-50 text-green-600 border-green-200",
};

const CALENDAR_STATUS_LABEL: Record<CalendarStatus, string> = {
  planned: "已计划",
  drafted: "已有草稿",
  scheduled: "已排期",
  published: "已发布",
  cancelled: "已取消",
};

const CALENDAR_STATUS_COLOR: Record<CalendarStatus, string> = {
  planned: "bg-blue-50 text-blue-600",
  drafted: "bg-indigo-50 text-indigo-600",
  scheduled: "bg-purple-50 text-purple-600",
  published: "bg-green-50 text-green-600",
  cancelled: "bg-gray-100 text-gray-500",
};

function tomorrowDate(): string {
  return format(addDays(new Date(), 1), "yyyy-MM-dd");
}

function isRevMismatch(error: unknown): error is ApiError & { current_rev: number } {
  return error instanceof ApiError && error.code === "rev_mismatch" && typeof error.current_rev === "number";
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "操作失败，请稍后重试";
}

function keywordRefs(topic: GeneratedTopic) {
  return topic.keywords.map((keyword) => ({
    type: "keyword",
    id: keyword,
    label: keyword,
  }));
}

function canScheduleTopic(topic: ApiTopic): boolean {
  return topic.status !== "archived" && topic.status !== "published";
}

function calendarTitle(item: ApiCalendarItem, topicById: Map<string, ApiTopic>): string {
  if (item.topic_id) {
    return topicById.get(item.topic_id)?.title ?? `选题 ${item.topic_id}`;
  }
  return item.content_id ? `内容 ${item.content_id}` : "未关联选题";
}

/* ── Sub-components ────────────────────────────────────────────────────── */

function MiniCalendar({
  calendar,
  topicById,
  onAdd,
}: {
  calendar: ApiCalendarItem[];
  topicById: Map<string, ApiTopic>;
  onAdd: (date: string) => void;
}) {
  const days = Array.from({ length: 14 }, (_, i) => {
    const d = addDays(new Date(), i);
    const dateStr = format(d, "yyyy-MM-dd");
    return {
      date: d,
      dateStr,
      weekday: format(d, "EEE", { locale: zhCN }),
      dayNum: format(d, "d"),
      items: calendar.filter((c) => c.scheduled_date === dateStr),
    };
  });

  return (
    <div className="bg-white border border-[var(--border)] rounded-xl p-4 mb-5">
      <div className="text-sm font-semibold text-[var(--text1)] mb-3">
        内容日历（未来 14 天）
      </div>
      <div className="grid grid-cols-7 gap-2">
        {days.map((d) => (
          <button
            key={d.dateStr}
            type="button"
            className={`border rounded-lg p-2 min-h-[80px] text-left hover:border-[var(--brand)] transition-colors ${
              isSameDay(d.date, new Date()) ? "border-[var(--brand)] bg-[var(--brand)]/5" : "border-[var(--border)]"
            }`}
            onClick={() => onAdd(d.dateStr)}
          >
            <div className="flex justify-between items-center mb-1">
              <span className="text-[10px] text-[var(--text3)]">{d.weekday}</span>
              <span
                className={`text-xs font-semibold ${
                  isSameDay(d.date, new Date()) ? "text-[var(--brand)]" : "text-[var(--text1)]"
                }`}
              >
                {d.dayNum}
              </span>
            </div>
            <div className="space-y-1">
              {d.items.map((item) => {
                const stage = item.funnel_stage ?? "trust";
                const title = calendarTitle(item, topicById);
                return (
                  <div
                    key={item.calendar_item_id}
                    className={`text-[9px] px-1.5 py-0.5 rounded border truncate ${FUNNEL_STAGE_COLOR[stage]}`}
                    title={title}
                  >
                    {FUNNEL_STAGE_LABEL[stage]} · {title.slice(0, 8)}
                    {title.length > 8 ? "…" : ""}
                  </div>
                );
              })}
              {d.items.length === 0 && (
                <div className="text-[10px] text-[var(--text3)] text-center mt-2">+</div>
              )}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

function CalendarCreateModal({
  draft,
  topics,
  onClose,
  onConfirm,
  isPending,
}: {
  draft: CalendarDraft;
  topics: ApiTopic[];
  onClose: () => void;
  onConfirm: (data: CalendarItemCreate) => void;
  isPending: boolean;
}) {
  const [selectedTopicId, setSelectedTopicId] = useState(draft.topicId ?? "");
  const [scheduledDate, setScheduledDate] = useState(draft.scheduledDate);
  const [scheduledTime, setScheduledTime] = useState(draft.scheduledTime);
  const [funnelStage, setFunnelStage] = useState<FunnelStage>(draft.funnelStage);
  const selectableTopics = topics.filter((topic) => canScheduleTopic(topic) || topic.topic_id === draft.topicId);

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-white rounded-xl p-5 w-[420px] shadow-lg" onClick={(e) => e.stopPropagation()}>
        <div className="text-sm font-semibold text-[var(--text1)] mb-3">加入内容日历</div>

        <div className="space-y-3">
          <div>
            <label className="block text-xs text-[var(--text2)] mb-1">选题</label>
            <select
              className="w-full border border-[var(--border)] rounded-lg px-3 py-2 text-sm outline-none bg-white"
              value={selectedTopicId}
              onChange={(e) => setSelectedTopicId(e.target.value)}
            >
              <option value="">选择一个选题</option>
              {selectableTopics.map((topic) => (
                <option key={topic.topic_id} value={topic.topic_id}>
                  {topic.title}
                </option>
              ))}
            </select>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="block text-xs text-[var(--text2)] mb-1">日期</label>
              <input
                type="date"
                className="w-full border border-[var(--border)] rounded-lg px-3 py-2 text-sm outline-none"
                value={scheduledDate}
                onChange={(e) => setScheduledDate(e.target.value)}
              />
            </div>
            <div>
              <label className="block text-xs text-[var(--text2)] mb-1">时间</label>
              <input
                type="time"
                className="w-full border border-[var(--border)] rounded-lg px-3 py-2 text-sm outline-none"
                value={scheduledTime}
                onChange={(e) => setScheduledTime(e.target.value)}
              />
            </div>
          </div>

          <div>
            <label className="block text-xs text-[var(--text2)] mb-1">漏斗阶段</label>
            <select
              className="w-full border border-[var(--border)] rounded-lg px-3 py-2 text-sm outline-none bg-white"
              value={funnelStage}
              onChange={(e) => setFunnelStage(e.target.value as FunnelStage)}
            >
              {Object.entries(FUNNEL_STAGE_LABEL).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="flex gap-2 pt-4">
          <Button size="sm" variant="outline" className="flex-1" onClick={onClose}>
            取消
          </Button>
          <Button
            size="sm"
            className="flex-1"
            style={{ background: "var(--brand)", color: "white" }}
            onClick={() => {
              if (!selectedTopicId || !scheduledDate) return;
              onConfirm({
                topic_id: selectedTopicId,
                scheduled_date: scheduledDate,
                scheduled_time: scheduledTime || null,
                funnel_stage: funnelStage,
              });
            }}
            disabled={!selectedTopicId || !scheduledDate || isPending}
          >
            {isPending ? "加入中…" : "确认加入"}
          </Button>
        </div>
      </div>
    </div>
  );
}

function CalendarList({
  items,
  topicById,
  onUpdate,
  onDelete,
  onGenerateContent,
}: {
  items: ApiCalendarItem[];
  topicById: Map<string, ApiTopic>;
  onUpdate: (item: ApiCalendarItem, changes: CalendarChanges) => void;
  onDelete: (item: ApiCalendarItem) => void;
  onGenerateContent: (item: ApiCalendarItem) => void;
}) {
  return (
    <div className="mb-6">
      <div className="text-sm font-semibold text-[var(--text1)] mb-2">
        日历条目
        <span className="text-xs text-[var(--text3)] font-normal ml-2">{items.length} 个</span>
      </div>

      {items.length === 0 ? (
        <div className="bg-gray-50 border border-dashed border-[var(--border)] rounded-xl p-6 text-center">
          <p className="text-xs text-[var(--text3)]">点击日历日期，或在选题行点击「加入日历」创建排期</p>
        </div>
      ) : (
        <div className="space-y-2">
          {items.map((item) => {
            const title = calendarTitle(item, topicById);
            return (
              <div key={item.calendar_item_id} className="bg-white border border-[var(--border)] rounded-xl p-3">
                <div className="flex items-start gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-2">
                      <span className="text-sm font-medium text-[var(--text1)] truncate">{title}</span>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${CALENDAR_STATUS_COLOR[item.status]}`}>
                        {CALENDAR_STATUS_LABEL[item.status]}
                      </span>
                    </div>

                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                      <input
                        type="date"
                        className="border border-[var(--border)] rounded px-2 py-1 text-xs outline-none"
                        value={item.scheduled_date}
                        onChange={(e) => onUpdate(item, { scheduled_date: e.target.value })}
                      />
                      <input
                        type="time"
                        className="border border-[var(--border)] rounded px-2 py-1 text-xs outline-none"
                        value={item.scheduled_time ?? ""}
                        onChange={(e) => onUpdate(item, { scheduled_time: e.target.value || null })}
                      />
                      <select
                        className="border border-[var(--border)] rounded px-2 py-1 text-xs outline-none bg-white"
                        value={item.funnel_stage ?? ""}
                        onChange={(e) =>
                          onUpdate(item, {
                            funnel_stage: e.target.value ? (e.target.value as FunnelStage) : null,
                          })
                        }
                      >
                        <option value="">未分层</option>
                        {Object.entries(FUNNEL_STAGE_LABEL).map(([value, label]) => (
                          <option key={value} value={value}>
                            {label}
                          </option>
                        ))}
                      </select>
                      <select
                        className="border border-[var(--border)] rounded px-2 py-1 text-xs outline-none bg-white"
                        value={item.status}
                        onChange={(e) => onUpdate(item, { status: e.target.value as CalendarStatus })}
                      >
                        {Object.entries(CALENDAR_STATUS_LABEL).map(([value, label]) => (
                          <option key={value} value={value}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>

                  <div className="flex gap-1 flex-shrink-0">
                    <Button size="xs" variant="outline" onClick={() => onGenerateContent(item)}>
                      <FileText />
                      生成内容
                    </Button>
                    <Button size="icon-xs" variant="destructive" aria-label="删除日历条目" onClick={() => onDelete(item)}>
                      <Trash2 />
                    </Button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ── Main Page ─────────────────────────────────────────────────────────── */

export default function TopicsPage() {
  const { activeGoalId, activeGoalName } = useGoalsStore();
  const router = useRouter();
  const qc = useQueryClient();
  const [count, setCount] = useState(5);
  const [topics, setTopics] = useState<GeneratedTopic[]>([]);
  const [aiError, setAiError] = useState<string | null>(null);
  const [calendarDraft, setCalendarDraft] = useState<CalendarDraft | null>(null);
  const [mutationError, setMutationError] = useState<string | null>(null);

  const calendarFrom = format(new Date(), "yyyy-MM-dd");
  const calendarTo = format(addDays(new Date(), 13), "yyyy-MM-dd");
  const topicsKey = ["topics", activeGoalId];
  const calendarKey = ["calendar", calendarFrom, calendarTo];

  const listTopics = () =>
    topicsApi.list({ goal_id: activeGoalId, page_size: 100, sort: "-updated_at" });

  const listCalendar = () =>
    calendarApi.list({ from: calendarFrom, to: calendarTo, include_deleted: false, page_size: 100 });

  const topicsQuery = useQuery({
    queryKey: topicsKey,
    queryFn: listTopics,
    enabled: !!activeGoalId,
  });

  const calendarQuery = useQuery({
    queryKey: calendarKey,
    queryFn: listCalendar,
    enabled: !!activeGoalId,
  });

  const topicLibrary = useMemo(() => topicsQuery.data?.items ?? [], [topicsQuery.data]);
  const calendarItems = useMemo(() => calendarQuery.data?.items ?? [], [calendarQuery.data]);
  const topicById = useMemo(
    () => new Map(topicLibrary.map((topic) => [topic.topic_id, topic])),
    [topicLibrary]
  );
  const existingTopicTitles = useMemo(() => new Set(topicLibrary.map((topic) => topic.title)), [topicLibrary]);

  async function reloadTopics() {
    await qc.invalidateQueries({ queryKey: topicsKey });
    return qc.fetchQuery({ queryKey: topicsKey, queryFn: listTopics });
  }

  async function reloadCalendar() {
    await qc.invalidateQueries({ queryKey: calendarKey });
    return qc.fetchQuery({ queryKey: calendarKey, queryFn: listCalendar });
  }

  async function updateTopicWithRetry(topic: ApiTopic, changes: TopicChanges) {
    try {
      return await topicsApi.update(topic.topic_id, { ...changes, rev: topic.rev });
    } catch (error) {
      if (isRevMismatch(error)) {
        await reloadTopics();
        return topicsApi.update(topic.topic_id, { ...changes, rev: error.current_rev });
      }
      throw error;
    }
  }

  async function deleteTopicWithRetry(topic: ApiTopic) {
    try {
      return await topicsApi.delete(topic.topic_id, topic.rev);
    } catch (error) {
      if (isRevMismatch(error)) {
        await reloadTopics();
        return topicsApi.delete(topic.topic_id, error.current_rev);
      }
      throw error;
    }
  }

  async function updateCalendarWithRetry(item: ApiCalendarItem, changes: CalendarChanges) {
    try {
      return await calendarApi.update(item.calendar_item_id, { ...changes, rev: item.rev });
    } catch (error) {
      if (isRevMismatch(error)) {
        await reloadCalendar();
        return calendarApi.update(item.calendar_item_id, { ...changes, rev: error.current_rev });
      }
      throw error;
    }
  }

  async function softDeleteCalendarWithRetry(item: ApiCalendarItem) {
    try {
      return await calendarApi.softDelete(item.calendar_item_id, item.rev);
    } catch (error) {
      if (isRevMismatch(error)) {
        await reloadCalendar();
        return calendarApi.softDelete(item.calendar_item_id, error.current_rev);
      }
      throw error;
    }
  }

  async function createTopicFromGenerated(topic: GeneratedTopic) {
    const existing = topicLibrary.find((storedTopic) => storedTopic.title === topic.title);
    if (existing) return existing;

    return topicsApi.create({
      title: topic.title,
      goal_id: activeGoalId,
      angle: topic.angle,
      source: "ai",
      source_refs: keywordRefs(topic),
    });
  }

  function handleWriteError(error: unknown) {
    setMutationError(errorMessage(error));
  }

  const genMut = useMutation({
    mutationFn: () =>
      apiFetch<TopicsResponse>("/api/v1/topics/generate", {
        method: "POST",
        body: JSON.stringify({ goal_id: activeGoalId, count }),
      }),
    onSuccess: (data) => {
      setTopics(data.topics);
      setAiError(data.error ?? null);
    },
  });

  const createTopicMut = useMutation({
    mutationFn: createTopicFromGenerated,
    onSuccess: () => {
      setMutationError(null);
      qc.invalidateQueries({ queryKey: topicsKey });
    },
    onError: handleWriteError,
  });

  const updateTopicMut = useMutation({
    mutationFn: ({ topic, changes }: { topic: ApiTopic; changes: TopicChanges }) =>
      updateTopicWithRetry(topic, changes),
    onSuccess: () => {
      setMutationError(null);
      qc.invalidateQueries({ queryKey: topicsKey });
    },
    onError: handleWriteError,
  });

  const deleteTopicMut = useMutation({
    mutationFn: deleteTopicWithRetry,
    onSuccess: () => {
      setMutationError(null);
      qc.invalidateQueries({ queryKey: topicsKey });
    },
    onError: handleWriteError,
  });

  const createCalendarMut = useMutation({
    mutationFn: (data: CalendarItemCreate) => calendarApi.create(data),
    onSuccess: () => {
      setMutationError(null);
      setCalendarDraft(null);
      qc.invalidateQueries({ queryKey: calendarKey });
      qc.invalidateQueries({ queryKey: topicsKey });
    },
    onError: handleWriteError,
  });

  const updateCalendarMut = useMutation({
    mutationFn: ({ item, changes }: { item: ApiCalendarItem; changes: CalendarChanges }) =>
      updateCalendarWithRetry(item, changes),
    onSuccess: () => {
      setMutationError(null);
      qc.invalidateQueries({ queryKey: calendarKey });
    },
    onError: handleWriteError,
  });

  const deleteCalendarMut = useMutation({
    mutationFn: softDeleteCalendarWithRetry,
    onSuccess: () => {
      setMutationError(null);
      qc.invalidateQueries({ queryKey: calendarKey });
    },
    onError: handleWriteError,
  });

  function openCalendarForTopic(topic: ApiTopic) {
    setCalendarDraft({
      topicId: topic.topic_id,
      scheduledDate: tomorrowDate(),
      scheduledTime: DEFAULT_SCHEDULED_TIME,
      funnelStage: topic.funnel_stage ?? "trust",
    });
  }

  function openCalendarForDate(date: string) {
    setCalendarDraft({
      scheduledDate: date,
      scheduledTime: DEFAULT_SCHEDULED_TIME,
      funnelStage: "trust",
    });
  }

  function goToContent(topic: ApiTopic) {
    const params = new URLSearchParams({ topicId: topic.topic_id });
    router.push(`/admin/content?${params.toString()}`);
  }

  function goToContentFromCalendar(item: ApiCalendarItem) {
    const params = new URLSearchParams({ calendarItemId: item.calendar_item_id });
    if (item.topic_id) params.set("topicId", item.topic_id);
    router.push(`/admin/content?${params.toString()}`);
  }

  function confirmDeleteTopic(topic: ApiTopic) {
    if (!window.confirm(`确认归档「${topic.title}」吗？`)) return;
    deleteTopicMut.mutate(topic);
  }

  function confirmDeleteCalendar(item: ApiCalendarItem) {
    const title = calendarTitle(item, topicById);
    if (!window.confirm(`确认从日历删除「${title}」吗？`)) return;
    deleteCalendarMut.mutate(item);
  }

  return (
    <div className="max-w-3xl mx-auto">
      <h1 className="text-xl font-bold text-[var(--text1)] mb-5">选题策划</h1>

      {/* AI Generation */}
      <div className="bg-white border border-[var(--border)] rounded-xl p-4 mb-5 flex items-center gap-3">
        <span className="text-xs text-[var(--text2)]">当前目标：</span>
        <span className="text-sm text-[var(--brand)]">{activeGoalName || activeGoalId || "未选择"}</span>
        <div className="flex items-center gap-2 ml-auto">
          <label className="text-xs text-[var(--text2)]">数量</label>
          <select
            className="border border-[var(--border)] rounded-lg px-2 py-1.5 text-sm outline-none bg-white"
            value={count}
            onChange={(e) => setCount(Number(e.target.value))}
          >
            {[3, 5, 8, 10].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
          <Button
            size="sm"
            style={{ background: "var(--brand)", color: "white" }}
            onClick={() => genMut.mutate()}
            disabled={!activeGoalId || genMut.isPending}
          >
            {genMut.isPending ? "生成中…" : "AI 生成选题"}
          </Button>
        </div>
      </div>

      {/* Errors */}
      {!activeGoalId && (
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 mb-4 text-sm text-amber-800">
          请先选择运营目标，再生成和管理选题。
        </div>
      )}
      {genMut.isError && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-4 text-sm text-red-700">
          请求失败，请检查后端是否启动
        </div>
      )}
      {(topicsQuery.isError || calendarQuery.isError) && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-4 text-sm text-red-700">
          列表加载失败，请检查后端接口或登录状态。
        </div>
      )}
      {mutationError && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-4 text-sm text-red-700">
          {mutationError}
        </div>
      )}
      {aiError && (
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 mb-4 text-sm text-amber-800">
          {aiError}
          <span className="ml-2 underline cursor-pointer" onClick={() => (window.location.href = "/admin/settings")}>
            前往配置 Kimi API Key
          </span>
        </div>
      )}

      {/* Generated Topics */}
      {topics.length > 0 && (
        <div className="mb-6">
          <div className="text-sm font-semibold text-[var(--text1)] mb-2">新生成选题</div>
          <div className="space-y-2">
            {topics.map((topic, i) => {
              const alreadySaved = existingTopicTitles.has(topic.title);
              return (
                <div
                  key={`${topic.title}-${i}`}
                  className="bg-white border border-[var(--border)] rounded-xl p-4 hover:border-[var(--brand)] transition-colors"
                >
                  <div className="flex items-start gap-3">
                    <span className="text-lg font-bold text-[var(--text3)] w-6 flex-shrink-0">{i + 1}</span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1.5">
                        <p className="text-sm font-semibold text-[var(--text1)]">{topic.title}</p>
                      </div>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span
                          className="text-[10px] px-2 py-0.5 rounded-full font-medium text-white"
                          style={{ background: ANGLE_COLOR[topic.angle] ?? "var(--brand)" }}
                        >
                          {topic.angle}
                        </span>
                        {topic.keywords.map((kw) => (
                          <span
                            key={kw}
                            className="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-[var(--text2)]"
                          >
                            {kw}
                          </span>
                        ))}
                      </div>
                    </div>
                    <div className="flex gap-1 flex-shrink-0">
                      <Button
                        size="xs"
                        variant="outline"
                        onClick={() => createTopicMut.mutate(topic)}
                        disabled={alreadySaved || createTopicMut.isPending}
                      >
                        <Save />
                        {alreadySaved ? "已保存" : "保存"}
                      </Button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Topic Library */}
      <div className="mb-6">
        <div className="text-sm font-semibold text-[var(--text1)] mb-2">
          我的选题库
          <span className="text-xs text-[var(--text3)] font-normal ml-2">{topicLibrary.length} 个</span>
        </div>
        {topicsQuery.isLoading ? (
          <div className="bg-white border border-[var(--border)] rounded-xl p-6 text-center text-xs text-[var(--text3)]">
            加载选题中…
          </div>
        ) : topicLibrary.length === 0 ? (
          <div className="bg-gray-50 border border-dashed border-[var(--border)] rounded-xl p-6 text-center">
            <p className="text-xs text-[var(--text3)]">生成选题后点击「保存」加入选题库</p>
          </div>
        ) : (
          <div className="space-y-2">
            {topicLibrary.map((topic) => (
              <div key={topic.topic_id} className="bg-white border border-[var(--border)] rounded-xl p-3">
                <div className="flex items-center gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-sm text-[var(--text1)] truncate">{topic.title}</span>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${TOPIC_STATUS_COLOR[topic.status]}`}>
                        {TOPIC_STATUS_LABEL[topic.status]}
                      </span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      {topic.angle && (
                        <span
                          className="text-[10px] px-1.5 py-0.5 rounded-full font-medium text-white"
                          style={{ background: ANGLE_COLOR[topic.angle] ?? "var(--brand)" }}
                        >
                          {topic.angle}
                        </span>
                      )}
                      {topic.funnel_stage && (
                        <span
                          className={`text-[10px] px-1.5 py-0.5 rounded-full border ${FUNNEL_STAGE_COLOR[topic.funnel_stage]}`}
                        >
                          {FUNNEL_STAGE_LABEL[topic.funnel_stage]}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 flex-shrink-0">
                    <Button size="xs" variant="outline" onClick={() => goToContent(topic)}>
                      <FileText />
                      生成内容
                    </Button>
                    <Button size="xs" variant="outline" onClick={() => openCalendarForTopic(topic)} disabled={!canScheduleTopic(topic)}>
                      <CalendarPlus />
                      加入日历
                    </Button>
                    <select
                      className="text-[10px] border border-[var(--border)] rounded px-1.5 py-1 outline-none bg-white"
                      value={topic.status}
                      onChange={(e) =>
                        updateTopicMut.mutate({
                          topic,
                          changes: { status: e.target.value as TopicStatus },
                        })
                      }
                    >
                      {Object.entries(TOPIC_STATUS_LABEL).map(([value, label]) => (
                        <option key={value} value={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                    <Button size="icon-xs" variant="ghost" aria-label="归档选题" onClick={() => confirmDeleteTopic(topic)}>
                      <Trash2 />
                    </Button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Calendar */}
      <MiniCalendar calendar={calendarItems} topicById={topicById} onAdd={openCalendarForDate} />
      <CalendarList
        items={calendarItems}
        topicById={topicById}
        onUpdate={(item, changes) => updateCalendarMut.mutate({ item, changes })}
        onDelete={confirmDeleteCalendar}
        onGenerateContent={goToContentFromCalendar}
      />

      {/* Calendar Modal */}
      {calendarDraft && (
        <CalendarCreateModal
          key={`${calendarDraft.topicId ?? "none"}-${calendarDraft.scheduledDate}`}
          draft={calendarDraft}
          topics={topicLibrary}
          onClose={() => setCalendarDraft(null)}
          onConfirm={(data) => createCalendarMut.mutate(data)}
          isPending={createCalendarMut.isPending}
        />
      )}

      {!genMut.isPending && topics.length === 0 && topicLibrary.length === 0 && (
        <div className="text-center py-16 text-[var(--text2)]">
          <p className="text-sm font-medium text-[var(--text1)] mb-1">AI 选题建议</p>
          <p className="text-xs">基于运营目标和关键词，生成差异化选题方向</p>
          <p className="text-xs mt-1 text-[var(--text3)]">
            五大公式：反直觉 / 数字清单 / 本地汇总 / 工具型 / 焦虑共鸣
          </p>
        </div>
      )}
    </div>
  );
}
