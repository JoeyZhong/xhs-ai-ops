import { apiFetch } from "@/lib/api";

export interface SkillSummary {
  id: string;
  name: string;
  description: string;
  suggested_for: string[];
  owner: "universal" | "mine";
  source_skill_id: string | null;
  version: string;
  rev: number;
}

export interface SkillDetail extends SkillSummary {
  body: string;
  status: string;
  created_at: string;
  updated_at: string;
}

function qs(params: Record<string, string | number | undefined>): string {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined);
  if (!entries.length) return "";
  return "?" + entries.map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&");
}

export const skillsApi = {
  list: (params: { owner?: string; suggested_for?: string; limit?: number; cursor?: string } = {}) =>
    apiFetch<SkillSummary[]>(`/api/v1/skills${qs(params)}`),

  get: (id: string) =>
    apiFetch<SkillDetail>(`/api/v1/skills/${id}`),

  create: (data: { name: string; description: string; body: string; suggested_for?: string[]; owner?: string }) =>
    apiFetch<SkillDetail>("/api/v1/skills", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  update: (id: string, data: Partial<Pick<SkillDetail, "name" | "description" | "body" | "suggested_for">> & { expected_rev: number }) =>
    apiFetch<SkillDetail>(`/api/v1/skills/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  delete: (id: string) =>
    apiFetch<{ deleted: boolean; unequipped_from: string[] }>(`/api/v1/skills/${id}`, {
      method: "DELETE",
    }),

  fork: (id: string, name?: string) =>
    apiFetch<SkillDetail>(`/api/v1/skills/${id}/fork`, {
      method: "POST",
      body: JSON.stringify({ name }),
    }),

  listEquipment: (role: string) =>
    apiFetch<SkillSummary[]>(`/api/v1/agents/${role}/equipment`),

  equip: (role: string, skill_id: string) =>
    apiFetch<{ equipped: boolean; skill_id: string }>(`/api/v1/agents/${role}/equipment`, {
      method: "POST",
      body: JSON.stringify({ skill_id }),
    }),

  unequip: (role: string, skill_id: string) =>
    apiFetch<{ unequipped: boolean; skill_id: string }>(`/api/v1/agents/${role}/equipment/${skill_id}`, {
      method: "DELETE",
    }),

  importZip: async (file: File, options?: { owner?: "mine" | "universal"; auto_equip?: boolean }) => {
    const form = new FormData();
    form.append("file", file);
    form.append("owner", options?.owner ?? "mine");
    form.append("auto_equip", String(options?.auto_equip ?? true));

    const { getToken, clearToken } = await import("@/lib/api");
    const token = getToken();
    const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
    const res = await fetch(`${API_BASE}/api/v1/skills/import`, {
      method: "POST",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: form,
    });

    if (res.status === 401 || res.status === 403) {
      clearToken();
      window.location.href = "/login?error=token";
      throw new Error("认证失败，请重新登录");
    }
    if (!res.ok) {
      const text = await res.text().catch(() => res.statusText);
      throw new Error(text);
    }
    return res.json() as Promise<SkillDetail>;
  },
};
