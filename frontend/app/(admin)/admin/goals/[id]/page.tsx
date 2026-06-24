/* eslint-disable react-hooks/set-state-in-effect */
"use client";

import { use, useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";
import { useGoalsStore } from "@/stores/goals";

interface Strategy {
  core_message: string;
  content_funnel: {
    top_30pct: string;
    mid_40pct: string;
    bottom_30pct: string;
  };
}

type AngleStatus = "validated_hit" | "sunk" | "unknown";

interface UsedAngle {
  angle: string;
  status: AngleStatus;
  evidence_count?: number;
  sample_count?: number;
  sampleCount?: number;
  last_ces?: number | null;
}

interface Goal {
  id: string;
  name: string;
  objective: string;
  status: string;
  description: string;
  brand_position: string;
  keywords: string[];
  keyword_library: string[];
  used_angles?: Array<string | UsedAngle>;
  target_audience: { who: string; pain_points: string; interests: string };
  overall_strategy?: Strategy;
}

/** 把 used_angles（老字符串数组 / 新三态对象数组）规整成排序好的列表 */
function normalizeUsedAngles(used: Array<string | UsedAngle> | undefined): UsedAngle[] {
  const out: UsedAngle[] = [];
  for (const u of used ?? []) {
    if (typeof u === "string") {
      if (u.trim()) out.push({ angle: u, status: "unknown", evidence_count: 0, last_ces: null });
    } else if (u && u.angle) {
      out.push({
        ...u,
        status: normalizeAngleStatus(u.status),
        evidence_count: normalizeEvidenceCount(u.evidence_count ?? u.sample_count ?? u.sampleCount),
        last_ces: normalizeNullableNumber(u.last_ces),
      });
    }
  }
  const order: Record<AngleStatus, number> = { validated_hit: 0, unknown: 1, sunk: 2 };
  return out.sort((a, b) => order[a.status] - order[b.status]);
}

function normalizeEvidenceCount(value: unknown): number {
  const count = typeof value === "number" ? value : Number(value);
  return Number.isFinite(count) && count >= 0 ? count : 0;
}

function normalizeNullableNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const numberValue = typeof value === "number" ? value : Number(value);
  return Number.isFinite(numberValue) ? numberValue : null;
}

function normalizeAngleStatus(status: unknown): AngleStatus {
  return status === "validated_hit" || status === "sunk" ? status : "unknown";
}

const ANGLE_STATUS_STYLE: Record<AngleStatus, { label: string; cls: string }> = {
  validated_hit: { label: "✅ 已验证爆款", cls: "bg-green-100 text-green-700" },
  sunk: { label: "❌ 沉底", cls: "bg-red-100 text-red-700" },
  unknown: { label: "◽ 待观察", cls: "bg-gray-100 text-gray-600" },
};

const INPUT_CLS =
  "w-full border border-[var(--border)] rounded-lg px-3 py-2 text-sm outline-none focus:border-[var(--brand)] focus:ring-1 focus:ring-[var(--brand)] bg-white";

const LABEL_CLS = "block text-xs font-medium text-[var(--text2)] mb-1";

