"use client";

import { useCallback, useEffect, useState } from "react";
import { skillsApi, type SkillSummary } from "@/lib/api/skills";

const ROLES = ["intel", "content", "analyst"] as const;
type Role = (typeof ROLES)[number];

const ROLE_LABEL: Record<Role, string> = {
  intel: "Intel",
  content: "Content",
  analyst: "Analyst",
};

const OWNER_FILTERS = [
  { key: "all", label: "全部" },
  { key: "universal", label: "通用库" },
  { key: "mine", label: "我的库" },
] as const;

const INPUT_CLS =
  "w-full border border-[var(--border)] rounded-lg px-3 py-2 text-sm outline-none focus:border-[var(--brand)] focus:ring-1 focus:ring-[var(--brand)] bg-white";

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export default function SkillsPage() {
  const [ownerFilter, setOwnerFilter] = useState<"all" | "universal" | "mine">("all");
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [equipment, setEquipment] = useState<Record<Role, Set<string>>>({
    intel: new Set(),
    content: new Set(),
    analyst: new Set(),
  });

  // Edit modal state
  const [editSkill, setEditSkill] = useState<SkillSummary | null>(null);
  const [editName, setEditName] = useState("");
  const [editDesc, setEditDesc] = useState("");
  const [editBody, setEditBody] = useState("");
  const [editSuggested, setEditSuggested] = useState("");
  const [editMsg, setEditMsg] = useState("");

  // Fork modal state
  const [forkSkill, setForkSkill] = useState<SkillSummary | null>(null);
  const [forkName, setForkName] = useState("");

  // Import state
  const [importing, setImporting] = useState(false);
  const [importMsg, setImportMsg] = useState("");

  const refreshSkills = useCallback(async () => {
    try {
      const list = await skillsApi.list({ owner: ownerFilter, limit: 100 });
      setSkills(list);
      const pairs = await Promise.all(
        ROLES.map(async (r) => {
          const ss = await skillsApi.listEquipment(r);
          return [r, new Set(ss.map((s) => s.id))] as const;
        }),
      );
      setEquipment(Object.fromEntries(pairs) as Record<Role, Set<string>>);
    } finally {
      setLoading(false);
    }
  }, [ownerFilter]);

  useEffect(() => {
    void refreshSkills();
  }, [refreshSkills]);

  async function toggleEquip(role: Role, skill_id: string) {
    if (equipment[role].has(skill_id)) {
      await skillsApi.unequip(role, skill_id);
      setEquipment((e) => ({ ...e, [role]: new Set([...e[role]].filter((id) => id !== skill_id)) }));
    } else {
      await skillsApi.equip(role, skill_id);
      setEquipment((e) => ({ ...e, [role]: new Set([...e[role], skill_id]) }));
    }
  }

  async function handleImportZip(file: File | null) {
    if (!file) return;
    setImporting(true);
    setImportMsg("");
    try {
      const skill = await skillsApi.importZip(file, { owner: "mine", auto_equip: true });
      setImportMsg(`已导入：${skill.name}`);
      await refreshSkills();
    } catch (e: unknown) {
      setImportMsg(`导入失败：${errorMessage(e)}`);
    } finally {
      setImporting(false);
    }
  }

  async function handleEdit(id: string) {
    const sug = editSuggested.split(",").map((s) => s.trim()).filter(Boolean);
    try {
      const skill = await skillsApi.get(id);
      await skillsApi.update(id, {
        name: editName,
        description: editDesc,
        body: editBody,
        suggested_for: sug,
        expected_rev: skill.rev,
      });
      setEditMsg("保存成功");
      setEditSkill(null);
      await refreshSkills();
    } catch (e: unknown) {
      setEditMsg(`保存失败: ${errorMessage(e)}`);
    }
  }

  async function handleDelete(id: string) {
    if (!confirm("确定删除此 skill？")) return;
    await skillsApi.delete(id);
    await refreshSkills();
  }

  async function handleFork(id: string) {
    try {
      await skillsApi.fork(id, forkName || undefined);
      setForkSkill(null);
      await refreshSkills();
    } catch (e: unknown) {
      alert(`Fork 失败: ${errorMessage(e)}`);
    }
  }

  function openEdit(s: SkillSummary) {
    setEditSkill(s);
    setEditName(s.name);
    setEditDesc(s.description);
    setEditBody("");
    setEditSuggested(s.suggested_for.join(", "));
    setEditMsg("");
    // Load body
    skillsApi.get(s.id).then((d) => setEditBody(d.body));
  }

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <h1 className="text-xl font-bold mb-1">🧠 技能中枢</h1>
      <p className="text-sm text-gray-500 mb-4">方法论知识库 — 管理 Agent 可装备的 skill</p>

      {/* Owner filter chips */}
      <div className="flex gap-2 mb-4">
        {OWNER_FILTERS.map((o) => (
          <button
            key={o.key}
            onClick={() => setOwnerFilter(o.key)}
            className={`px-3 py-1 rounded text-sm ${
              ownerFilter === o.key ? "bg-[var(--brand)] text-white" : "bg-gray-100 text-gray-700"
            }`}
          >
            {o.label}
          </button>
        ))}
      </div>

      {/* Upload control */}
      <div className="flex items-center gap-2 mb-4">
        <label className="text-xs px-3 py-1.5 rounded bg-gray-900 text-white cursor-pointer">
          {importing ? "导入中..." : "上传 Skill Zip"}
          <input
            type="file"
            accept=".zip,application/zip"
            className="hidden"
            disabled={importing}
            onChange={(e) => {
              const file = e.target.files?.[0] ?? null;
              void handleImportZip(file);
              e.currentTarget.value = "";
            }}
          />
        </label>
        {importMsg && <span className="text-xs text-gray-500">{importMsg}</span>}
      </div>

      {/* Loading / Empty */}
      {loading ? (
        <div className="text-sm text-gray-400">加载中…</div>
      ) : skills.length === 0 ? (
        <div className="text-sm text-gray-400 py-8 text-center">暂无 skill</div>
      ) : (
        /* Skill cards */
        <div className="grid gap-3">
          {skills.map((s) => (
            <div key={s.id} className="border border-[var(--border)] rounded-lg p-4">
              <div className="flex justify-between items-start">
                <div className="flex-1 min-w-0">
                  <h3 className="font-semibold text-sm">{s.name}</h3>
                  <p className="text-xs text-gray-500 mt-0.5 truncate">{s.description}</p>
                  <div className="flex gap-1 mt-1.5 flex-wrap">
                    <span
                      className={`text-[10px] px-1.5 py-0.5 rounded ${
                        s.owner === "universal" ? "bg-purple-100 text-purple-700" : "bg-green-100 text-green-700"
                      }`}
                    >
                      {s.owner === "universal" ? "通用" : "我的"}
                    </span>
                    {s.suggested_for.length === 0 ? (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-400">
                        未分配
                      </span>
                    ) : (
                      s.suggested_for.map((role) => (
                        <span key={role} className="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-600">
                          建议: {role}
                        </span>
                      ))
                    )}
                  </div>
                </div>
                <div className="flex gap-1.5 ml-3 shrink-0">
                  {s.owner === "universal" && (
                    <button
                      onClick={() => {
                        setForkSkill(s);
                        setForkName(`${s.name} (fork)`);
                      }}
                      className="text-xs px-2 py-1 rounded bg-indigo-50 text-indigo-600 hover:bg-indigo-100"
                    >
                      Fork
                    </button>
                  )}
                  {s.owner === "mine" && (
                    <>
                      <button
                        onClick={() => openEdit(s)}
                        className="text-xs px-2 py-1 rounded bg-blue-50 text-blue-600 hover:bg-blue-100"
                      >
                        编辑
                      </button>
                      <button
                        onClick={() => handleDelete(s.id)}
                        className="text-xs px-2 py-1 rounded bg-red-50 text-red-600 hover:bg-red-100"
                      >
                        删除
                      </button>
                    </>
                  )}
                </div>
              </div>
              {/* Equipment chips */}
              <div className="flex gap-2 mt-3 pt-2 border-t border-[var(--border)]">
                <span className="text-xs text-gray-400 mt-0.5">装备到：</span>
                {ROLES.map((role) => (
                  <button
                    key={role}
                    onClick={() => toggleEquip(role, s.id)}
                    className={`text-xs px-2 py-1 rounded ${
                      equipment[role].has(s.id)
                        ? "bg-[var(--brand)] text-white"
                        : "bg-gray-100 text-gray-600 hover:bg-gray-200"
                    }`}
                  >
                    {ROLE_LABEL[role]}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Fork modal */}
      {forkSkill && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50" onClick={() => setForkSkill(null)}>
          <div className="bg-white rounded-lg p-6 w-[400px] shadow-xl" onClick={(e) => e.stopPropagation()}>
            <h2 className="text-sm font-bold mb-3">Fork Skill</h2>
            <p className="text-xs text-gray-500 mb-3">从「{forkSkill.name}」创建副本到我的库</p>
            <label className="text-xs text-gray-600 block mb-1">名称</label>
            <input className={INPUT_CLS} value={forkName} onChange={(e) => setForkName(e.target.value)} />
            <div className="flex gap-2 mt-4 justify-end">
              <button onClick={() => setForkSkill(null)} className="text-xs px-3 py-1.5 rounded bg-gray-100">
                取消
              </button>
              <button
                onClick={() => handleFork(forkSkill.id)}
                className="text-xs px-3 py-1.5 rounded bg-indigo-500 text-white"
              >
                Fork
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Edit modal */}
      {editSkill && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50" onClick={() => setEditSkill(null)}>
          <div className="bg-white rounded-lg p-6 w-[600px] max-h-[80vh] overflow-y-auto shadow-xl" onClick={(e) => e.stopPropagation()}>
            <h2 className="text-sm font-bold mb-3">编辑 Skill</h2>
            {editMsg && <p className="text-xs mb-2 text-green-600">{editMsg}</p>}
            <label className="text-xs text-gray-600 block mb-1">名称</label>
            <input className={INPUT_CLS + " mb-2"} value={editName} onChange={(e) => setEditName(e.target.value)} />
            <label className="text-xs text-gray-600 block mb-1">描述</label>
            <input className={INPUT_CLS + " mb-2"} value={editDesc} onChange={(e) => setEditDesc(e.target.value)} />
            <label className="text-xs text-gray-600 block mb-1">建议适配角色（逗号分隔）</label>
            <input className={INPUT_CLS + " mb-2"} value={editSuggested} onChange={(e) => setEditSuggested(e.target.value)} />
            <label className="text-xs text-gray-600 block mb-1">SKILL.md 内容</label>
            <textarea
              className={INPUT_CLS + " mb-3 font-mono"}
              rows={10}
              value={editBody}
              onChange={(e) => setEditBody(e.target.value)}
            />
            <div className="flex gap-2 justify-end">
              <button onClick={() => setEditSkill(null)} className="text-xs px-3 py-1.5 rounded bg-gray-100">
                取消
              </button>
              <button
                onClick={() => handleEdit(editSkill.id)}
                className="text-xs px-3 py-1.5 rounded bg-blue-500 text-white"
              >
                保存
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
