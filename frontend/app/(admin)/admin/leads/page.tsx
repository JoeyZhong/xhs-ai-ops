"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Radar,
  ExternalLink,
  Copy,
  Pencil,
  X,
  Check,
  Clock,
  ShieldCheck,
  ShieldAlert,
  MessageSquareReply,
  Handshake,
  Inbox,
  Send,
  Eye,
  Loader2,
  Ban,
  Briefcase,
  RefreshCw,
  Search,
} from "lucide-react";
import {
  ApiError,
  leadsApi,
  type Lead,
  type LeadStatus,
  type LeadOutcome,
  type LeadSource,
  type TriggerType,
  type SendResult,
  type ScanResponse,
} from "@/lib/api";
import { useGoalsStore } from "@/stores/goals";

/* ── 触发场景标签 + 配色 ─────────────────────────────────────── */

const TRIGGER_LABEL: Record<TriggerType, string> = {
  loan: "贷款",
  bid: "投标",
  hitech: "高新",
  foreign: "外资",
  cancel: "注销",
};

const TRIGGER_COLOR: Record<TriggerType, string> = {
  loan: "bg-amber-50 text-amber-700",
  bid: "bg-blue-50 text-blue-600",
  hitech: "bg-green-50 text-green-600",
  foreign: "bg-pink-50 text-pink-600",
  cancel: "bg-orange-50 text-orange-600",
};

/* ── 信源元数据（字符徽标 + 文体 + 触达文案）────────────────── */

const SOURCE_META: Record<LeadSource, {
  label: string; char: string; bg: string;
  noun: string;        // 原帖/问题/需求单
  openVerb: string;    // 打开原帖/打开问题/打开需求单
  draftFmt: string;    // 草稿文体
  autoSend: boolean;   // 是否支持一键发送（仅小红书）
}> = {
  xhs:      { label: "小红书", char: "红", bg: "var(--brand)",
              noun: "帖", openVerb: "打开原帖", draftFmt: "小红书短回复", autoSend: true },
  zhihu:    { label: "知乎", char: "知", bg: "#3b82f6",
              noun: "问题", openVerb: "打开问题", draftFmt: "知乎专业回答", autoSend: false },
  zhubajie: { label: "猪八戒", char: "猪", bg: "#c98a1a",
              noun: "需求单", openVerb: "打开需求单", draftFmt: "接单报价话术", autoSend: false },
};

function srcKey(lead: Lead): LeadSource {
  return (lead.source in SOURCE_META ? lead.source : "xhs") as LeadSource;
}

function SrcBadge({ source, size = 17 }: { source: LeadSource; size?: number }) {
  const m = SOURCE_META[source];
  return (
    <span
      title={m.label}
      style={{ width: size, height: size, background: m.bg, fontSize: size * 0.62 }}
      className="inline-flex flex-none items-center justify-center rounded-[5px] font-bold leading-none text-white"
    >
      {m.char}
    </span>
  );
}

/* ── 猪八戒结构化 meta 读取 ──────────────────────────────────── */

function zbjMeta(lead: Lead): { budget?: string; delivery?: string; taken?: boolean } | null {
  if (srcKey(lead) !== "zhubajie" || !lead.meta) return null;
  const m = lead.meta as Record<string, unknown>;
  const out: { budget?: string; delivery?: string; taken?: boolean } = {};
  if (typeof m.budget === "string") out.budget = m.budget;
  if (typeof m.delivery === "string") out.delivery = m.delivery;
  if (typeof m.taken === "boolean") out.taken = m.taken;
  return Object.keys(out).length ? out : null;
}

type Tab = "pending" | "touched" | "skipped";
type SrcFilter = "all" | LeadSource;

const TAB_LABEL: Record<Tab, string> = {
  pending: "待处理",
  touched: "已触达",
  skipped: "跳过",
};

const PENDING_STATUSES: LeadStatus[] = ["detected", "qualified", "drafted", "pending"];

/* ── Helpers ─────────────────────────────────────────────────── */

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "操作失败";
}

function isRevMismatch(error: unknown): error is ApiError & { current_rev: number } {
  return error instanceof ApiError && error.code === "rev_mismatch" && typeof error.current_rev === "number";
}