export default function GoalDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const qc = useQueryClient();
  const { activeGoalId, setActiveGoal } = useGoalsStore();

  const { data: goal, isLoading } = useQuery<Goal>({
    queryKey: ["goal", id],
    queryFn: () => apiFetch<Goal>(`/api/v1/goals/${id}`),
  });

  const [form, setForm] = useState<Partial<Goal>>({});
  const [savedMsg, setSavedMsg] = useState("");
  const [kwRaw, setKwRaw] = useState("");
  const [kwLibRaw, setKwLibRaw] = useState("");

  useEffect(() => {
    if (goal) {
      setForm(goal);
      setKwRaw((goal.keywords ?? []).join(", "));
      setKwLibRaw((goal.keyword_library ?? []).join(", "));
    }

  }, [goal]);

  const saveMut = useMutation({
    mutationFn: (body: Partial<Goal>) =>
      apiFetch<Goal>(`/api/v1/goals/${id}`, { method: "PUT", body: JSON.stringify(body) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["goals"] });
      qc.invalidateQueries({ queryKey: ["goal", id] });
      setSavedMsg("已保存");
      setTimeout(() => setSavedMsg(""), 2000);
    },
  });

  const [strategyError, setStrategyError] = useState<string | null>(null);
  const [generatingStrategy, setGeneratingStrategy] = useState(false);

  const strategyMut = useMutation({
    mutationFn: () =>
      apiFetch<{ strategy: Strategy; error?: string }>(`/api/v1/goals/${id}/strategy/generate`, {
        method: "POST",
      }),
    onSuccess: (data) => {
      setGeneratingStrategy(false);
      setStrategyError(data.error ?? null);
      if (data.strategy) {
        saveMut.mutate({ overall_strategy: data.strategy });
        setForm((f) => ({ ...f, overall_strategy: data.strategy }));
      }
    },
    onError: () => {
      setGeneratingStrategy(false);
      setStrategyError("请求失败，请检查后端是否启动");
    },
  });

  function handleGenerateStrategy() {
    setGeneratingStrategy(true);
    setStrategyError(null);
    strategyMut.mutate();
  }

  function update(key: keyof Goal, value: unknown) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  function updateAudience(key: string, value: string) {
    setForm((f) => ({
      ...f,
      target_audience: { ...(f.target_audience ?? { who: "", pain_points: "", interests: "" }), [key]: value },
    }));
  }

  function handleBlur() {
    if (!form.name?.trim()) return;
    saveMut.mutate(form);
  }

  function parseKws(raw: string): string[] {
    return raw.split(/[,，\n]/).map((s) => s.trim()).filter(Boolean);
  }

  if (isLoading) return <div className="py-16 text-center text-[var(--text2)] text-sm">加载中…</div>;
  if (!goal) return <div className="py-16 text-center text-[var(--text2)] text-sm">目标不存在</div>;

  const isActive = activeGoalId === id;
  const usedAngles = normalizeUsedAngles(form.used_angles ?? goal.used_angles);

  return (
    <div className="max-w-2xl mx-auto">
      <div className="flex items-center gap-2 mb-5">
        <button
          className="text-sm text-[var(--text2)] hover:text-[var(--brand)] transition-colors"
          onClick={() => router.push("/admin/goals")}
        >
          ← 所有目标
        </button>
        <span className="text-[var(--border)]">/</span>
        <span className="text-sm font-semibold text-[var(--text1)]">{goal.name}</span>
        {savedMsg && <span className="text-xs text-[var(--color-completed)] ml-auto">{savedMsg}</span>}
        {!savedMsg && saveMut.isPending && <span className="text-xs text-[var(--text3)] ml-auto">保存中…</span>}
        {!isActive && !savedMsg && !saveMut.isPending && (
          <Button size="sm" className="ml-auto" style={{ background: "var(--brand)", color: "white" }} onClick={() => setActiveGoal(id, form.name ?? goal.name)}>
            激活此目标
          </Button>
        )}
        {isActive && <span className="text-xs px-2 py-0.5 rounded-full bg-[var(--brand)] text-white ml-auto">当前激活</span>}
      </div>

      <div className="bg-white border border-[var(--border)] rounded-xl p-5 space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className={LABEL_CLS}>目标名称</label>
            <input
              className={INPUT_CLS}
              value={form.name ?? ""}
              onChange={(e) => update("name", e.target.value)}
              onBlur={handleBlur}
            />
          </div>
          <div>
            <label className={LABEL_CLS}>核心目标</label>
            <input
              className={INPUT_CLS}
              value={form.objective ?? ""}
              onChange={(e) => update("objective", e.target.value)}
              onBlur={handleBlur}
            />
          </div>
        </div>

        <div>
          <label className={LABEL_CLS}>目标描述</label>
          <textarea
            className={`${INPUT_CLS} h-16 resize-none`}
            value={form.description ?? ""}
            onChange={(e) => update("description", e.target.value)}
            onBlur={handleBlur}
          />
        </div>

        <div>
          <label className={LABEL_CLS}>账号定位</label>
          <input
            className={INPUT_CLS}
            value={form.brand_position ?? ""}
            onChange={(e) => update("brand_position", e.target.value)}
            onBlur={handleBlur}
          />
        </div>

        <div className="border-t border-[var(--border)] pt-4">
          <div className="text-xs font-semibold text-[var(--text1)] mb-3">目标受众</div>
          <div className="space-y-3">
            <div>
              <label className={LABEL_CLS}>受众描述</label>
              <input
                className={INPUT_CLS}
                value={form.target_audience?.who ?? ""}
                onChange={(e) => updateAudience("who", e.target.value)}
                onBlur={handleBlur}
              />
            </div>
            <div>
              <label className={LABEL_CLS}>核心痛点</label>
              <input
                className={INPUT_CLS}
                value={form.target_audience?.pain_points ?? ""}
                onChange={(e) => updateAudience("pain_points", e.target.value)}
                onBlur={handleBlur}
              />
            </div>
            <div>
              <label className={LABEL_CLS}>兴趣偏好</label>
              <input
                className={INPUT_CLS}
                value={form.target_audience?.interests ?? ""}
                onChange={(e) => updateAudience("interests", e.target.value)}
                onBlur={handleBlur}
              />
            </div>
          </div>
        </div>

        <div className="border-t border-[var(--border)] pt-4">
          <div className="text-xs font-semibold text-[var(--text1)] mb-3">关键词</div>
          <div className="space-y-3">
            <div>
              <label className={LABEL_CLS}>核心关键词（逗号分隔）</label>
              <textarea
                className={`${INPUT_CLS} h-14 resize-none font-mono text-xs`}
                value={kwRaw}
                onChange={(e) => setKwRaw(e.target.value)}
                onBlur={() => {
                  const parsed = parseKws(kwRaw);
                  setForm((f) => ({ ...f, keywords: parsed }));
                  saveMut.mutate({ ...form, keywords: parsed });
                }}
              />
              <div className="flex flex-wrap gap-1 mt-1">
                {parseKws(kwRaw).map((kw) => (
                  <span key={kw} className="text-xs px-2 py-0.5 rounded-full bg-[var(--brand)]/10 text-[var(--brand)]">{kw}</span>
                ))}
              </div>
            </div>
            <div>
              <label className={LABEL_CLS}>关键词库（逗号分隔）</label>
              <textarea
                className={`${INPUT_CLS} h-14 resize-none font-mono text-xs`}
                value={kwLibRaw}
                onChange={(e) => setKwLibRaw(e.target.value)}
                onBlur={() => {
                  const parsed = parseKws(kwLibRaw);
                  setForm((f) => ({ ...f, keyword_library: parsed }));
                  saveMut.mutate({ ...form, keyword_library: parsed });
                }}
              />
            </div>
          </div>
        </div>
      </div>

      <div className="bg-white border border-[var(--border)] rounded-xl p-5 mt-4">
        <div className="flex items-center justify-between gap-3 mb-3">
          <div>
            <div className="text-xs font-semibold text-[var(--text1)]">角度表现</div>
            <div className="text-[11px] text-[var(--text3)] mt-0.5">根据发布后 CES 自动判定</div>
          </div>
          <span className="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">
            {usedAngles.length} 个角度
          </span>
        </div>

        {usedAngles.length > 0 ? (
          <div className="divide-y divide-[var(--border)]">
            {usedAngles.map((item) => {
              const style = ANGLE_STATUS_STYLE[item.status];
              return (
                <div key={item.angle} className="py-2.5 flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-[var(--text1)] truncate">{item.angle}</div>
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-1 text-[11px] text-[var(--text3)]">
                      <span>样本 {item.evidence_count ?? 0}</span>
                      <span>最近 CES {item.last_ces ?? "-"}</span>
                    </div>
                  </div>
                  <span className={`shrink-0 text-[10px] px-2 py-0.5 rounded-full ${style.cls}`}>
                    {style.label}
                  </span>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="py-5 text-center text-xs text-[var(--text3)]">暂无角度记录</div>
        )}
      </div>

      {/* Strategy Section */}
      <div className="bg-white border border-[var(--border)] rounded-xl p-5 mt-4">
        <div className="flex items-center justify-between mb-3">
          <div className="text-xs font-semibold text-[var(--text1)]">整体内容策略</div>
          <Button
            size="sm"
            variant="outline"
            onClick={handleGenerateStrategy}
            disabled={generatingStrategy}
          >
            {generatingStrategy ? "生成中…" : "🤖 AI 生成策略"}
          </Button>
        </div>

        {strategyError && (
          <div className="bg-amber-50 border border-amber-200 rounded-lg p-2.5 mb-3 text-xs text-amber-800">
            ⚠️ {strategyError}
          </div>
        )}

        {form.overall_strategy ? (
          <div className="space-y-3">
            <div className="p-3 bg-[var(--brand)]/5 rounded-lg border border-[var(--brand)]/10">
              <div className="text-[10px] text-[var(--text3)] uppercase tracking-wide mb-1">核心传播信息</div>
              <div className="text-sm text-[var(--text1)]">{form.overall_strategy.core_message}</div>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <div className="p-2.5 bg-orange-50 rounded-lg border border-orange-100">
                <div className="text-[10px] text-orange-600 mb-0.5">引流层 30%</div>
                <div className="text-xs text-[var(--text1)]">{form.overall_strategy.content_funnel.top_30pct}</div>
              </div>
              <div className="p-2.5 bg-blue-50 rounded-lg border border-blue-100">
                <div className="text-[10px] text-blue-600 mb-0.5">信任层 40%</div>
                <div className="text-xs text-[var(--text1)]">{form.overall_strategy.content_funnel.mid_40pct}</div>
              </div>
              <div className="p-2.5 bg-green-50 rounded-lg border border-green-100">
                <div className="text-[10px] text-green-600 mb-0.5">转化层 30%</div>
                <div className="text-xs text-[var(--text1)]">{form.overall_strategy.content_funnel.bottom_30pct}</div>
              </div>
            </div>
          </div>
        ) : (
          <div className="text-center py-6 text-[var(--text3)]">
            <div className="text-2xl mb-2">🎯</div>
            <p className="text-xs">点击「AI 生成策略」基于当前目标自动生成内容漏斗策略</p>
          </div>
        )}
      </div>
    </div>
  );
}
