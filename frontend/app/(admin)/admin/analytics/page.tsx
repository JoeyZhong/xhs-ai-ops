"use client";

import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { useGoalsStore } from "@/stores/goals";
import { Button } from "@/components/ui/button";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,

} from "recharts";
import { format } from "date-fns";
/* ── Types ─────────────────────────────────────────────────────────────── */

interface PerformancePost {
  id: string;
  title: string;
  date: string;
  angle: string;
  likes: number;
  collections: number;
  comments: number;
  shares: number;
  follows: number;
  leads: number;
}

interface Goal {
  id: string;
  performance: {
    posts: PerformancePost[];
  };
  used_angles?: string[];
}

/* ── Helpers ───────────────────────────────────────────────────────────── */

function fmt(n: number | undefined) {
  return n == null ? "—" : n.toLocaleString();
}

function computeCES(p: {
  likes?: number;
  collections?: number;
  comments?: number;
  shares?: number;
  follows?: number;
}) {
  return (
    (p.likes ?? 0) * 1 +
    (p.collections ?? 0) * 1 +
    (p.comments ?? 0) * 4 +
    (p.shares ?? 0) * 4 +
    (p.follows ?? 0) * 8
  );
}

function genId() {
  return Math.random().toString(36).slice(2, 10);
}

const TH_CLS =
  "text-left text-[10px] font-medium text-[var(--text3)] px-3 py-2 uppercase tracking-wide";
const TD_CLS = "px-3 py-2 text-xs text-[var(--text2)]";
const INPUT_CLS =
  "w-full border border-[var(--border)] rounded px-2 py-1 text-xs outline-none focus:border-[var(--brand)] bg-white";

/* ── Sub-components ────────────────────────────────────────────────────── */