function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const mins = Math.floor((Date.now() - t) / 60000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins}分钟前`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}小时前`;
  return `${Math.floor(hrs / 24)}天前`;
}

function matchClass(score: number | null): string {
  if (score == null) return "text-[var(--text3)]";
  if (score >= 80) return "text-[var(--text1)] font-semibold";
  if (score >= 60) return "text-[var(--text2)] font-semibold";
  return "text-[var(--text3)]";
}

/* ── Stats strip（Delta5：今日合格按源小分段）────────────────── */

function StatsStrip({ goalId, todayBySource }: {
  goalId: string | null;
  todayBySource: Record<LeadSource, number>;
}) {
  const { data } = useQuery({
    queryKey: ["leads-stats", goalId],
    queryFn: () => leadsApi.stats(goalId),
    refetchInterval: 60_000,
  });
  const sub = todayBySource;
  const hasSub = sub.xhs + sub.zhihu + sub.zhubajie > 0;
  return (
    <div className="flex gap-8 px-1 py-3 mb-3 border-b border-[var(--border)]">
      <div className="flex flex-col">
        <span className="text-lg font-semibold text-[var(--text1)]">{data?.today_qualified ?? "—"}</span>
        <span className="text-[10px] text-[var(--text3)]">今日合格线索</span>
        {hasSub && (
          <span className="flex gap-2 mt-0.5">
            {(["xhs", "zhihu", "zhubajie"] as LeadSource[]).filter((s) => sub[s] > 0).map((s) => (
              <span key={s} className="inline-flex items-center gap-1 text-[10px] text-[var(--text3)]">
                <i style={{ width: 6, height: 6, background: SOURCE_META[s].bg }} className="inline-block rounded-full" />
                {SOURCE_META[s].label} {sub[s]}
              </span>
            ))}
          </span>
        )}
      </div>
      {[
        { v: data?.pending ?? "—", k: "待处理" },
        { v: data?.week_opportunities ?? "—", k: "本周沟通机会 · 北极星", star: true },
        { v: data?.week_conversions ?? "—", k: "本周成交" },
      ].map((c) => (
        <div key={c.k} className="flex flex-col">
          <span className={`text-lg font-semibold ${c.star ? "text-[var(--brand)]" : "text-[var(--text1)]"}`}>{c.v}</span>
          <span className="text-[10px] text-[var(--text3)]">{c.k}</span>
        </div>
      ))}
    </div>
  );
}

/* ── Queue item ──────────────────────────────────────────────── */

function QueueItem({ lead, selected, onClick }: {
  lead: Lead;
  selected: boolean;
  onClick: () => void;
}) {
  const source = srcKey(lead);
  const meta = zbjMeta(lead);
  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-full text-left px-4 py-3 border-b border-[var(--border)] border-l-2 transition-colors ${
        selected ? "bg-white border-l-[var(--brand)]" : "border-l-transparent hover:bg-black/[0.02]"
      }`}
    >
      <div className="flex items-center gap-2 mb-1.5">
        <SrcBadge source={source} />
        {lead.trigger_type && (
          <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-md ${TRIGGER_COLOR[lead.trigger_type]}`}>
            {TRIGGER_LABEL[lead.trigger_type]}
          </span>
        )}
        <span className={`text-xs ${matchClass(lead.match_score)}`}>匹配 {lead.match_score ?? "—"}%</span>
        <span className="ml-auto flex items-center gap-1 text-[11px] text-[var(--text3)]">
          <Clock size={12} />
          {relativeTime(lead.detected_at)}
        </span>
      </div>
      <div className="text-[12.5px] text-[var(--text2)] line-clamp-2">
        {lead.excerpt || lead.post_text || "（无内容）"}
      </div>
      {meta && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {meta.budget && (
            <span className="inline-flex items-center gap-1 text-[11px] text-amber-700 bg-amber-50 rounded-md px-1.5 py-0.5">
              <Briefcase size={11} /> 预算 {meta.budget}
            </span>
          )}
          {meta.delivery && (
            <span className="inline-flex items-center gap-1 text-[11px] text-amber-700 bg-amber-50 rounded-md px-1.5 py-0.5">
              <Clock size={11} /> 交付 {meta.delivery}
            </span>
          )}
        </div>
      )}
      <div className="mt-1.5 text-[11px] text-[var(--text3)]">
        {SOURCE_META[source].label} · {lead.author || "匿名"}
      </div>
    </button>
  );
}

