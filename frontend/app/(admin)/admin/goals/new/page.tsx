"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";

interface Goal {
  id: string;
  name: string;
  objective: string;
  status: string;
  description: string;
}

const INPUT_CLS =
  "w-full border border-[var(--border)] rounded-lg px-3 py-2 text-sm outline-none focus:border-[var(--brand)] focus:ring-1 focus:ring-[var(--brand)] bg-white";

export default function NewGoalPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [objective, setObjective] = useState("");
  const [description, setDescription] = useState("");
  const [keywords, setKeywords] = useState("");
  const [brandPosition, setBrandPosition] = useState("");
  const [who, setWho] = useState("");
  const [painPoints, setPainPoints] = useState("");
  const [interests, setInterests] = useState("");

  const createMut = useMutation({
    mutationFn: async () => {
      const goal = await apiFetch<Goal>("/api/v1/goals", {
        method: "POST",
        body: JSON.stringify({ name: name.trim(), objective: objective.trim(), description: description.trim() }),
      });
      // set additional fields via PUT
      const patch: Record<string, unknown> = {};
      if (brandPosition.trim()) patch.brand_position = brandPosition.trim();
      if (keywords.trim()) patch.keywords = keywords.split(/[,，]/).map((k) => k.trim()).filter(Boolean);
      if (who.trim() || painPoints.trim() || interests.trim()) {
        patch.target_audience = {
          who: who.trim(),
          pain_points: painPoints.trim(),
          interests: interests.trim(),
        };
      }
      if (Object.keys(patch).length > 0) {
        await apiFetch<Goal>(`/api/v1/goals/${goal.id}`, {
          method: "PUT",
          body: JSON.stringify(patch),
        });
      }
      return goal;
    },
    onSuccess: (goal) => {
      router.push(`/admin/goals/${goal.id}`);
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || createMut.isPending) return;
    createMut.mutate();
  }

  return (
    <div className="max-w-2xl mx-auto">
      <div className="flex items-center gap-3 mb-5">
        <button onClick={() => router.back()} className="text-[var(--text2)] hover:text-[var(--text1)] text-sm">
          ← 返回
        </button>
        <h1 className="text-xl font-bold text-[var(--text1)]">新建运营目标</h1>
      </div>

      <form onSubmit={handleSubmit} className="bg-white border border-[var(--border)] rounded-xl p-5 space-y-4">
        {/* Name */}
        <div>
          <label className="block text-xs font-medium text-[var(--text2)] mb-1">目标名称 *</label>
          <input
            className={INPUT_CLS}
            placeholder="例：深圳工厂点位招商"
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoFocus
          />
        </div>

        {/* Objective */}
        <div>
          <label className="block text-xs font-medium text-[var(--text2)] mb-1">核心目标</label>
          <input
            className={INPUT_CLS}
            placeholder="例：转化B端物业主主动联系"
            value={objective}
            onChange={(e) => setObjective(e.target.value)}
          />
        </div>

        {/* Description */}
        <div>
          <label className="block text-xs font-medium text-[var(--text2)] mb-1">目标描述</label>
          <textarea
            className={`${INPUT_CLS} h-16 resize-none`}
            placeholder="详细说明运营目标和预期效果"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>

        {/* Brand Position */}
        <div>
          <label className="block text-xs font-medium text-[var(--text2)] mb-1">品牌定位</label>
          <input
            className={INPUT_CLS}
            placeholder="例：深圳自助售卖机运营商，4年235台"
            value={brandPosition}
            onChange={(e) => setBrandPosition(e.target.value)}
          />
        </div>

        {/* Keywords */}
        <div>
          <label className="block text-xs font-medium text-[var(--text2)] mb-1">关键词（逗号分隔）</label>
          <input
            className={INPUT_CLS}
            placeholder="自助机点位招商, 工业区商铺出租"
            value={keywords}
            onChange={(e) => setKeywords(e.target.value)}
          />
        </div>

        {/* Target Audience */}
        <fieldset className="border border-[var(--border)] rounded-lg p-3 space-y-3">
          <legend className="text-xs font-medium text-[var(--text2)] px-1">目标受众</legend>
          <div>
            <label className="block text-xs text-[var(--text3)] mb-1">是谁</label>
            <input className={INPUT_CLS} placeholder="工厂老板、写字楼物业" value={who} onChange={(e) => setWho(e.target.value)} />
          </div>
          <div>
            <label className="block text-xs text-[var(--text3)] mb-1">痛点</label>
            <input className={INPUT_CLS} placeholder="闲置场地无收益、招租困难" value={painPoints} onChange={(e) => setPainPoints(e.target.value)} />
          </div>
          <div>
            <label className="block text-xs text-[var(--text3)] mb-1">兴趣</label>
            <input className={INPUT_CLS} placeholder="自动化、低风险增收" value={interests} onChange={(e) => setInterests(e.target.value)} />
          </div>
        </fieldset>

        {/* Errors */}
        {createMut.isError && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700">
            创建失败：{(createMut.error as Error).message || "请检查后端是否启动"}
          </div>
        )}

        {/* Submit */}
        <div className="flex gap-2 pt-1">
          <Button
            type="submit"
            disabled={!name.trim() || createMut.isPending}
            style={{ background: "var(--brand)", color: "white" }}
          >
            {createMut.isPending ? "创建中…" : "创建目标"}
          </Button>
          <Button type="button" variant="outline" onClick={() => router.back()}>
            取消
          </Button>
        </div>
      </form>
    </div>
  );
}