function PerformanceTrend({ posts }: { posts: PerformancePost[] }) {
  const data = useMemo(() => {
    const groups: Record<string, { date: string; ces: number; count: number }> = {};
    for (const p of posts) {
      if (!p.date) continue;
      if (!groups[p.date]) {
        groups[p.date] = { date: p.date, ces: 0, count: 0 };
      }
      groups[p.date].ces += computeCES(p);
      groups[p.date].count += 1;
    }
    return Object.values(groups).sort((a, b) => a.date.localeCompare(b.date));
  }, [posts]);

  if (data.length === 0) return null;

  return (
    <div className="bg-white border border-[var(--border)] rounded-xl p-4 mb-5">
      <div className="text-sm font-semibold text-[var(--text1)] mb-3">我的发布表现</div>
      <div className="h-48">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
            <XAxis dataKey="date" tick={{ fontSize: 10 }} stroke="#94a3b8" />
            <YAxis tick={{ fontSize: 10 }} stroke="#94a3b8" />
            <Tooltip
              contentStyle={{ fontSize: 12, borderRadius: 8 }}
              formatter={(value) => [
                typeof value === "number" ? value.toLocaleString() : "—",
                "",
              ]}
            />
            <Line
              type="monotone"
              dataKey="ces"
              name="CES"
              stroke="var(--brand)"
              strokeWidth={2}
              dot={{ r: 4 }}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function TenThreeOneProgress({ posts }: { posts: PerformancePost[] }) {
  const { phase, top3Avg, maxCES } = useMemo(() => {
    const count = posts.length;
    if (count === 0) return { phase: 1, top3Avg: 0, maxCES: 0 };
    const scores = posts.map((p) => computeCES(p)).sort((a, b) => b - a);
    const top3 = scores.slice(0, 3);
    const top3Avg = top3.length ? Math.round(top3.reduce((s, v) => s + v, 0) / top3.length) : 0;
    const maxCES = scores[0] ?? 0;
    const avg = Math.round(scores.reduce((s, v) => s + v, 0) / count);

    let phase = 1;
    if (count >= 10 && top3Avg > avg * 1.2) phase = 2;
    if (maxCES > avg * 2 && count >= 10) phase = 3;

    return { phase, top3Avg, maxCES };
  }, [posts]);

  const phases = [
    { label: "Phase 1", sub: "广撒网", desc: "发布 ≥10 篇测试内容", done: posts.length >= 10, current: phase === 1 },
    { label: "Phase 2", sub: "找共性", desc: "Top3 均值 > 平均 1.2x", done: phase >= 2, current: phase === 2 },
    { label: "Phase 3", sub: "精打磨", desc: "Top1 CES > 平均 2x", done: phase >= 3, current: phase === 3 },
  ];

  return (
    <div className="bg-white border border-[var(--border)] rounded-xl p-4 mb-5">
      <div className="text-sm font-semibold text-[var(--text1)] mb-3">10-3-1 爆款漏斗</div>
      <div className="grid grid-cols-3 gap-3 mb-4">
        {phases.map((p, i) => (
          <div
            key={i}
            className={`text-center p-3 rounded-lg border transition-colors ${
              p.done
                ? p.current
                  ? "border-[var(--brand)] bg-[var(--brand)]/5"
                  : "border-green-200 bg-green-50"
                : "border-gray-100 bg-gray-50"
            }`}
          >
            <div
              className={`text-2xl font-bold ${
                p.done ? (p.current ? "text-[var(--brand)]" : "text-green-600") : "text-gray-300"
              }`}
            >
              {p.done ? "✓" : i + 1}
            </div>
            <div className="text-[10px] text-[var(--text2)] mt-0.5">{p.sub}</div>
            <div className="text-[10px] text-[var(--text3)] mt-0.5">{p.desc}</div>
          </div>
        ))}
      </div>

      {/* Progress bar */}
      <div className="w-full bg-gray-100 rounded-full h-2 overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{
            width: `${Math.min((posts.length / 10) * 100, 100)}%`,
            background: phase >= 3 ? "#22c55e" : phase >= 2 ? "var(--brand)" : "#94a3b8",
          }}
        />
      </div>
      <div className="flex justify-between text-[10px] text-[var(--text3)] mt-1">
        <span>已发布 {posts.length} 篇</span>
        <span>
          {phase === 1 && "继续发布积累数据"}
          {phase === 2 && `Top3 均值 ${top3Avg}，寻找爆款共性`}
          {phase === 3 && `爆款候选 CES ${maxCES}，精打磨中`}
        </span>
      </div>
    </div>
  );
}

