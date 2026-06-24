"use client";

import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";
import { useGoalsStore } from "@/stores/goals";

interface Goal {
  id: string;
  name: string;
  objective: string;
  status: string;
  description?: string;
}

interface GoalsResponse {
  goals: Goal[];
  active_goal_id: string;
}

const INPUT_CLS =
  "w-full border border-[var(--border)] rounded-lg px-3 py-2 text-sm outline-none focus:border-[var(--brand)] focus:ring-1 focus:ring-[var(--brand)] bg-white";

const STATUS_COLOR: Record<string, string> = {
  active: "var(--color-completed)",
  paused: "var(--color-pending)",
  archived: "var(--color-cancelled)",
};

export default function GoalsPage() {
  const qc = useQueryClient();
  const router = useRouter();
  const { activeGoalId, setActiveGoal } = useGoalsStore();
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [objective, setObjective] = useState("");
  const [description, setDescription] = useState("");

  const { data, isLoading } = useQuery<GoalsResponse>({
    queryKey: ["goals"],
    queryFn: () => apiFetch<GoalsResponse>("/api/v1/goals"),
  });

  useEffect(() => {
    if (!data) return;
    const fallbackGoalId = data.active_goal_id || data.goals[0]?.id || "";
    const selectedGoalId = activeGoalId || fallbackGoalId;
    const active = data.goals.find((g) => g.id === selectedGoalId);
    if (active?.name) setActiveGoal(active.id, active.name);
  }, [data, activeGoalId, setActiveGoal]);

  const createMut = useMutation({
    mutationFn: (body: object) =>
      apiFetch<Goal>("/api/v1/goals", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["goals"] });
      setShowCreate(false);
      setName("");
      setObjective("");
      setDescription("");
    },
  });

  function handleCreate() {
    if (!name.trim()) return;
    createMut.mutate({ name: name.trim(), objective: objective.trim(), description: description.trim() });
  }

  function handleActivate(id: string, name: string) {
    setActiveGoal(id, name);
  }

  return (
    <div className="max-w-2xl mx-auto">
      <div className="flex items-center justify-between mb-5">
        <h1 className="text-xl font-bold text-[var(--text1)]">目标对齐</h1>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => router.push("/admin/goals/new")}
          >
            ✚ 新建目标
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setShowCreate(!showCreate)}
          >
            快速创建
          </Button>
        </div>
      </div>

      {showCreate && (
        <div className="bg-white border border-[var(--brand)] rounded-xl p-4 mb-4 shadow-sm">
          <h2 className="text-sm font-semibold text-[var(--text1)] mb-3">创建运营目标</h2>
          <div className="space-y-3">
            <div>
              <label className="block text-xs text-[var(--text2)] mb-1">目标名称 *</label>
              <input className={INPUT_CLS} placeholder="例：深圳工厂点位招商" value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div>
              <label className="block text-xs text-[var(--text2)] mb-1">核心目标</label>
              <input className={INPUT_CLS} placeholder="例：转化B端物业主" value={objective} onChange={(e) => setObjective(e.target.value)} />
            </div>
            <div>
              <label className="block text-xs text-[var(--text2)] mb-1">目标描述</label>
              <textarea
                className={`${INPUT_CLS} h-16 resize-none`}
                placeholder="详细说明运营目标和预期效果"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </div>
          </div>
          <div className="flex gap-2 mt-3">
            <Button size="sm" style={{ background: "var(--brand)", color: "white" }} onClick={handleCreate} disabled={createMut.isPending || !name.trim()}>
              {createMut.isPending ? "创建中…" : "创建"}
            </Button>
            <Button size="sm" variant="outline" onClick={() => setShowCreate(false)}>取消</Button>
          </div>
        </div>
      )}

      {isLoading && (
        <div className="text-center py-16 text-[var(--text2)] text-sm">加载中…</div>
      )}

      <div className="space-y-2">
        {data?.goals.map((g) => {
          const isActive = g.id === activeGoalId;
          return (
            <div
              key={g.id}
              className={`bg-white border rounded-xl p-4 flex items-start gap-3 cursor-pointer hover:border-[var(--brand)] transition-colors ${
                isActive ? "border-[var(--brand)]" : "border-[var(--border)]"
              }`}
              onClick={() => router.push(`/admin/goals/${g.id}`)}
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-sm font-semibold text-[var(--text1)] truncate">{g.name}</span>
                  <span
                    className="text-[10px] px-1.5 py-0.5 rounded-full font-medium"
                    style={{ color: STATUS_COLOR[g.status] ?? "var(--text3)", background: `${STATUS_COLOR[g.status] ?? "var(--text3)"}18` }}
                  >
                    {g.status}
                  </span>
                  {isActive && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full font-medium bg-[var(--brand)] text-white">
                      当前
                    </span>
                  )}
                </div>
                {g.objective && <p className="text-xs text-[var(--text2)] truncate">{g.objective}</p>}
                {g.description && <p className="text-xs text-[var(--text3)] mt-0.5 truncate">{g.description}</p>}
              </div>
              <div className="flex gap-1.5 flex-shrink-0" onClick={(e) => e.stopPropagation()}>
                {!isActive && (
                  <Button size="sm" variant="outline" onClick={() => handleActivate(g.id, g.name)}>
                    激活
                  </Button>
                )}
                <Button size="sm" variant="outline" onClick={() => router.push(`/admin/goals/${g.id}`)}>
                  编辑
                </Button>
              </div>
            </div>
          );
        })}
      </div>

      {!isLoading && !data?.goals.length && (
        <div className="text-center py-16 text-[var(--text2)]">
          <div className="text-4xl mb-3">目标</div>
          <p className="text-sm">还没有运营目标，点击右上角创建</p>
        </div>
      )}
    </div>
  );
}
