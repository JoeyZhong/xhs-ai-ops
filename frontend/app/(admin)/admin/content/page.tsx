"use client";

import { Suspense, useMemo, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import {
  ApiError,
  apiFetch,
  calendarApi,
  generateIdempotencyKey,
  strategiesApi,
  topicsApi,
  type CalendarItem,
  type ContentDraft,
  type ContentStrategy,
  type FunnelStage,
  type Topic,
} from "@/lib/api";
import { useGoalsStore } from "@/stores/goals";

interface ContentItem {
  id: string;
  content_id?: string;
  goal_id: string;
  title: string;
  alt_titles: string[];
  content: string;
  tags: string[];
  publish_time: string;
  publish_reason: string;
  angle: string;
  status: string;
  source: string;
  created_at: string;
  updated_at: string;
  edit_count: number;
}

type AngleStatus = "validated_hit" | "sunk" | "unknown";

interface UsedAngle {
  angle: string;
  status: AngleStatus;
  evidence_count?: number;
  last_ces?: number | null;
}

interface Goal {
  id: string;
  // 三态对象数组（新）或字符串数组（老）—— 用 angleStatusMap 容错读
  used_angles: Array<string | UsedAngle>;
  keywords: string[];
  content_calendar: Array<{ date: string; title: string; type: string }>;
}

/** 把 used_angles（老字符串数组 / 新三态对象数组）规整成 angle→{status,last_ces} 映射 */
function angleStatusMap(used: Array<string | UsedAngle> | undefined): Record<string, UsedAngle> {
  const map: Record<string, UsedAngle> = {};
  for (const u of used ?? []) {
    if (typeof u === "string") {
      if (u.trim()) map[u] = { angle: u, status: "unknown" };
    } else if (u && u.angle) {
      map[u.angle] = { ...u, status: normalizeAngleStatus(u.status) };
    }
  }
  return map;
}

function normalizeAngleStatus(status: unknown): AngleStatus {
  return status === "validated_hit" || status === "sunk" ? status : "unknown";
}

interface Strategy {
  angle: string;
  hook: string;
  key_points: string[];
  cta: string;
}

const ANGLES = ["反直觉型", "数字清单型", "本地汇总型", "工具型", "焦虑共鸣型"];
const COUNTS = [1, 3, 5];
const INPUT_CLS = "w-full border border-[var(--border)] rounded-lg px-3 py-2 text-sm outline-none focus:border-[var(--brand)] focus:ring-1 focus:ring-[var(--brand)] bg-white";

const FUNNEL_LABEL: Record<FunnelStage, string> = {
  traffic: "引流（漏斗上）",
  trust: "信任（漏斗中）",
  conversion: "转化（漏斗下）",
};

/* ── Backend payload normalization ─────────────────────────── */

type GeneratedDraftLike = Partial<ContentDraft> & Partial<ContentItem> & {
  id?: string;
  content_id?: string;
  title?: string | null;
  body?: string | null;
  content?: string | null;
  hashtags?: string[];
  tags?: string[];
  publish_at?: string | null;
  publish_time?: string;
  publish_reason?: string;
  alt_titles?: string[];
  goal_id?: string | null;
  angle?: string | null;
  status?: string;
  source?: string;
  created_at?: string;
  updated_at?: string;
  edit_count?: number;
};

function normalizeContentItem(raw: GeneratedDraftLike, fallbackGoalId: string): ContentItem {
  const contentId = raw.content_id ?? raw.id ?? "";
  return {
    id: contentId,
    content_id: contentId || undefined,
    goal_id: raw.goal_id ?? fallbackGoalId,
    title: raw.title ?? "",
    alt_titles: Array.isArray(raw.alt_titles) ? raw.alt_titles : [],
    content: raw.body ?? raw.content ?? "",
    tags: Array.isArray(raw.hashtags) ? raw.hashtags : Array.isArray(raw.tags) ? raw.tags : [],
    publish_time: raw.publish_at ?? raw.publish_time ?? "",
    publish_reason: raw.publish_reason ?? "",
    angle: raw.angle ?? "",
    status: raw.status ?? "draft",
    source: raw.source ?? "ai_generate",
    created_at: raw.created_at ?? "",
    updated_at: raw.updated_at ?? "",
    edit_count: raw.edit_count ?? 0,
  };
}

/* ── Markdown renderer ─────────────────────────────────── */

function renderMarkdown(raw: string): string {
  let html = raw.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/^### (.+)$/gm, '<h3 class="text-sm font-bold mt-3 mb-1">$1</h3>');
  html = html.replace(/^## (.+)$/gm, '<h2 class="text-base font-bold mt-4 mb-2">$1</h2>');
  html = html.replace(/^# (.+)$/gm, '<h1 class="text-lg font-bold mt-4 mb-2">$1</h1>');
  html = html.replace(/^- (.+)$/gm, '<li class="ml-4 list-disc">$1</li>');
  html = html.replace(/^\> (.+)$/gm, '<blockquote class="border-l-2 border-[var(--brand)] pl-3 italic text-[var(--text2)] my-2">$1</blockquote>');
  html = html.replace(/\n\n/g, "</p><p class='mb-2'>");
  html = html.replace(/^(.+)$/gm, (_m, p1) => p1.startsWith("<") || p1.startsWith("- ") ? p1 : `<p class="mb-2">${p1}</p>`);
  return html;
}

/* ── Content Card ─────────────────────────────────────────── */

function ContentCard({ item, angleInfo, onAddToCalendar, onSave }: {
  item: ContentItem; angleInfo?: UsedAngle;
  onAddToCalendar: (item: ContentItem) => void;
  onSave: (id: string, patch: Partial<ContentItem>) => Promise<void>;
}) {
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [preview, setPreview] = useState(true);
  const [draft, setDraft] = useState(item);
  const [tagInput, setTagInput] = useState("");

  async function saveEdit() {
    setEditing(false);
    const patch: Partial<ContentItem> = {};
    if (draft.title !== item.title) patch.title = draft.title;
    if (draft.content !== item.content) patch.content = draft.content;
    if (JSON.stringify(draft.tags) !== JSON.stringify(item.tags)) patch.tags = draft.tags;
    if (Object.keys(patch).length > 0) await onSave(item.id, patch);
  }

  function startEditing() {
    setDraft(item);
    setPreview(false);
    setEditing(true);
  }

  function cancelEditing() {
    setDraft(item);
    setEditing(false);
    setPreview(true);
  }

  function addTag() {
    const t = tagInput.trim();
    if (t && !draft.tags.includes(t)) {
      setDraft({ ...draft, tags: [...draft.tags, t] });
    }
    setTagInput("");
  }

  function removeTag(tag: string) {
    setDraft({ ...draft, tags: draft.tags.filter((t) => t !== tag) });
  }

  const draftHref = item.content_id ? `/admin/drafts?content_id=${encodeURIComponent(item.content_id)}` : null;

  return (
    <div className="bg-white border border-[var(--border)] rounded-xl overflow-hidden">
      <button className="w-full px-4 py-3 flex items-center gap-3 text-left hover:bg-gray-50 transition-colors" onClick={() => setExpanded(!expanded)}>
        <div className="flex-1 min-w-0">
          {editing ? (
            <input className="w-full border border-[var(--border)] rounded px-2 py-1 text-sm font-semibold outline-none focus:border-[var(--brand)]" value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
          ) : (
            <p className="text-sm font-semibold text-[var(--text1)] truncate">{item.title}</p>
          )}
          <div className="flex items-center gap-2 mt-0.5 flex-wrap">
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-[var(--brand)]/10 text-[var(--brand)]">{item.angle}</span>
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">{item.status}</span>
            {angleInfo?.status === "validated_hit" && (
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-green-100 text-green-700">
                ✅ 已验证爆款{angleInfo.last_ces != null ? `（CES ${angleInfo.last_ces}）` : ""}
              </span>
            )}
            {angleInfo?.status === "sunk" && (
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-red-100 text-red-700">
                ❌ 沉底{angleInfo.last_ces != null ? `（CES ${angleInfo.last_ces}）` : ""}
              </span>
            )}
            <span className="text-[10px] text-[var(--text3)]">{item.publish_time}</span>
          </div>
        </div>
        <span className="text-[var(--text3)] text-xs">{expanded ? "▲" : "▼"}</span>
      </button>
      {expanded && (
        <div className="px-4 pb-4 border-t border-[var(--border)]">
          <div className="flex items-center gap-2 mt-3 mb-2">
            {!editing ? (
              <>
                <Button size="xs" variant="outline" onClick={startEditing}>✎ 编辑</Button>
                <Button size="xs" variant="outline" onClick={() => onAddToCalendar(item)}>📅 加入日历</Button>
                {draftHref && (
                  <Link
                    href={draftHref}
                    className="text-xs px-2.5 py-1 rounded-lg border border-[var(--border)] text-[var(--text2)] hover:border-[var(--brand)] hover:text-[var(--brand)] transition-colors"
                  >
                    🗂 查看草稿
                  </Link>
                )}
              </>
            ) : (
              <>
                <Button size="xs" style={{ background: "var(--brand)", color: "white" }} onClick={saveEdit}>✓ 保存</Button>
                <Button size="xs" variant="outline" onClick={cancelEditing}>取消</Button>
                <Button size="xs" variant="ghost" onClick={() => setPreview(!preview)} className="ml-auto">{preview ? "预览" : "源码"}</Button>
              </>
            )}
          </div>
          {editing ? (
            preview ? (
              <div className="bg-gray-50 rounded-lg p-3 text-sm text-[var(--text1)] leading-relaxed max-h-72 overflow-y-auto" dangerouslySetInnerHTML={{ __html: renderMarkdown(draft.content) }} />
            ) : (
              <textarea className={`${INPUT_CLS} h-48 font-mono text-xs`} value={draft.content} onChange={(e) => setDraft({ ...draft, content: e.target.value })} />
            )
          ) : (
            <div className="bg-gray-50 rounded-lg p-3 text-sm text-[var(--text1)] leading-relaxed max-h-72 overflow-y-auto" dangerouslySetInnerHTML={{ __html: renderMarkdown(item.content) }} />
          )}
          {editing ? (
            <div className="mt-2 space-y-1">
              <div className="flex flex-wrap gap-1">
                {draft.tags.map((tag) => (
                  <span key={tag} className="text-[10px] px-2 py-0.5 rounded-full bg-[var(--brand)]/10 text-[var(--brand)] flex items-center gap-1">
                    #{tag}
                    <button onClick={() => removeTag(tag)} className="hover:font-bold">×</button>
                  </span>
                ))}
              </div>
              <div className="flex gap-1">
                <input className="w-full border border-[var(--border)] rounded px-2 py-1 text-xs outline-none focus:border-[var(--brand)]" placeholder="添加标签" value={tagInput} onChange={(e) => setTagInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && addTag()} />
                <Button size="xs" variant="outline" onClick={addTag}>添加</Button>
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap gap-1 mt-2">
              {item.tags.map((tag) => (
                <span key={tag} className="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-[var(--text2)]">#{tag}</span>
              ))}
            </div>
          )}
          <div className="flex gap-2 mt-3">
            <Button size="sm" variant="outline" onClick={() => navigator.clipboard.writeText(item.content)}>复制正文</Button>
            <Button size="sm" variant="outline" onClick={() => { const text = `${item.title}\n\n${item.content}\n\n${item.tags.map(t => "#" + t).join(" ")}`; navigator.clipboard.writeText(text); }}>复制全文</Button>
          </div>
        </div>
      )}
    </div>
  );
}

/* ── Calendar Modal ──────────────────────────────────────── */

function CalendarModal({ item, onClose, onConfirm }: {
  item: ContentItem | null;
  onClose: () => void;
  onConfirm: (date: string, type: "top-funnel" | "trust-building" | "conversion") => void;
}) {
  const [date, setDate] = useState(() => { const d = new Date(); d.setDate(d.getDate() + 1); return d.toISOString().slice(0, 10); });
  const [type, setType] = useState<"top-funnel" | "trust-building" | "conversion">("trust-building");
  if (!item) return null;
  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-white rounded-xl p-5 w-80 shadow-lg" onClick={(e) => e.stopPropagation()}>
        <div className="text-sm font-semibold text-[var(--text1)] mb-3">加入内容日历</div>
        <div className="space-y-3">
          <div><label className="block text-xs text-[var(--text2)] mb-1">发布日期</label><input type="date" className={INPUT_CLS} value={date} onChange={e => setDate(e.target.value)} /></div>
          <div><label className="block text-xs text-[var(--text2)] mb-1">内容类型</label>
            <select className={INPUT_CLS} value={type} onChange={e => setType(e.target.value as typeof type)}>
              <option value="top-funnel">引流层（30%）</option>
              <option value="trust-building">信任层（40%）</option>
              <option value="conversion">转化层（30%）</option>
            </select>
          </div>
          <div className="text-xs text-[var(--text3)] line-clamp-2">{item.title}</div>
          <div className="flex gap-2 pt-1">
            <Button size="sm" variant="outline" className="flex-1" onClick={onClose}>取消</Button>
            <Button size="sm" className="flex-1" style={{ background: "var(--brand)", color: "white" }} onClick={() => onConfirm(date, type)}>确认</Button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── Lifecycle Context Banner ────────────────────────────── */

function LifecycleBanner({ topic, calendarItem, strategy }: {
  topic: Topic | undefined;
  calendarItem: CalendarItem | undefined;
  strategy: ContentStrategy | undefined;
}) {
  if (!topic && !calendarItem && !strategy) return null;
  return (
    <div className="bg-[var(--brand)]/5 border border-[var(--brand)]/30 rounded-xl p-3 space-y-2 mb-3">
      <div className="text-[11px] font-semibold text-[var(--brand)] uppercase tracking-wide">生命周期上下文</div>
      {topic && (
        <div className="text-xs text-[var(--text1)]">
          <span className="text-[var(--text3)] mr-1">📌 选题</span>
          <span className="font-medium">{topic.title}</span>
          {topic.angle && <span className="ml-2 text-[var(--text2)]">· {topic.angle}</span>}
          {topic.funnel_stage && (
            <span className="ml-2 text-[10px] px-2 py-0.5 rounded-full bg-white border border-[var(--border)] text-[var(--text2)]">
              {FUNNEL_LABEL[topic.funnel_stage]}
            </span>
          )}
        </div>
      )}
      {calendarItem && (
        <div className="text-xs text-[var(--text1)]">
          <span className="text-[var(--text3)] mr-1">📅 日历</span>
          <span className="font-medium">{calendarItem.scheduled_date}</span>
          {calendarItem.scheduled_time && <span className="ml-1 text-[var(--text2)]">{calendarItem.scheduled_time}</span>}
          {calendarItem.funnel_stage && (
            <span className="ml-2 text-[10px] px-2 py-0.5 rounded-full bg-white border border-[var(--border)] text-[var(--text2)]">
              {FUNNEL_LABEL[calendarItem.funnel_stage]}
            </span>
          )}
          <span className="ml-2 text-[10px] px-2 py-0.5 rounded-full bg-white border border-[var(--border)] text-[var(--text2)]">
            {calendarItem.status}
          </span>
        </div>
      )}
      {strategy && (
        <div className="text-xs text-[var(--text1)]">
          <span className="text-[var(--text3)] mr-1">🎯 策略</span>
          {strategy.angle && <span className="font-medium">{strategy.angle}</span>}
          {strategy.hook && <span className="ml-2 text-[var(--text2)] line-clamp-1">「{strategy.hook}」</span>}
        </div>
      )}
    </div>
  );
}

/* ── Step 1 Card ──────────────────────────────────────────── */

function Step1Card({ goal, keywords, setKeywords, userIntent, setUserIntent, onGenerate }: {
  goal: Goal | undefined; keywords: string[]; setKeywords: (k: string[]) => void;
  userIntent: string; setUserIntent: (v: string) => void;
  onGenerate: () => void;
}) {
  const [typed, setTyped] = useState("");
  const available = goal?.keywords ?? [];

  function toggleKw(kw: string) {
    setKeywords(keywords.includes(kw) ? keywords.filter(k => k !== kw) : [...keywords, kw]);
  }
  function addTyped() {
    const t = typed.trim();
    if (t && !keywords.includes(t)) setKeywords([...keywords, t]);
    setTyped("");
  }

  return (
    <div className="bg-white border border-[var(--border)] rounded-xl p-4 space-y-3">
      <h2 className="text-sm font-bold text-[var(--text1)]">Step 1 · 生成内容策略</h2>
      {/* Keyword chips */}
      <div>
        <label className="block text-xs font-medium text-[var(--text2)] mb-1">关键词（选择或输入）</label>
        <div className="flex flex-wrap gap-1.5 mb-2">
          {available.map(kw => (
            <button key={kw} onClick={() => toggleKw(kw)}
              className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${keywords.includes(kw) ? "bg-[var(--brand)] text-white border-[var(--brand)]" : "bg-white text-[var(--text2)] border-[var(--border)] hover:border-[var(--brand)]"}`}>
              {kw}
            </button>
          ))}
        </div>
        <div className="flex gap-1">
          <input className={`${INPUT_CLS} flex-1`} placeholder="输入自定义关键词" value={typed} onChange={e => setTyped(e.target.value)} onKeyDown={e => e.key === "Enter" && addTyped()} />
          <Button size="xs" variant="outline" onClick={addTyped}>添加</Button>
        </div>
        {keywords.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1.5">
            {keywords.map(kw => (
              <span key={kw} className="text-[10px] px-2 py-0.5 rounded-full bg-[var(--brand)]/10 text-[var(--brand)] flex items-center gap-1">
                {kw}
                <button onClick={() => setKeywords(keywords.filter(k => k !== kw))} className="hover:font-bold">×</button>
              </span>
            ))}
          </div>
        )}
      </div>
      {/* User intent */}
      <div>
        <label className="block text-xs font-medium text-[var(--text2)] mb-1">用户意图 / 内容方向</label>
        <textarea className={`${INPUT_CLS} h-16 resize-none`} placeholder="例：吸引深圳工厂物业主动联系，展示自助机点位合作价值" value={userIntent} onChange={e => setUserIntent(e.target.value)} />
      </div>
      <Button size="sm" style={{ background: "var(--brand)", color: "white" }} onClick={onGenerate}>生成策略</Button>
    </div>
  );
}

/* ── Step 2 Card ──────────────────────────────────────────── */

function Step2Card({ strategy, setStrategy, count, setCount, onRegenerate, onConfirm }: {
  strategy: Strategy; setStrategy: (s: Strategy) => void;
  count: number; setCount: (n: number) => void;
  onRegenerate: () => void; onConfirm: () => void;
}) {
  const [kpInput, setKpInput] = useState("");

  function addKp() {
    const t = kpInput.trim();
    if (t) { setStrategy({ ...strategy, key_points: [...strategy.key_points, t] }); setKpInput(""); }
  }

  return (
    <div className="bg-white border border-[var(--border)] rounded-xl p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-bold text-[var(--text1)]">Step 2 · 调整策略并确认</h2>
        <Button size="xs" variant="ghost" onClick={onRegenerate}>🔄 重新生成</Button>
      </div>
      <div>
        <label className="block text-xs font-medium text-[var(--text2)] mb-1">角度</label>
        <select className={INPUT_CLS} value={strategy.angle} onChange={e => setStrategy({ ...strategy, angle: e.target.value })}>
          {ANGLES.map(a => <option key={a} value={a}>{a}</option>)}
        </select>
      </div>
      <div>
        <label className="block text-xs font-medium text-[var(--text2)] mb-1">Hook（开头）</label>
        <input className={INPUT_CLS} value={strategy.hook} onChange={e => setStrategy({ ...strategy, hook: e.target.value })} />
      </div>
      <div>
        <label className="block text-xs font-medium text-[var(--text2)] mb-1">Key Points</label>
        <ul className="space-y-1 mb-1">
          {(strategy.key_points ?? []).map((kp, i) => (
            <li key={i} className="flex items-center gap-1 text-xs text-[var(--text1)]">
              <span className="flex-1">• {kp}</span>
              <button onClick={() => setStrategy({ ...strategy, key_points: strategy.key_points.filter((_, j) => j !== i) })} className="text-red-400 hover:font-bold">×</button>
            </li>
          ))}
        </ul>
        <div className="flex gap-1">
          <input className={`${INPUT_CLS} flex-1`} placeholder="添加要点" value={kpInput} onChange={e => setKpInput(e.target.value)} onKeyDown={e => e.key === "Enter" && addKp()} />
          <Button size="xs" variant="outline" onClick={addKp}>添加</Button>
        </div>
      </div>
      <div>
        <label className="block text-xs font-medium text-[var(--text2)] mb-1">CTA（结尾引导）</label>
        <input className={INPUT_CLS} value={strategy.cta} onChange={e => setStrategy({ ...strategy, cta: e.target.value })} />
      </div>
      <div className="flex items-center gap-3">
        <div className="w-24">
          <label className="block text-xs font-medium text-[var(--text2)] mb-1">生成数量</label>
          <select className={INPUT_CLS} value={count} onChange={e => setCount(Number(e.target.value))}>
            {COUNTS.map(n => <option key={n} value={n}>{n} 篇</option>)}
          </select>
        </div>
        <Button size="sm" style={{ background: "var(--brand)", color: "white" }} onClick={onConfirm} className="mt-5">
          确认并生成内容
        </Button>
      </div>
    </div>
  );
}

/* ── Main Page ─────────────────────────────────────────────── */

function ContentPageInner() {
  const { activeGoalId } = useGoalsStore();
  const searchParams = useSearchParams();
  const qc = useQueryClient();

  const topicIdParam = searchParams.get("topicId");
  const calendarItemIdParam = searchParams.get("calendarItemId");
  const strategyIdParam = searchParams.get("strategyId");

  // Step 1 state
  const [keywords, setKeywords] = useState<string[]>([]);
  const [userIntent, setUserIntent] = useState(searchParams.get("intent") ?? searchParams.get("topic") ?? "");

  // Step 2 state
  const [strategy, setStrategy] = useState<Strategy | null>(null);
  const [count, setCount] = useState(3);

  // Step 3 state
  const [items, setItems] = useState<ContentItem[]>([]);
  const [generating, setGenerating] = useState(false);
  const [genErrors, setGenErrors] = useState<string[]>([]);
  const [aiError, setAiError] = useState<string | null>(null);

  // Misc
  const [calendarItem, setCalendarItem] = useState<ContentItem | null>(null);

  const { data: goal, isLoading: goalLoading } = useQuery<Goal>({
    queryKey: ["goal", activeGoalId],
    queryFn: () => apiFetch<Goal>(`/api/v1/goals/${activeGoalId}`),
    enabled: !!activeGoalId,
  });

  const angleMap = angleStatusMap(goal?.used_angles);

  // ── Lifecycle context queries ──────────────────────────────

  const topicQuery = useQuery<Topic | null>({
    queryKey: ["lifecycle-topic", topicIdParam],
    queryFn: () => topicsApi.get(topicIdParam!),
    enabled: !!topicIdParam,
  });

  const calendarItemQuery = useQuery<CalendarItem | null>({
    queryKey: ["lifecycle-calendar-item", calendarItemIdParam],
    queryFn: async () => {
      if (!calendarItemIdParam) return null;
      try {
        return await calendarApi.get(calendarItemIdParam);
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) {
          const page = await calendarApi.list({ page_size: 200, include_deleted: true });
          return page.items.find((it) => it.calendar_item_id === calendarItemIdParam) ?? null;
        }
        throw err;
      }
    },
    enabled: !!calendarItemIdParam,
  });

  const strategyQuery = useQuery<ContentStrategy | null>({
    queryKey: ["lifecycle-strategy", strategyIdParam],
    queryFn: () => strategiesApi.get(strategyIdParam!),
    enabled: !!strategyIdParam,
  });

  const topicData = topicQuery.data ?? undefined;
  const calendarItemData = calendarItemQuery.data ?? undefined;
  const strategyData = strategyQuery.data ?? undefined;

  // Derive the seed text from topic; user typing into the textarea overrides it.
  const topicSeed = useMemo(() => {
    if (!topicData) return "";
    const angle = topicData.angle ? `（角度：${topicData.angle}）` : "";
    return `${topicData.title}${angle}`;
  }, [topicData]);
  const effectiveUserIntent = userIntent || topicSeed;

  // Derive a strategy snapshot from the server strategy. Once user edits via Step2,
  // setStrategy will be called with the merged snapshot and local state takes over.
  const strategySeed = useMemo<Strategy | null>(() => {
    if (!strategyData) return null;
    const kps = Array.isArray(strategyData.key_points)
      ? (strategyData.key_points as unknown[]).map((kp) => (typeof kp === "string" ? kp : JSON.stringify(kp)))
      : [];
    return {
      angle: strategyData.angle ?? "",
      hook: strategyData.hook ?? "",
      key_points: kps,
      cta: strategyData.cta ?? "",
    };
  }, [strategyData]);
  const effectiveStrategy = strategy ?? strategySeed;

  // Resolved lifecycle ids for the generate request body
  const resolvedTopicId = useMemo(() => {
    return (
      topicIdParam ??
      topicData?.topic_id ??
      calendarItemData?.topic_id ??
      strategyData?.topic_id ??
      null
    );
  }, [topicIdParam, topicData, calendarItemData, strategyData]);

  // ── Step 1: generate strategy ──────────────────────────────

  const strategyMut = useMutation({
    mutationFn: () => apiFetch<{ strategy: Strategy; error?: string }>("/api/v1/content/strategy", {
      method: "POST",
      headers: { "Idempotency-Key": generateIdempotencyKey() },
      body: JSON.stringify({
        goal_id: activeGoalId,
        keywords,
        user_intent: effectiveUserIntent,
        topic_id: resolvedTopicId,
      }),
    }),
    onError: (err) => {
      setAiError(err instanceof Error ? err.message : "策略生成请求失败");
    },
    onSuccess: (data) => {
      const raw = data.strategy;
      if (raw && typeof raw === "object") {
        // Unwrap nested {description, strategy} shape from Kimi
        const rawAny = raw as unknown as Record<string, unknown>;
        const unwrapped =
          rawAny.angle === undefined && typeof rawAny.strategy === "object"
            ? rawAny.strategy as Record<string, unknown>
            : rawAny;
        setStrategy({
          angle: typeof unwrapped.angle === "string" ? unwrapped.angle : "",
          hook: typeof unwrapped.hook === "string" ? unwrapped.hook : "",
          key_points: Array.isArray(unwrapped.key_points) ? unwrapped.key_points as string[] : [],
          cta: typeof unwrapped.cta === "string" ? unwrapped.cta : "",
        });
      }
      setAiError(data.error ?? null);
    },
  });

  // ── Step 3: concurrent generate ────────────────────────────

  async function handleConfirm() {
    if (!effectiveStrategy || generating) return;
    setGenerating(true);
    setItems([]);
    setGenErrors([]);
    setAiError(null);

    const requests = Array.from({ length: count }, (_, i) =>
      apiFetch<{ items: GeneratedDraftLike[]; error?: string }>("/api/v1/content/generate", {
        method: "POST",
        headers: { "Idempotency-Key": generateIdempotencyKey() },
        body: JSON.stringify({
          goal_id: activeGoalId,
          topic: effectiveUserIntent,
          strategy: effectiveStrategy,
          count: 1,
          persist: true,
          topic_id: resolvedTopicId,
          strategy_id: strategyIdParam,
          calendar_item_id: calendarItemIdParam,
        }),
      }).then(r => ({ index: i, result: r }))
        .catch(err => ({ index: i, error: err as Error }))
    );

    for (const p of requests) {
      const settled = await p;
      if ("error" in settled) {
        setGenErrors(prev => [...prev, `#${settled.index + 1}: ${settled.error.message}`]);
      } else {
        const normalized = settled.result.items.map((it) => normalizeContentItem(it, activeGoalId ?? ""));
        setItems(prev => [...prev, ...normalized]);
        if (settled.result.error) {
          setAiError(settled.result.error);
        }
      }
    }
    setGenerating(false);
  }

  // ── Calendar + Save ────────────────────────────────────────

  const saveCalendarMut = useMutation({
    mutationFn: (calendar: Goal["content_calendar"]) =>
      apiFetch<Goal>(`/api/v1/goals/${activeGoalId}`, { method: "PUT", body: JSON.stringify({ content_calendar: calendar }) }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["goal", activeGoalId] }); setCalendarItem(null); },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: Partial<ContentItem> }) => {
      // Map UI field names back to backend ContentUpdateRequest schema
      const apiPatch: Record<string, unknown> = {};
      if (patch.title !== undefined) apiPatch.title = patch.title;
      if (patch.content !== undefined) apiPatch.body = patch.content;
      if (patch.tags !== undefined) apiPatch.hashtags = patch.tags;
      if (patch.publish_time !== undefined) apiPatch.publish_at = patch.publish_time;
      if (patch.angle !== undefined) apiPatch.angle = patch.angle;
      if (patch.status !== undefined) apiPatch.status = patch.status;
      return apiFetch<GeneratedDraftLike>(`/api/v1/content/${id}`, {
        method: "PUT",
        headers: { "Idempotency-Key": generateIdempotencyKey() },
        body: JSON.stringify(apiPatch),
      });
    },
  });

  async function handleSave(id: string, patch: Partial<ContentItem>) {
    try {
      const updated = await updateMutation.mutateAsync({ id, patch });
      const normalized = normalizeContentItem(updated, activeGoalId ?? "");
      setItems(prev => prev.map(i => i.id === id ? { ...i, ...normalized } : i));
    } catch (err) {
      alert(`保存失败：${err instanceof Error ? err.message : "未知错误"}`);
    }
  }

  function confirmCalendar(date: string, type: "top-funnel" | "trust-building" | "conversion") {
    if (!calendarItem) return;
    const existing = goal?.content_calendar ?? [];
    saveCalendarMut.mutate([...existing, { date, title: calendarItem.title, type }]);
  }

  const lifecycleLoading =
    (topicIdParam && topicQuery.isLoading) ||
    (calendarItemIdParam && calendarItemQuery.isLoading) ||
    (strategyIdParam && strategyQuery.isLoading);

  return (
    <div className="max-w-2xl mx-auto">
      <h1 className="text-xl font-bold text-[var(--text1)] mb-5">内容创作</h1>

      {/* Loading */}
      {goalLoading && <div className="text-center py-8 text-sm text-[var(--text2)]">加载目标信息…</div>}

      {/* Lifecycle context */}
      {lifecycleLoading && (
        <div className="bg-white border border-[var(--border)] rounded-xl p-3 mb-3 text-xs text-[var(--text2)]">加载生命周期上下文…</div>
      )}
      <LifecycleBanner topic={topicData} calendarItem={calendarItemData} strategy={strategyData} />

      {/* Step 1: Generate strategy */}
      <div className="space-y-4">
        <Step1Card
          goal={goal}
          keywords={keywords}
          setKeywords={setKeywords}
          userIntent={effectiveUserIntent}
          setUserIntent={setUserIntent}
          onGenerate={() => strategyMut.mutate()}
        />

        {/* Strategy loading */}
        {strategyMut.isPending && (
          <div className="bg-white border border-[var(--border)] rounded-xl p-4 text-center text-sm text-[var(--text2)]">
            AI 正在生成策略…
          </div>
        )}

        {/* Step 2: Review strategy */}
        {effectiveStrategy && !strategyMut.isPending && (
          <Step2Card
            strategy={effectiveStrategy}
            setStrategy={setStrategy}
            count={count}
            setCount={setCount}
            onRegenerate={() => strategyMut.mutate()}
            onConfirm={handleConfirm}
          />
        )}

        {/* Generating */}
        {generating && (
          <div className="bg-white border border-[var(--border)] rounded-xl p-4">
            <p className="text-sm text-[var(--text2)] mb-2">正在生成内容…（每篇依次生成）</p>
            <div className="w-full bg-gray-100 rounded-full h-1.5">
              <div className="bg-[var(--brand)] h-1.5 rounded-full transition-all" style={{ width: `${(items.length / count) * 100}%` }} />
            </div>
            <p className="text-xs text-[var(--text3)] mt-1">{items.length}/{count} 篇完成</p>
          </div>
        )}

        {/* Step 3: Results */}
        {items.length > 0 && (
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-[var(--text1)]">生成结果（{items.length}/{count}）</h3>
              <Link
                href="/admin/drafts"
                className="text-xs text-[var(--brand)] hover:underline"
              >
                打开草稿箱 →
              </Link>
            </div>
            {items.map(item => (
              <ContentCard key={item.id} item={item} angleInfo={angleMap[item.angle]}
                onAddToCalendar={setCalendarItem} onSave={handleSave} />
            ))}
          </div>
        )}

        {/* Retry buttons for failed items */}
        {genErrors.length > 0 && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700">
            <p className="font-semibold mb-1">{genErrors.length} 篇生成失败</p>
            {genErrors.map((err, i) => (
              <p key={i} className="text-xs mb-1">{err}</p>
            ))}
          </div>
        )}

        {/* AI errors */}
        {aiError && (
          <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-sm text-amber-800">
            ⚠️ {aiError}
          </div>
        )}
      </div>

      {/* Calendar Modal */}
      <CalendarModal item={calendarItem} onClose={() => setCalendarItem(null)} onConfirm={confirmCalendar} />

      {/* Empty state */}
      {!goalLoading && !effectiveStrategy && !strategyMut.isPending && (
        <div className="text-center py-16 text-[var(--text2)]">
          <div className="text-4xl mb-3">✍️</div>
          <p className="text-sm font-medium text-[var(--text1)] mb-1">两步内容创作</p>
          <p className="text-xs">Step 1: 选择关键词并生成策略 → Step 2: 确认策略并批量生成</p>
        </div>
      )}
    </div>
  );
}

export default function ContentPage() {
  return (
    <Suspense fallback={<div className="py-16 text-center text-sm text-[var(--text2)]">加载中…</div>}>
      <ContentPageInner />
    </Suspense>
  );
}