function PerformanceTable({
  posts,
  onChange,
}: {
  posts: PerformancePost[];
  onChange: (posts: PerformancePost[]) => void;
}) {
  const [editingId, setEditingId] = useState<string | null>(null);
  // Local draft for instant visual feedback during editing
  const [draftPosts, setDraftPosts] = useState(posts);

  // Sync parent changes when not editing (e.g. after mutation completes)
  if (editingId === null && draftPosts !== posts) {
    setDraftPosts(posts);
  }

  function addRow() {
    const newPost: PerformancePost = {
      id: genId(),
      title: "",
      date: format(new Date(), "yyyy-MM-dd"),
      angle: "",
      likes: 0,
      collections: 0,
      comments: 0,
      shares: 0,
      follows: 0,
      leads: 0,
    };
    const updated = [...draftPosts, newPost];
    onChange(updated);
    setDraftPosts(updated);
    setEditingId(newPost.id);
  }

  function updateRow(id: string, patch: Partial<PerformancePost>) {
    const updated = draftPosts.map((p) => (p.id === id ? { ...p, ...patch } : p));
    setDraftPosts(updated);
    onChange(updated);
  }

  function deleteRow(id: string) {
    const updated = draftPosts.filter((p) => p.id !== id);
    setDraftPosts(updated);
    onChange(updated);
  }

  return (
    <div className="bg-white border border-[var(--border)] rounded-xl overflow-hidden mb-5">
      <div className="px-4 py-3 border-b border-[var(--border)] flex items-center justify-between">
        <div>
          <span className="text-sm font-semibold text-[var(--text1)]">数据录入</span>
          <span className="text-xs text-[var(--text3)] ml-2">记录发布后 7 天最终数据</span>
        </div>
        <Button size="sm" onClick={addRow} style={{ background: "var(--brand)", color: "white" }}>
          + 新增
        </Button>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead className="border-b border-[var(--border)]">
            <tr>
              {["标题", "日期", "角度", "点赞", "收藏", "评论", "分享", "关注", "线索", "CES", ""].map((h) => (
                <th key={h} className={`${TH_CLS} ${h === "" ? "w-8" : ""}`}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border)]">
            {draftPosts.length === 0 && (
              <tr>
                <td colSpan={11} className={`${TD_CLS} text-center py-6 text-[var(--text3)]`}>
                  点击「+ 新增」添加第一篇数据
                </td>
              </tr>
            )}
            {draftPosts.map((p) => {
              const ces = computeCES(p);
              const isEditing = editingId === p.id;
              return (
                <tr key={p.id} className="hover:bg-gray-50 transition-colors">
                  <td className={TD_CLS}>
                    {isEditing ? (
                      <input
                        className={INPUT_CLS}
                        defaultValue={p.title}
                        onBlur={(e) => updateRow(p.id, { title: e.target.value })}
                        onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
                      />
                    ) : (
                      <span className="line-clamp-1 max-w-[120px]">{p.title || "—"}</span>
                    )}
                  </td>
                  <td className={TD_CLS}>
                    {isEditing ? (
                      <input
                        className={INPUT_CLS}
                        type="date"
                        value={p.date}
                        onChange={(e) => updateRow(p.id, { date: e.target.value })}
                      />
                    ) : (
                      p.date
                    )}
                  </td>
                  <td className={TD_CLS}>
                    {isEditing ? (
                      <input
                        className={INPUT_CLS}
                        defaultValue={p.angle}
                        onBlur={(e) => updateRow(p.id, { angle: e.target.value })}
                        onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
                      />
                    ) : (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--brand)]/10 text-[var(--brand)]">
                        {p.angle || "—"}
                      </span>
                    )}
                  </td>
                  {(["likes", "collections", "comments", "shares", "follows", "leads"] as const).map((k) => (
                    <td key={k} className={`${TD_CLS} text-right`}>
                      {isEditing ? (
                        <input
                          className={`${INPUT_CLS} text-right`}
                          type="number"
                          min={0}
                          value={p[k]}
                          onChange={(e) =>
                            updateRow(p.id, { [k]: parseInt(e.target.value) || 0 })
                          }
                        />
                      ) : (
                        fmt(p[k])
                      )}
                    </td>
                  ))}
                  <td className={`${TD_CLS} text-right font-semibold`} style={{ color: "var(--brand)" }}>
                    {ces.toLocaleString()}
                  </td>
                  <td className={TD_CLS}>
                    <div className="flex gap-1">
                      <button
                        className="text-[10px] text-[var(--text3)] hover:text-[var(--brand)]"
                        onClick={() => setEditingId(isEditing ? null : p.id)}
                      >
                        {isEditing ? "✓" : "✎"}
                      </button>
                      <button
                        className="text-[10px] text-[var(--text3)] hover:text-red-500"
                        onClick={() => deleteRow(p.id)}
                      >
                        ✕
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function DiagnosisPanel({ posts }: { posts: PerformancePost[] }) {
  const [show, setShow] = useState(false);

  const diagnosis = useMemo(() => {
    const tips: string[] = [];
    if (posts.length === 0) {
      tips.push("暂无数据，点击「+ 新增」记录已发布笔记的表现，开启数据追踪。");
      return tips;
    }

    const ownScores = posts.map((p) => ({ ces: computeCES(p), title: p.title }));
    const avgOwn = Math.round(ownScores.reduce((s, v) => s + v.ces, 0) / ownScores.length);
    const bestOwn = ownScores.reduce((a, b) => (a.ces > b.ces ? a : b));

    if (avgOwn < 100) {
      tips.push(`你的笔记平均 CES ${avgOwn}，互动偏低。建议优化标题钩子和内容结构，前 3 秒抓住注意力。`);
    } else {
      tips.push(`你的笔记平均 CES ${avgOwn}，表现良好。保持内容节奏，持续测试新角度。`);
    }
    if (bestOwn.ces > 0) {
      tips.push(`最高分「${bestOwn.title.slice(0, 20)}」CES ${bestOwn.ces}，分析成功因素复制到下一篇。`);
    }

    return tips;
  }, [posts]);

  return (
    <div className="mb-5">
      <Button size="sm" variant="outline" onClick={() => setShow(!show)}>
        {show ? "隐藏诊断" : "一键诊断"}
      </Button>
      {show && (
        <div className="mt-3 bg-amber-50 border border-amber-200 rounded-xl p-4">
          <div className="text-sm font-semibold text-amber-800 mb-2">诊断建议</div>
          <ul className="space-y-2">
            {diagnosis.map((tip, i) => (
              <li key={i} className="text-xs text-amber-700 flex gap-2">
                <span className="flex-shrink-0">{i + 1}.</span>
                <span>{tip}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/* ── Main Page ─────────────────────────────────────────────────────────── */

export default function AnalyticsPage() {
  const { activeGoalId } = useGoalsStore();

  const { data: goal } = useQuery<Goal>({
    queryKey: ["goal", activeGoalId],
    queryFn: () => apiFetch<Goal>(`/api/v1/goals/${activeGoalId}`),
    enabled: !!activeGoalId,
  });

  const posts = goal?.performance?.posts ?? [];

  const qc = useQueryClient();
  const saveMut = useMutation({
    mutationFn: (body: { performance: { posts: PerformancePost[] } }) =>
      apiFetch<Goal>(`/api/v1/goals/${activeGoalId}`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["goal", activeGoalId] });
    },
  });

  function handlePostsChange(newPosts: PerformancePost[]) {
    saveMut.mutate({ performance: { posts: newPosts } });
  }

  const stats = useMemo(() => {
    if (!posts.length) return { count: 0, avgCES: 0, totalCES: 0 };
    const scores = posts.map((p) => computeCES(p));
    return {
      count: posts.length,
      avgCES: Math.round(scores.reduce((s, v) => s + v, 0) / scores.length),
      totalCES: scores.reduce((s, v) => s + v, 0),
    };
  }, [posts]);

  return (
    <div className="max-w-4xl mx-auto">
      <h1 className="text-xl font-bold text-[var(--text1)] mb-5">数据追踪</h1>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-3 mb-5">
        {[
          { label: "已发布笔记", value: stats.count, unit: "篇" },
          { label: "平均 CES", value: stats.avgCES, unit: "" },
          { label: "总 CES", value: stats.totalCES, unit: "" },
        ].map(({ label, value, unit }) => (
          <div key={label} className="bg-white border border-[var(--border)] rounded-xl p-4">
            <div className="text-[10px] text-[var(--text3)] uppercase tracking-wide mb-1">{label}</div>
            <div className="text-2xl font-bold text-[var(--text1)]">
              {value.toLocaleString()}
              <span className="text-sm font-normal text-[var(--text2)] ml-0.5">{unit}</span>
            </div>
          </div>
        ))}
      </div>

      {/* My Performance Trend */}
      <PerformanceTrend posts={posts} />

      {/* 10-3-1 */}
      <TenThreeOneProgress posts={posts} />

      {/* Diagnosis */}
      <DiagnosisPanel posts={posts} />

      {/* Data Entry */}
      <PerformanceTable posts={posts} onChange={handlePostsChange} />

      {/* Empty state when no data at all */}
      {posts.length === 0 && (
        <div className="text-center py-16 text-[var(--text2)]">
          <div className="text-4xl mb-3">📈</div>
          <p className="text-sm font-medium text-[var(--text1)] mb-1">暂无数据</p>
          <p className="text-xs">点击上方「+ 新增」录入第一篇笔记的表现数据</p>
        </div>
      )}
    </div>
  );
}