/* ── 一键发送状态条（Delta4）──────────────────────────────────── */

function SendBar({ result, count, limit }: { result: SendResult | null; count: number | null; limit: number | null }) {
  const showCount = count != null && limit != null;
  let chip: { cls: string; icon: React.ReactNode; text: string } | null = null;
  if (result) {
    if (result.status === "dryrun")
      chip = { cls: "text-amber-700 bg-amber-50", icon: <Eye size={12} />, text: "演练 · 不会真实发出" };
    else if (result.status === "sent")
      chip = { cls: "text-green-600 bg-green-50", icon: <Check size={12} />, text: result.platform_id ? `已发送 · ${result.platform_id}` : "已发送" };
    else if (result.status === "rate_limited")
      chip = { cls: "text-orange-600 bg-orange-50", icon: <Ban size={12} />, text: result.reason || "今日已达上限" };
    else if (result.status === "engine_not_ready")
      chip = { cls: "text-orange-600 bg-orange-50", icon: <ShieldAlert size={12} />, text: "引擎未就绪，请用复制+打开原帖" };
    else if (result.status === "blocked_checks")
      chip = { cls: "text-red-600 bg-red-50", icon: <ShieldAlert size={12} />, text: "校验未通过，禁止发送" };
  }
  if (!chip && !showCount) return null;
  return (
    <div className="flex flex-wrap items-center gap-2.5 mt-2.5">
      {chip && (
        <span className={`inline-flex items-center gap-1.5 text-[11px] font-semibold px-2.5 py-1 rounded-full ${chip.cls}`}>
          {chip.icon} {chip.text}
        </span>
      )}
      {showCount && (
        <span className="inline-flex items-center gap-1 text-[11px] text-[var(--text3)]">
          <Send size={11} /> 今日 {count}/{limit}
        </span>
      )}
    </div>
  );
}

/* ── Detail panel ────────────────────────────────────────────── */

