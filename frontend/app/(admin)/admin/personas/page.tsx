"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";
import { useGoalsStore } from "@/stores/goals";

interface Persona {
  id: string;
  nickname: string;
  background: string;
  style_notes: string;
  tone: string;
  system_prompt: string;
  created_at: string;
}

interface PersonasResponse {
  personas: Persona[];
  active_id: string;
}

const INPUT_CLS =
  "w-full border border-[var(--border)] rounded-lg px-3 py-2 text-sm outline-none focus:border-[var(--brand)] focus:ring-1 focus:ring-[var(--brand)] bg-white";
const LABEL_CLS = "block text-xs font-medium text-[var(--text2)] mb-1";

const EMPTY: Omit<Persona, "id" | "created_at"> = {
  nickname: "",
  background: "",
  style_notes: "",
  tone: "",
  system_prompt: "",
};

export default function PersonasPage() {
  const qc = useQueryClient();
  const { setActivePersonaId } = useGoalsStore();

  const [editId, setEditId] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ ...EMPTY });
  const [savedMsg, setSavedMsg] = useState("");

  const { data, isLoading } = useQuery<PersonasResponse>({
    queryKey: ["personas"],
    queryFn: () => apiFetch<PersonasResponse>("/api/v1/personas"),
  });

  const activeMut = useMutation({
    mutationFn: (id: string) =>
      apiFetch<{ active_id: string }>(`/api/v1/personas/${id}/activate`, { method: "POST" }),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["personas"] });
      setActivePersonaId(d.active_id);
    },
  });

  const saveMut = useMutation({
    mutationFn: (body: object) =>
      editId
        ? apiFetch<Persona>(`/api/v1/personas/${editId}`, { method: "PUT", body: JSON.stringify(body) })
        : apiFetch<Persona>("/api/v1/personas", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["personas"] });
      setEditId(null);
      setShowCreate(false);
      setForm({ ...EMPTY });
      setSavedMsg("已保存");
      setTimeout(() => setSavedMsg(""), 2000);
    },
  });

  function startEdit(p: Persona) {
    setEditId(p.id);
    setShowCreate(false);
    setForm({
      nickname: p.nickname,
      background: p.background,
      style_notes: p.style_notes,
      tone: p.tone,
      system_prompt: p.system_prompt,
    });
  }

  function cancelEdit() {
    setEditId(null);
    setShowCreate(false);
    setForm({ ...EMPTY });
  }

  const activeId = data?.active_id;

  return (
    <div className="max-w-2xl mx-auto">
      <div className="flex items-center justify-between mb-5">
        <h1 className="text-xl font-bold text-[var(--text1)]">人设管理</h1>
        <div className="flex items-center gap-2">
          {savedMsg && <span className="text-xs text-[var(--color-completed)]">{savedMsg}</span>}
          <Button
            size="sm"
            style={{ background: "var(--brand)", color: "white" }}
            onClick={() => { setShowCreate(true); setEditId(null); setForm({ ...EMPTY }); }}
          >
            + 新建人设
          </Button>
        </div>
      </div>

      {/* Create / Edit form */}
      {(showCreate || editId) && (
        <div className="bg-white border border-[var(--brand)] rounded-xl p-4 mb-4 shadow-sm">
          <h2 className="text-sm font-semibold text-[var(--text1)] mb-3">
            {editId ? "编辑人设" : "创建人设"}
          </h2>
          <div className="space-y-3">
            <div>
              <label className={LABEL_CLS}>账号昵称 *</label>
              <input className={INPUT_CLS} value={form.nickname} onChange={(e) => setForm((f) => ({ ...f, nickname: e.target.value }))} placeholder="例：示例品牌" />
            </div>
            <div>
              <label className={LABEL_CLS}>背景故事</label>
              <textarea className={`${INPUT_CLS} h-16 resize-none`} value={form.background} onChange={(e) => setForm((f) => ({ ...f, background: e.target.value }))} placeholder="在深圳做了4年售货机，管理235台…" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className={LABEL_CLS}>写作风格</label>
                <input className={INPUT_CLS} value={form.style_notes} onChange={(e) => setForm((f) => ({ ...f, style_notes: e.target.value }))} placeholder="务实接地气，夹粤语词" />
              </div>
              <div>
                <label className={LABEL_CLS}>语气调性</label>
                <input className={INPUT_CLS} value={form.tone} onChange={(e) => setForm((f) => ({ ...f, tone: e.target.value }))} placeholder="数据说话，实操经验" />
              </div>
            </div>
            <div>
              <label className={LABEL_CLS}>System Prompt</label>
              <textarea className={`${INPUT_CLS} h-20 resize-none font-mono text-xs`} value={form.system_prompt} onChange={(e) => setForm((f) => ({ ...f, system_prompt: e.target.value }))} placeholder="你是一名在深圳做了4年自助售货机的运营者…" />
            </div>
          </div>
          <div className="flex gap-2 mt-3">
            <Button
              size="sm"
              style={{ background: "var(--brand)", color: "white" }}
              disabled={saveMut.isPending || !form.nickname.trim()}
              onClick={() => saveMut.mutate(form)}
            >
              {saveMut.isPending ? "保存中…" : "保存"}
            </Button>
            <Button size="sm" variant="outline" onClick={cancelEdit}>取消</Button>
          </div>
        </div>
      )}

      {isLoading && <div className="text-center py-8 text-sm text-[var(--text2)]">加载中…</div>}

      <div className="space-y-2">
        {data?.personas.map((p) => {
          const isActive = p.id === activeId;
          return (
            <div
              key={p.id}
              className={`bg-white border rounded-xl p-4 ${isActive ? "border-[var(--brand)]" : "border-[var(--border)]"}`}
            >
              <div className="flex items-start gap-3">
                <div
                  className="w-9 h-9 rounded-full flex-shrink-0 flex items-center justify-center text-white font-bold text-sm"
                  style={{ background: "var(--brand)" }}
                >
                  {p.nickname.slice(0, 1)}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className="text-sm font-semibold text-[var(--text1)]">{p.nickname}</span>
                    {isActive && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded-full font-medium bg-[var(--brand)] text-white">当前</span>
                    )}
                  </div>
                  {p.background && <p className="text-xs text-[var(--text2)] truncate">{p.background}</p>}
                  {(p.style_notes || p.tone) && (
                    <p className="text-[10px] text-[var(--text3)] mt-0.5 truncate">
                      {[p.style_notes, p.tone].filter(Boolean).join(" · ")}
                    </p>
                  )}
                </div>
                <div className="flex gap-1.5 flex-shrink-0">
                  {!isActive && (
                    <Button size="sm" variant="outline" onClick={() => activeMut.mutate(p.id)} disabled={activeMut.isPending}>
                      激活
                    </Button>
                  )}
                  <Button size="sm" variant="outline" onClick={() => startEdit(p)}>编辑</Button>
                </div>
              </div>
              {p.system_prompt && (
                <div className="mt-2 ml-12 bg-gray-50 rounded-lg p-2 text-[10px] font-mono text-[var(--text3)] line-clamp-2">
                  {p.system_prompt}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {!isLoading && !data?.personas.length && (
        <div className="text-center py-16 text-[var(--text2)]">
          <div className="text-4xl mb-3">🎭</div>
          <p className="text-sm">还没有人设，点击右上角创建</p>
        </div>
      )}
    </div>
  );
}
