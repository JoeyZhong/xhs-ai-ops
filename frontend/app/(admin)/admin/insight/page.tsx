"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { apiFetch, generateIdempotencyKey, ApiError } from "@/lib/api";
import { useSSE, type SSEEvent } from "@/lib/useSSE";
import { useGoalsStore } from "@/stores/goals";

type Tab = "collect" | "hot" | "keywords" | "evidence";

interface Evidence {
  evidence_id: string;
  source_note_id: string;
  angle: string;
  funnel_stage: string;
  hook: string;
  key_insight: string;
  ces_score: number;
  extracted_at?: string;
}

interface ExtractResult {
  status: string;
  extracted_count: number;
  skipped_count: number;
  errors: { note_id?: string; error?: string }[];
  fallback_batches: number;
}

const FUNNEL_LABEL: Record<string, string> = {
  traffic: "引流",
  trust: "信任",
  conversion: "转化",
};

interface Note {
  "标题"?: string;
  "笔记标题"?: string;
  "点赞数"?: number;
  "收藏数"?: number;
  "评论数"?: number;
  "分享数"?: number;
  "关注数"?: number;
  ces_score: number;
  source_file?: string;
}

interface Goal {
  keyword_library?: string[];
  keywords?: string[];
}

const COOLDOWN_MS = 30 * 60 * 1000;
const LS_KEY = "xhs_last_collect_ts";
const INPUT_CLS = "border border-[var(--border)] rounded-lg px-3 py-2 text-sm outline-none focus:border-[var(--brand)] focus:ring-1 focus:ring-[var(--brand)] bg-white";