function DetailPanel({ lead, onChanged }: { lead: Lead; onChanged: () => void }) {
  const qc = useQueryClient();
  const source = srcKey(lead);
  const sm = SOURCE_META[source];
  const meta = zbjMeta(lead);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(lead.draft_text ?? "");
  const [copied, setCopied] = useState(false);
  const [sendResult, setSendResult] = useState<SendResult | null>(null);

  async function withRetry<T>(fn: (rev: number) => Promise<T>): Promise<T> {
    try {
      return await fn(lead.rev);
    } catch (e) {
      if (isRevMismatch(e)) return fn(e.current_rev);
      throw e;
    }
  }

  const saveMut = useMutation({
    mutationFn: () => withRetry((rev) => leadsApi.update(lead.lead_id, { draft_text: draft, rev })),
    onSuccess: async () => { setEditing(false); await qc.invalidateQueries({ queryKey: ["leads-list"] }); onChanged(); },
    onError: (e) => alert(`保存失败：${errorMessage(e)}`),
  });

  const touchMut = useMutation({
    mutationFn: (outcome?: LeadOutcome) => withRetry((rev) => leadsApi.touch(lead.lead_id, { outcome, rev })),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["leads-list"] });
      await qc.invalidateQueries({ queryKey: ["leads-stats"] });
      onChanged();
    },
    onError: (e) => alert(`操作失败：${errorMessage(e)}`),
  });

  const skipMut = useMutation({
    mutationFn: () => withRetry((rev) => leadsApi.update(lead.lead_id, { lead_status: "skipped", rev })),
    onSuccess: async () => { await qc.invalidateQueries({ queryKey: ["leads-list"] }); onChanged(); },
    onError: (e) => alert(`操作失败：${errorMessage(e)}`),
  });

  const sendMut = useMutation({
    mutationFn: () => leadsApi.send(lead.lead_id, {}),
    onSuccess: async (res) => {
      setSendResult(res);
      if (res.sent) {
        await qc.invalidateQueries({ queryKey: ["leads-list"] });
        await qc.invalidateQueries({ queryKey: ["leads-stats"] });
        onChanged();
      }
    },
    onError: (e) => alert(`发送失败：${errorMessage(e)}`),
  });

  async function copyAndOpen() {
    try {
      await navigator.clipboard.writeText(draft);
      setCopied(true);
    } catch {
      // 剪贴板不可用不阻断
    }
    if (lead.source_url) window.open(lead.source_url, "_blank", "noopener");
    touchMut.mutate(undefined);
  }

  const sendable = lead.check_lure_pass && lead.check_dup_pass;
  const isTouched = lead.lead_status === "touched";
  const sentDone = sendResult?.sent === true;

  return (
    <div className="flex-1 overflow-y-auto px-6 py-5">
      {/* 原帖 */}
      <section className="bg-white border border-[var(--border)] rounded-xl p-4 mb-4">
        <h3 className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--text3)] mb-2.5">
          <MessageSquareReply size={14} /> 原{sm.noun === "帖" ? "帖" : sm.noun}
        </h3>
        <div className="flex items-center gap-2.5 mb-2.5">
          <SrcBadge source={source} size={28} />
          <div>
            <div className="text-[13px] font-semibold text-[var(--text1)]">{lead.author || "匿名"}</div>
            <div className="text-[11px] text-[var(--text3)]">
              {sm.label} · {sm.noun} · {relativeTime(lead.posted_at || lead.detected_at)}发布
            </div>
          </div>
          {lead.source_url && (
            <a
              href={lead.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="ml-auto flex items-center gap-1 text-[12px] text-[var(--brand)] hover:underline"
            >
              <ExternalLink size={13} /> {sm.openVerb}
            </a>
          )}
        </div>
        <p className="text-[13px] leading-relaxed text-[var(--text1)] bg-[var(--bg)] border border-[var(--border)] rounded-lg px-3 py-2.5 whitespace-pre-wrap">
          {lead.post_text || "（无正文）"}
        </p>
      </section>

      {/* 猪八戒结构化 meta（Delta2，仅 zhubajie）*/}
      {meta && (
        <section className="bg-white border border-[var(--border)] rounded-xl p-4 mb-4">
          <h3 className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--text3)] mb-2.5">
            <Briefcase size={14} /> 需求单 · 结构化信息
          </h3>
          <div className="grid grid-cols-3 gap-3">
            <div>
              <div className="text-[11px] text-[var(--text3)] mb-0.5">预算</div>
              <div className="text-[13px] font-semibold text-[var(--text1)]">{meta.budget || "—"}</div>
            </div>
            <div>
              <div className="text-[11px] text-[var(--text3)] mb-0.5">交付周期</div>
              <div className="text-[13px] font-semibold text-[var(--text1)]">{meta.delivery || "—"}</div>
            </div>
            <div>
              <div className="text-[11px] text-[var(--text3)] mb-0.5">接单状态</div>
              <div className={`text-[13px] font-semibold ${meta.taken ? "text-amber-700" : "text-[var(--text1)]"}`}>
                {meta.taken === undefined ? "—" : meta.taken ? "已接单" : "未接单"}
              </div>
            </div>
          </div>
        </section>
      )}

      {/* 意图判定 */}
      <section className="bg-white border border-[var(--border)] rounded-xl p-4 mb-4">
        <h3 className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--text3)] mb-2.5">
          <Radar size={14} /> 意图判定
        </h3>
        <div className="grid grid-cols-3 gap-3 mb-2.5">
          <div>
            <div className="text-[11px] text-[var(--text3)] mb-0.5">是否求购</div>
            <div className="text-[13px] font-semibold text-green-600">{lead.is_intent ? "是" : "否"}</div>
          </div>
          <div>
            <div className="text-[11px] text-[var(--text3)] mb-0.5">画像匹配度</div>
            <div className="text-[13px] font-semibold text-[var(--text1)]">{lead.match_score ?? "—"}%</div>
          </div>
          <div>
            <div className="text-[11px] text-[var(--text3)] mb-0.5">触发场景</div>
            <div className="text-[13px] font-semibold text-[var(--text1)]">
              {lead.trigger_type ? TRIGGER_LABEL[lead.trigger_type] : "—"}
            </div>
          </div>
        </div>
        {lead.judge_reason && (
          <div className="text-[12.5px] text-[var(--text2)] border-t border-dashed border-[var(--border)] pt-2.5">
            <span className="font-semibold text-[var(--text1)]">判定理由：</span>{lead.judge_reason}
          </div>
        )}
      </section>

      {/* 首触草稿 */}
      <section className="bg-white border border-[var(--border)] rounded-xl p-4 mb-4">
        <h3 className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--text3)] mb-2.5">
          <Pencil size={14} /> 首触草稿
          <span className="ml-auto normal-case tracking-normal text-[11px] font-semibold text-[var(--text2)] bg-[var(--bg)] rounded-md px-2 py-0.5">
            {sm.draftFmt}
          </span>
        </h3>
        <textarea
          className="w-full min-h-[96px] bg-[var(--bg)] border border-[var(--border)] rounded-lg px-3 py-2.5 text-[13px] leading-relaxed text-[var(--text1)] outline-none focus:border-[var(--brand)] focus:ring-1 focus:ring-[var(--brand)] resize-y disabled:opacity-70"
          value={draft}
          disabled={!editing}
          onChange={(e) => setDraft(e.target.value)}
        />
        <div className="flex items-center gap-3 mt-2.5 text-[11px]">
          <span className={`flex items-center gap-1 ${lead.check_lure_pass ? "text-green-600" : "text-red-600"}`}>
            {lead.check_lure_pass ? <ShieldCheck size={13} /> : <ShieldAlert size={13} />}
            引流词校验 {lead.check_lure_pass ? "通过" : "未通过"}
          </span>
          <span className={`flex items-center gap-1 ${lead.check_dup_pass ? "text-green-600" : "text-red-600"}`}>
            {lead.check_dup_pass ? <ShieldCheck size={13} /> : <ShieldAlert size={13} />}
            雷同度校验 {lead.check_dup_pass ? "通过" : "未通过"}
          </span>
        </div>
      </section>

      {/* 操作区 */}
      {!isTouched && lead.lead_status !== "skipped" && (
        <>
          {editing ? (
            <div className="flex flex-wrap gap-2.5">
              <button
                onClick={() => saveMut.mutate()}
                disabled={saveMut.isPending}
                className="flex items-center gap-1.5 text-[13px] font-semibold px-4 py-2.5 rounded-lg bg-[var(--brand)] text-white disabled:opacity-60"
              >
                <Check size={15} /> {saveMut.isPending ? "保存中…" : "保存草稿"}
              </button>
              <button
                onClick={() => { setEditing(false); setDraft(lead.draft_text ?? ""); }}
                className="text-[13px] px-4 py-2.5 rounded-lg border border-[var(--border)] text-[var(--text2)]"
              >
                取消
              </button>
            </div>
          ) : sm.autoSend ? (
            /* 小红书：一键发送（主，演练默认）+ 复制+打开原帖（兜底次）*/
            <>
              <div className="flex flex-wrap gap-2.5">
                <button
                  onClick={() => sendMut.mutate()}
                  disabled={!sendable || sendMut.isPending || sentDone}
                  title={sendable ? "" : "校验未通过，不能发送"}
                  className={`flex items-center gap-1.5 text-[13px] font-semibold px-4 py-2.5 rounded-lg disabled:opacity-50 ${
                    sentDone
                      ? "bg-green-50 text-green-600 border border-green-200"
                      : "bg-[var(--brand)] text-white"
                  }`}
                >
                  {sendMut.isPending ? <Loader2 size={15} className="animate-spin" /> : sentDone ? <Check size={15} /> : <Send size={15} />}
                  {sendMut.isPending ? "发送中…" : sentDone ? "已发送" : "一键发送"}
                </button>
                <button
                  onClick={copyAndOpen}
                  disabled={touchMut.isPending}
                  className="flex items-center gap-1.5 text-[13px] px-4 py-2.5 rounded-lg border border-[var(--border)] text-[var(--text1)]"
                >
                  <Copy size={15} /> {copied ? "已复制，去发送" : "复制草稿 + 打开原帖"}
                </button>
                <button
                  onClick={() => setEditing(true)}
                  className="flex items-center gap-1.5 text-[13px] px-4 py-2.5 rounded-lg border border-[var(--border)] text-[var(--text1)]"
                >
                  <Pencil size={15} /> 改
                </button>
                <button
                  onClick={() => skipMut.mutate()}
                  disabled={skipMut.isPending}
                  className="flex items-center gap-1.5 text-[13px] px-4 py-2.5 rounded-lg text-[var(--text3)] hover:text-[var(--text1)]"
                >
                  <X size={15} /> 跳过
                </button>
              </div>
              <SendBar
                result={sendResult}
                count={sendResult?.count_today ?? null}
                limit={sendResult?.daily_limit ?? null}
              />
              <p className="flex items-center gap-1.5 text-[11px] text-[var(--text3)] mt-2.5">
                <Eye size={13} />
                演练态：点「一键发送」只跑校验与预览，<b className="text-[var(--text2)] font-semibold">不会真实发评论</b>。切真发需在引擎配置开启 ReaJason。
              </p>
            </>
          ) : (
            /* 知乎 / 猪八戒：只读 —— 复制草稿 + 打开问题/需求单 */
            <>
              <div className="flex flex-wrap gap-2.5">
                <button
                  onClick={copyAndOpen}
                  disabled={!sendable || touchMut.isPending}
                  title={sendable ? "" : "校验未通过"}
                  className="flex items-center gap-1.5 text-[13px] font-semibold px-4 py-2.5 rounded-lg bg-[var(--brand)] text-white disabled:opacity-50"
                >
                  <Copy size={15} /> {copied ? "已复制，去发送" : `复制草稿 + ${sm.openVerb}`}
                </button>
                <button
                  onClick={() => setEditing(true)}
                  className="flex items-center gap-1.5 text-[13px] px-4 py-2.5 rounded-lg border border-[var(--border)] text-[var(--text1)]"
                >
                  <Pencil size={15} /> 改
                </button>
                <button
                  onClick={() => skipMut.mutate()}
                  disabled={skipMut.isPending}
                  className="flex items-center gap-1.5 text-[13px] px-4 py-2.5 rounded-lg text-[var(--text3)] hover:text-[var(--text1)]"
                >
                  <X size={15} /> 跳过
                </button>
              </div>
              <p className="flex items-center gap-1.5 text-[11px] text-[var(--text3)] mt-2.5">
                <Clock size={13} />
                {sm.label}为只读：复制草稿后到原页面手动发布；系统不自动发。
              </p>
            </>
          )}
        </>
      )}

      {/* 已触达：标记沟通机会（北极星）*/}
      {isTouched && (
        <div className="flex flex-wrap items-center gap-2.5">
          <span className="text-[12px] text-[var(--text2)]">
            已触达{lead.sent_at ? "（已发送）" : ""}。结果跟进：
          </span>
          <button
            onClick={() => touchMut.mutate("replied")}
            disabled={touchMut.isPending}
            className={`flex items-center gap-1.5 text-[12px] px-3 py-1.5 rounded-lg border ${
              lead.outcome === "replied" || lead.outcome === "converted"
                ? "border-[var(--brand)] text-[var(--brand)]" : "border-[var(--border)] text-[var(--text2)]"
            }`}
          >
            <MessageSquareReply size={14} /> 有回复
          </button>
          <button
            onClick={() => touchMut.mutate("converted")}
            disabled={touchMut.isPending}
            className={`flex items-center gap-1.5 text-[12px] px-3 py-1.5 rounded-lg border ${
              lead.outcome === "converted"
                ? "border-green-500 text-green-600" : "border-[var(--border)] text-[var(--text2)]"
            }`}
          >
            <Handshake size={14} /> 成交
          </button>
        </div>
      )}
    </div>
  );
}

/* ── Main page ───────────────────────────────────────────────── */

export default function LeadsPage() {
  const { activeGoalId } = useGoalsStore();
  const [tab, setTab] = useState<Tab>("pending");
  const [srcFilter, setSrcFilter] = useState<SrcFilter>("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [scanResult, setScanResult] = useState<ScanResponse | null>(null);

  const scanMut = useMutation({
    mutationFn: (goalId: string) => leadsApi.scan(goalId, 20),
    onSuccess: (res) => {
      setScanResult(res);
      if (res.ok) {
        listQuery.refetch();
      }
    },
    onError: (e) => alert(`扫描失败：${errorMessage(e)}`),
  });

  function handleScan() {
    if (!activeGoalId) return;
    setScanResult(null);
    scanMut.mutate(activeGoalId);
  }

  const listQuery = useQuery({
    queryKey: ["leads-list", activeGoalId],
    queryFn: () => leadsApi.list({ goal_id: activeGoalId || undefined, limit: 200 }),
    refetchInterval: 60_000,
  });

  const all = useMemo(() => listQuery.data?.items ?? [], [listQuery.data]);

  const tabVisible = useMemo(() => {
    if (tab === "pending") return all.filter((l) => PENDING_STATUSES.includes(l.lead_status));
    if (tab === "touched") return all.filter((l) => l.lead_status === "touched");
    return all.filter((l) => l.lead_status === "skipped");
  }, [all, tab]);

  const srcCounts = useMemo(() => {
    const c: Record<SrcFilter, number> = { all: tabVisible.length, xhs: 0, zhihu: 0, zhubajie: 0 };
    for (const l of tabVisible) c[srcKey(l)]++;
    return c;
  }, [tabVisible]);

  const visible = useMemo(
    () => (srcFilter === "all" ? tabVisible : tabVisible.filter((l) => srcKey(l) === srcFilter)),
    [tabVisible, srcFilter],
  );

  const counts = useMemo(() => ({
    pending: all.filter((l) => PENDING_STATUSES.includes(l.lead_status)).length,
    touched: all.filter((l) => l.lead_status === "touched").length,
    skipped: all.filter((l) => l.lead_status === "skipped").length,
  }), [all]);

  const todayBySource = useMemo(() => {
    const today = new Date().toISOString().slice(0, 10);
    const m: Record<LeadSource, number> = { xhs: 0, zhihu: 0, zhubajie: 0 };
    for (const l of all) {
      if ((l.created_at || "").slice(0, 10) === today) m[srcKey(l)]++;
    }
    return m;
  }, [all]);

  const selected = useMemo(
    () => visible.find((l) => l.lead_id === selectedId) ?? visible[0] ?? null,
    [visible, selectedId],
  );

  const SRC_TABS: SrcFilter[] = ["all", "xhs", "zhihu", "zhubajie"];

  return (
    <div className="max-w-6xl mx-auto h-[calc(100vh-7rem)] flex flex-col">
      {/* 顶栏 */}
      <div className="flex items-center gap-3 mb-1">
        <h1 className="flex items-center gap-2 text-xl font-bold text-[var(--text1)]">
          <Radar size={20} className="text-[var(--brand)]" /> 线索雷达
        </h1>
        <span className="text-[11px] text-[var(--text3)] bg-white border border-[var(--border)] rounded-full px-2.5 py-0.5">
          待处理 {counts.pending}
        </span>
        <button
          type="button"
          onClick={handleScan}
          disabled={scanMut.isPending || !activeGoalId}
          title={activeGoalId ? "手动触发一次雷达扫描" : "请先在「目标对齐」激活一个目标"}
          className="flex items-center gap-1.5 text-[12px] font-semibold px-3 py-1.5 rounded-lg border border-[var(--brand)] text-[var(--brand)] hover:bg-[var(--brand)] hover:text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {scanMut.isPending ? <RefreshCw size={14} className="animate-spin" /> : <Search size={14} />}
          {scanMut.isPending ? "扫描中…" : "扫描"}
        </button>
        <div className="ml-auto flex gap-1 bg-white border border-[var(--border)] rounded-lg p-1">
          {(Object.keys(TAB_LABEL) as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => { setTab(t); setSelectedId(null); }}
              className={`text-[12px] px-3 py-1 rounded-md transition-colors ${
                tab === t ? "bg-[var(--brand)] text-white font-semibold" : "text-[var(--text2)] hover:text-[var(--text1)]"
              }`}
            >
              {TAB_LABEL[t]} {counts[t] > 0 && <span className="opacity-70">{counts[t]}</span>}
            </button>
          ))}
        </div>
      </div>

      <StatsStrip goalId={activeGoalId} todayBySource={todayBySource} />

      {/* 扫描结果通知 */}
      {scanResult && (
        <div className={`mb-3 px-4 py-2.5 rounded-lg border text-[13px] ${
          scanResult.ok
            ? "bg-green-50 border-green-200 text-green-700"
            : "bg-red-50 border-red-200 text-red-700"
        }`}>
          {scanResult.ok ? (
            <span className="flex items-center gap-2 flex-wrap">
              <Check size={15} />
              扫描完成：采集 {scanResult.stats.scanned} 条
              · 合格 {scanResult.stats.qualified} 条
              · 新建线索 {scanResult.stats.created} 条
              · 噪声过滤 {scanResult.stats.noise} 条
              · 重复跳过 {scanResult.stats.duplicate} 条
              {scanResult.stats.errors > 0 && <> · 异常 {scanResult.stats.errors} 条</>}
              {Object.entries(scanResult.stats.by_source || {}).map(([src, s]) => (
                <span key={src} className="text-[11px] opacity-75">
                  {src}: 采集{s.scanned} 合格{s.qualified || 0} 新建{s.created || 0}
                </span>
              ))}
            </span>
          ) : (
            <span className="flex items-center gap-2">
              <X size={15} /> 扫描失败：{scanResult.error}
            </span>
          )}
          <button
            onClick={() => setScanResult(null)}
            className="ml-3 text-[11px] underline hover:no-underline"
          >
            关闭
          </button>
        </div>
      )}

      {/* 主体：队列 + 详情 */}
      <div className="flex-1 flex min-h-0 bg-white border border-[var(--border)] rounded-xl overflow-hidden">
        {listQuery.isLoading ? (
          <div className="flex-1 flex items-center justify-center text-sm text-[var(--text2)]">加载线索中…</div>
        ) : listQuery.isError ? (
          <div className="flex-1 flex items-center justify-center text-sm text-red-600">
            加载失败：{errorMessage(listQuery.error)}
          </div>
        ) : tabVisible.length === 0 ? (
          <div className="flex-1 flex flex-col items-center justify-center text-[var(--text2)]">
            <Inbox size={32} className="text-[var(--text3)] mb-2" />
            <p className="text-sm font-medium text-[var(--text1)] mb-1">{TAB_LABEL[tab]}里暂无线索</p>
            <p className="text-xs">雷达检测到合格线索后会出现在这里</p>
          </div>
        ) : (
          <>
            <div className="w-[360px] flex-none border-r border-[var(--border)] overflow-y-auto bg-[var(--bg)]/40">
              {/* Delta1 · 信源筛选（独立分段，区别于生命周期 Tab）*/}
              <div className="sticky top-0 z-10 flex gap-1.5 px-3 py-2 bg-[var(--bg)] border-b border-[var(--border)]">
                {SRC_TABS.filter((s) => s === "all" || srcCounts[s] > 0).map((s) => (
                  <button
                    key={s}
                    onClick={() => { setSrcFilter(s); setSelectedId(null); }}
                    className={`flex items-center gap-1.5 text-[11.5px] px-2.5 py-1 rounded-full border transition-colors ${
                      srcFilter === s
                        ? "bg-white border-[var(--border)] text-[var(--text1)] font-semibold"
                        : "border-transparent text-[var(--text2)] hover:text-[var(--text1)]"
                    }`}
                  >
                    {s !== "all" && <SrcBadge source={s} size={15} />}
                    {s === "all" ? "全部" : SOURCE_META[s].label}
                    <span className="text-[var(--text3)]">{srcCounts[s]}</span>
                  </button>
                ))}
              </div>
              {visible.map((l) => (
                <QueueItem
                  key={l.lead_id}
                  lead={l}
                  selected={selected?.lead_id === l.lead_id}
                  onClick={() => setSelectedId(l.lead_id)}
                />
              ))}
              {visible.length === 0 && (
                <div className="px-4 py-8 text-center text-xs text-[var(--text3)]">该信源下暂无线索</div>
              )}
            </div>
            {selected && <DetailPanel key={selected.lead_id} lead={selected} onChanged={() => setSelectedId(selected.lead_id)} />}
          </>
        )}
      </div>
    </div>
  );
}