export default function InsightPage() {
  const [tab, setTab] = useState<Tab>("collect");
  const [keywords, setKeywords] = useState<string[]>(["餐饮选址技巧"]);
  const [kwInput, setKwInput] = useState("");
  const [collecting, setCollecting] = useState(false);
  const [cooldownLeft, setCooldownLeft] = useState(0);
  const [newKw, setNewKw] = useState("");
  const [prevTotal, setPrevTotal] = useState<number | null>(null);
  const { activeGoalId, activeGoalName } = useGoalsStore();
  const qc = useQueryClient();
  const logRef = useRef<HTMLDivElement>(null);

  // Cooldown timer
  useEffect(() => {
    const stored = localStorage.getItem(LS_KEY);
    if (stored) {
      const diff = COOLDOWN_MS - (Date.now() - parseInt(stored, 10));
      if (diff > 0) setCooldownLeft(diff); // eslint-disable-line react-hooks/set-state-in-effect
    }
    const interval = setInterval(() => {
      const s = localStorage.getItem(LS_KEY);
      if (!s) { setCooldownLeft(0); return; }
      const diff = COOLDOWN_MS - (Date.now() - parseInt(s, 10));
      setCooldownLeft(Math.max(0, diff));
    }, 1000);
    return () => clearInterval(interval);
  }, []);

  // SSE collection
  const [skipApi, setSkipApi] = useState(false);

  const { events, error: sseError, done: sseDone } = useSSE(
    collecting ? "/api/v1/collect/stream" : null,
    { keywords, account_id: "default", skip_api: skipApi, goal_id: activeGoalId },
    collecting
  );

  useEffect(() => {
    if (sseDone && collecting) {
      setCollecting(false); // eslint-disable-line react-hooks/set-state-in-effect
      qc.invalidateQueries({ queryKey: ["notes", activeGoalId] });
    }
  }, [sseDone, collecting, qc]);

  // reset prevTotal when goal changes
  useEffect(() => { setPrevTotal(null); }, [activeGoalId]); // eslint-disable-line react-hooks/exhaustive-deps, react-hooks/set-state-in-effect

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [events]);

  const { data: notesData } = useQuery({
    queryKey: ["notes", activeGoalId],
    queryFn: () => apiFetch<{ notes: Note[]; total: number }>(`/api/v1/notes?goal_id=${activeGoalId}`),
  });

  const { data: goalData } = useQuery<Goal>({
    queryKey: ["goal", activeGoalId],
    queryFn: () => apiFetch<Goal>(`/api/v1/goals/${activeGoalId}`),
  });

  // 目标切换时，从目标的 keywords 字段初始化采集关键词
  useEffect(() => {
    const kws = goalData?.keywords;
    if (kws && kws.length > 0) {
      setKeywords(kws); // eslint-disable-line react-hooks/set-state-in-effect
    }
  }, [goalData?.keywords?.join(","), activeGoalId]); // eslint-disable-line react-hooks/exhaustive-deps

  const saveKwMut = useMutation({
    mutationFn: (kws: string[]) =>
      apiFetch(`/api/v1/goals/${activeGoalId}`, {
        method: "PUT",
        body: JSON.stringify({ keywords: kws }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["goal", activeGoalId] }),
  });

  // ── Evidence pool (P2) ──────────────────────────────────────────
  const [cesThreshold, setCesThreshold] = useState("");
  const [extractError, setExtractError] = useState<string | null>(null);
  const [extractResult, setExtractResult] = useState<ExtractResult | null>(null);

  const { data: evidenceData } = useQuery<{ items: Evidence[]; total: number }>({
    queryKey: ["evidence"],
    queryFn: () => apiFetch<{ items: Evidence[]; total: number }>("/api/v1/intel/evidence?limit=100"),
  });

  const extractMut = useMutation({
    mutationFn: (threshold: number | null) =>
      apiFetch<ExtractResult>("/api/v1/intel/evidence/extract", {
        method: "POST",
        headers: { "Idempotency-Key": generateIdempotencyKey() },
        body: JSON.stringify(threshold != null ? { ces_threshold: threshold } : {}),
      }),
    onSuccess: (res) => {
      setExtractResult(res);
      setExtractError(null);
      qc.invalidateQueries({ queryKey: ["evidence"] });
    },
    onError: (err: Error) => {
      setExtractResult(null);
      setExtractError(err instanceof ApiError ? err.message : "提取失败，请稍后重试");
    },
  });

  function startExtract() {
    setExtractError(null);
    setExtractResult(null);
    const t = cesThreshold.trim();
    const parsed = t ? Number(t) : null;
    extractMut.mutate(parsed != null && Number.isFinite(parsed) ? parsed : null);
  }

  // group evidence by angle for display
  const evidenceByAngle: Record<string, Evidence[]> = {};
  for (const ev of evidenceData?.items ?? []) {
    (evidenceByAngle[ev.angle] ??= []).push(ev);
  }

  function addKeyword() {
    const kw = kwInput.trim();
    if (!kw || keywords.includes(kw)) { setKwInput(""); return; }
    const next = [...keywords, kw];
    setKeywords(next);
    setKwInput("");
    saveKwMut.mutate(next);
  }

  function removeKeyword(kw: string) {
    const next = keywords.filter((k) => k !== kw);
    setKeywords(next);
    saveKwMut.mutate(next);
  }

  function startCollect(forceBrowser = false) {
    if (keywords.length === 0) return;
    setPrevTotal(notesData?.total ?? 0);
    setSkipApi(forceBrowser);
    if (!forceBrowser) {
      localStorage.setItem(LS_KEY, String(Date.now()));
      setCooldownLeft(COOLDOWN_MS);
    }
    setCollecting(true);
  }

  async function addToLibrary() {
    const kw = newKw.trim();
    if (!kw) return;
    const current = goalData?.keyword_library ?? [];
    if (!current.includes(kw)) {
      await apiFetch(`/api/v1/goals/${activeGoalId}`, {
        method: "PUT",
        body: JSON.stringify({ keyword_library: [...current, kw] }),
      });
      qc.invalidateQueries({ queryKey: ["goal", activeGoalId] });
    }
    setNewKw("");
  }

  async function removeFromLibrary(kw: string) {
    const current = goalData?.keyword_library ?? [];
    await apiFetch(`/api/v1/goals/${activeGoalId}`, {
      method: "PUT",
      body: JSON.stringify({ keyword_library: current.filter((k) => k !== kw) }),
    });
    qc.invalidateQueries({ queryKey: ["goal", activeGoalId] });
  }

  const fmtMs = (ms: number) => {
    const m = Math.floor(ms / 60000);
    const s = Math.floor((ms % 60000) / 1000);
    return `${m}:${String(s).padStart(2, "0")}`;
  };

  const TABS: { key: Tab; label: string }[] = [
    { key: "collect", label: "📡 采集" },
    { key: "hot", label: "🔥 热词" },
    { key: "keywords", label: "🏷️ 关键词库" },
    { key: "evidence", label: "💡 爆款样本" },
  ];

  return (
    <div className="max-w-4xl mx-auto">
      <h1 className="text-xl font-bold text-[var(--text1)] mb-4">📊 市场洞察</h1>

      {/* Tabs */}
      <div className="flex gap-1 mb-5 bg-[var(--border)] rounded-lg p-1 w-fit">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
              tab === t.key
                ? "bg-white text-[var(--text1)] shadow-sm"
                : "text-[var(--text2)] hover:text-[var(--text1)]"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* 采集 Tab */}
      {tab === "collect" && (
        <div>
          {/* Keyword Chips */}
          <div className="bg-white border border-[var(--border)] rounded-xl p-4 mb-4">
            <label className="block text-sm font-medium text-[var(--text1)] mb-2">采集关键词</label>
            <div className="flex flex-wrap gap-1.5 mb-2 min-h-[32px]">
              {keywords.map((kw) => (
                <span
                  key={kw}
                  className="inline-flex items-center gap-1 px-2.5 py-1 bg-[var(--brand-mid)] text-[var(--brand)] rounded-full text-xs font-medium"
                >
                  {kw}
                  <button
                    onClick={() => removeKeyword(kw)}
                    className="hover:text-red-500 text-base leading-none"
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
            <div className="flex gap-2">
              <input
                className={`${INPUT_CLS} flex-1`}
                placeholder="输入关键词，回车添加"
                value={kwInput}
                onChange={(e) => setKwInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addKeyword()}
              />
              <Button variant="outline" size="sm" onClick={addKeyword}>添加</Button>
            </div>
          </div>

          {/* Collect button + cooldown */}
          <div className="flex items-center gap-3 mb-4">
            {cooldownLeft > 0 && !collecting ? (
              <>
                <Button
                  size="sm"
                  disabled={keywords.length === 0}
                  onClick={() => startCollect(true)}
                  variant="outline"
                >
                  🌐 浏览器兜底采集
                </Button>
                <span className="text-xs text-[var(--text2)]">
                  API 冷却中 {fmtMs(cooldownLeft)}，可直接走浏览器
                </span>
              </>
            ) : (
              <Button
                size="sm"
                disabled={collecting || keywords.length === 0}
                onClick={() => startCollect(false)}
                style={{ background: collecting ? undefined : "var(--brand)", color: "white" }}
              >
                {collecting ? "采集中…" : "立即采集"}
              </Button>
            )}
          </div>

          {/* SSE Error */}
          {sseError && (
            <div className="bg-red-50 border border-red-200 rounded-xl p-3 mb-4">
              <span className="text-sm text-red-600">✗ 采集失败：{sseError}</span>
            </div>
          )}

          {/* SSE Log */}
          {(events.length > 0 || collecting) && (
            <div className="bg-gray-950 rounded-xl p-3 mb-4">
              <div className="text-xs text-gray-400 mb-2 font-mono">采集日志</div>
              <div ref={logRef} className="h-36 overflow-y-auto space-y-0.5 font-mono text-xs">
                {events.map((ev: SSEEvent, i) => (
                  <div key={i} className="text-gray-300">
                    {ev.type === "done"
                      ? (() => {
                          const raw = (ev as {count?:number}).count ?? 0;
                          const newUniq = prevTotal != null ? (notesData?.total ?? 0) - prevTotal : null;
                          return (
                            <span className="text-green-400">
                              ✓ 采集完成 · 原始抓取 {raw} 条
                              {newUniq != null && <span className="ml-1">· <span className="text-emerald-300">新增唯一 {newUniq} 条</span></span>}
                            </span>
                          );
                        })()
                      : ev.type === "error"
                      ? <span className="text-red-400">✗ {String(ev.msg ?? ev.message ?? ev.error ?? "错误")}</span>
                      : ev.type === "progress"
                      ? <span className="text-gray-400">· {String(ev.msg ?? "")}</span>
                      : <span className="text-yellow-400">⚡ {String(ev.msg ?? ev.type ?? "")}</span>
                    }
                  </div>
                ))}
                {collecting && events.length === 0 && (
                  <div className="text-gray-500 animate-pulse">连接中…</div>
                )}
              </div>
            </div>
          )}

          {/* Notes Table */}
          {notesData && notesData.total > 0 && (
            <div className="bg-white border border-[var(--border)] rounded-xl overflow-hidden">
              <div className="px-4 py-3 border-b border-[var(--border)] flex items-center justify-between">
                <span className="text-sm font-medium text-[var(--text1)]">历史采集（目标累计唯一）</span>
                <span className="text-xs text-[var(--text2)]">
                  共 {notesData.total} 条唯一笔记
                  {prevTotal != null && notesData.total > prevTotal && (
                    <span className="text-emerald-600 ml-1">+{notesData.total - prevTotal} 新增</span>
                  )}
                </span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead className="border-b border-[var(--border)] bg-gray-50">
                    <tr>
                      {["标题", "点赞", "收藏", "评论", "分享", "关注", "CES"].map((h) => (
                        <th key={h} className="text-left px-3 py-2.5 font-medium text-[var(--text2)] whitespace-nowrap">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {notesData.notes.slice(0, 50).map((n, i) => (
                      <tr key={i} className={i < notesData.notes.length - 1 ? "border-b border-[var(--border)]" : ""}>
                        <td className="px-3 py-2 text-[var(--text1)] max-w-[200px] truncate">{n["标题"] ?? n["笔记标题"] ?? "—"}</td>
                        <td className="px-3 py-2 text-[var(--text2)]">{n["点赞数"] ?? 0}</td>
                        <td className="px-3 py-2 text-[var(--text2)]">{n["收藏数"] ?? 0}</td>
                        <td className="px-3 py-2 text-[var(--text2)]">{n["评论数"] ?? 0}</td>
                        <td className="px-3 py-2 text-[var(--text2)]">{n["分享数"] ?? 0}</td>
                        <td className="px-3 py-2 text-[var(--text2)]">{n["关注数"] ?? 0}</td>
                        <td className="px-3 py-2 font-semibold text-[var(--brand)]">{n.ces_score}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {notesData && notesData.total === 0 && !collecting && (
            <div className="text-sm text-[var(--text2)] text-center py-12">
              暂无采集数据，输入关键词后点击「立即采集」
            </div>
          )}
        </div>
      )}

      {/* 热词 Tab */}
      {tab === "hot" && (
        <div className="text-center py-16 text-[var(--text2)]">
          <div className="text-4xl mb-3">🔥</div>
          <p className="text-sm font-medium text-[var(--text1)] mb-1">热词监控</p>
          <p className="text-xs">F1.4 阶段上线，敬请期待</p>
        </div>
      )}

      {/* 关键词库 Tab */}
      {tab === "keywords" && (
        <div>
          <div className="bg-white border border-[var(--border)] rounded-xl p-4 mb-4">
            <h3 className="text-sm font-medium text-[var(--text1)] mb-3">关键词库（{activeGoalName || activeGoalId}）</h3>
            <div className="flex gap-2 mb-3">
              <input
                className={`${INPUT_CLS} flex-1`}
                placeholder="添加新关键词"
                value={newKw}
                onChange={(e) => setNewKw(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addToLibrary()}
              />
              <Button size="sm" onClick={addToLibrary}>添加</Button>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {(goalData?.keyword_library ?? []).map((kw) => (
                <span
                  key={kw}
                  className="inline-flex items-center gap-1 px-2.5 py-1 bg-[var(--brand-mid)] text-[var(--brand)] rounded-full text-xs font-medium"
                >
                  {kw}
                  <button
                    onClick={() => removeFromLibrary(kw)}
                    className="hover:text-red-500 text-base leading-none"
                  >
                    ×
                  </button>
                </span>
              ))}
              {(goalData?.keyword_library ?? []).length === 0 && (
                <span className="text-xs text-[var(--text3)]">暂无关键词，点击上方添加</span>
              )}
            </div>
          </div>
        </div>
      )}

      {/* 爆款样本 Tab (P2 Evidence Pool) */}
      {tab === "evidence" && (
        <div>
          {/* Extract control */}
          <div className="bg-white border border-[var(--border)] rounded-xl p-4 mb-4">
            <label className="block text-sm font-medium text-[var(--text1)] mb-2">
              从高 CES 笔记提取爆款样本
            </label>
            <p className="text-xs text-[var(--text2)] mb-3">
              对已采集的高互动笔记做 AI 拆解，抽取 角度 / 钩子 / 核心洞察，注入到内容生成 prompt。
            </p>
            <div className="flex gap-2 items-center">
              <input
                className={`${INPUT_CLS} w-44`}
                placeholder="CES 阈值（留空用默认）"
                value={cesThreshold}
                inputMode="numeric"
                onChange={(e) => setCesThreshold(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && startExtract()}
              />
              <Button
                size="sm"
                disabled={extractMut.isPending}
                onClick={startExtract}
                style={{ background: extractMut.isPending ? undefined : "var(--brand)", color: "white" }}
              >
                {extractMut.isPending ? "提取中…" : "提取爆款样本"}
              </Button>
            </div>

            {/* Result counters */}
            {extractResult && (
              <div className="mt-3 text-sm flex flex-wrap gap-x-4 gap-y-1">
                <span className="text-emerald-600 font-medium">
                  ✓ 新增提取 {extractResult.extracted_count} 条
                </span>
                <span className="text-[var(--text2)]">跳过 {extractResult.skipped_count} 条</span>
                {extractResult.fallback_batches > 0 && (
                  <span className="text-amber-600">降级批次 {extractResult.fallback_batches}</span>
                )}
                {extractResult.errors.length > 0 && (
                  <span className="text-red-500">错误 {extractResult.errors.length} 条</span>
                )}
              </div>
            )}
            {extractError && (
              <div className="mt-3 text-sm text-red-600">✗ {extractError}</div>
            )}
          </div>

          {/* Evidence list grouped by angle */}
          {evidenceData && evidenceData.total > 0 ? (
            <div className="space-y-4">
              <div className="text-xs text-[var(--text2)]">共 {evidenceData.total} 条爆款样本</div>
              {Object.entries(evidenceByAngle).map(([angle, items]) => (
                <div key={angle} className="bg-white border border-[var(--border)] rounded-xl overflow-hidden">
                  <div className="px-4 py-2.5 border-b border-[var(--border)] bg-gray-50 flex items-center justify-between">
                    <span className="text-sm font-semibold text-[var(--text1)]">{angle}</span>
                    <span className="text-xs text-[var(--text2)]">{items.length} 条</span>
                  </div>
                  <div className="divide-y divide-[var(--border)]">
                    {items.map((ev) => (
                      <div key={ev.evidence_id} className="px-4 py-3">
                        <div className="flex items-start justify-between gap-3 mb-1">
                          <span className="text-sm font-medium text-[var(--text1)]">{ev.hook}</span>
                          <span className="flex items-center gap-2 shrink-0">
                            <span className="px-2 py-0.5 bg-[var(--brand-mid)] text-[var(--brand)] rounded-full text-xs">
                              {FUNNEL_LABEL[ev.funnel_stage] ?? ev.funnel_stage}
                            </span>
                            <span className="text-xs font-semibold text-[var(--brand)]">CES {ev.ces_score}</span>
                          </span>
                        </div>
                        <p className="text-xs text-[var(--text2)] leading-relaxed">{ev.key_insight}</p>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-sm text-[var(--text2)] text-center py-12">
              暂无爆款样本，先采集高互动笔记，再点「提取爆款样本」
            </div>
          )}
        </div>
      )}
    </div>
  );
}
