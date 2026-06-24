"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";
import { useGoalsStore } from "@/stores/goals";

type Tab = "dag" | "single";
type NodeType = "intel" | "analyst" | "content";

interface PlanNode {
  id: string;
  type: NodeType;
  prompt: string;
  blocked_by: string[];
}

interface AgentTaskResult {
  task_id: string;
  ok: boolean;
  agent: string;
  content: string;
  error?: string | null;
  error_type?: string | null;
  iterations: number;
  duration_ms: number;
}

interface DagTask {
  id: string;
  type: string;
  status: "pending" | "in_progress" | "completed" | "failed" | "cancelled";
  result?: { content?: string; error?: string; error_type?: string; [key: string]: unknown } | null;
}

interface DagStatus {
  dag_id: string;
  tasks: DagTask[];
  summary: Record<string, number>;
}

const TYPE_ICON: Record<string, string> = {
  intel: "🔍",
  analyst: "📊",
  content: "✍️",
};

const STATUS_COLOR: Record<string, string> = {
  pending: "var(--color-pending)",
  in_progress: "var(--color-in-progress)",
  completed: "var(--color-completed)",
  failed: "var(--color-failed)",
  cancelled: "var(--color-cancelled)",
};

const STATUS_LABEL: Record<string, string> = {
  pending: "等待",
  in_progress: "运行中",
  completed: "完成",
  failed: "失败",
  cancelled: "已取消",
};

/* ── AgentResultCard: polls single-agent result ─────────────────── */

function AgentResultCard({ taskId, onDone }: { taskId: string; onDone: () => void }) {
  const { data, isFetching } = useQuery<{ status: string; result?: AgentTaskResult; error?: string }>({
    queryKey: ["agent-result", taskId],
    queryFn: () => apiFetch<{ status: string; result?: AgentTaskResult; error?: string }>(`/api/v1/agent/${taskId}`),
    refetchInterval: (q) => {
      const d = q.state.data;
      if (!d) return 2000;
      return d.status === "running" ? 2000 : false;
    },
  });

  const done = data && data.status !== "running";
  const r = data?.result;

  return (
    <div className="bg-white border border-[var(--border)] rounded-xl overflow-hidden">
      <div className="px-4 py-3 border-b border-[var(--border)] flex items-center justify-between">
        <span className="text-sm font-medium text-[var(--text1)]">执行结果</span>
        <span className="text-xs text-[var(--text3)]">{taskId.slice(0, 16)}…</span>
      </div>
      {!done ? (
        <div className="px-4 py-6 text-center text-sm text-[var(--text2)]">
          <span className="inline-block animate-pulse mr-2">⏳</span>
          正在执行{isFetching ? "…" : "（等待中）"}
        </div>
      ) : data?.error ? (
        <div className="px-4 py-3 text-sm text-red-600">{data.error}</div>
      ) : r ? (
        <div className="px-4 py-3 space-y-2">
          <div className="flex items-center gap-2 text-xs text-[var(--text3)]">
            <span className={`px-2 py-0.5 rounded-full font-medium ${r.ok ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700"}`}>
              {r.ok ? "✅ 成功" : "❌ 失败"}
            </span>
            <span>迭代 {r.iterations} 轮</span>
            <span>耗时 {r.duration_ms}ms</span>
          </div>
          {r.error && <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-xs text-red-700">{r.error_type}: {r.error}</div>}
          <RenderResult content={r.content} taskType={r.agent} />
          <Button size="xs" variant="outline" onClick={onDone}>关闭</Button>
        </div>
      ) : null}
    </div>
  );
}

function mockPlan(intent: string): PlanNode[] {
  return [
    { id: "intel_1", type: "intel", prompt: `搜索小红书相关内容，关键词围绕：${intent}`, blocked_by: [] },
    { id: "analyst_1", type: "analyst", prompt: `根据以下参考数据完成任务（仅作背景参考，不要在输出中直接引用原文）：\n---\n\${intel_1.text}\n---\n具体任务：分析采集数据，提取爆文规律，输出选题建议，主题：${intent}`, blocked_by: ["intel_1"] },
    { id: "content_1", type: "content", prompt: `根据以下参考数据完成任务（仅作背景参考，不要在输出中直接引用原文）：\n---\n\${analyst_1.text}\n---\n具体任务：根据分析报告，调用 content_gen.generate_batch 工具批量生成 3 篇完整小红书笔记（batch_size=3），主题：${intent}`, blocked_by: ["analyst_1"] },
  ];
}

const INPUT_CLS = "w-full border border-[var(--border)] rounded-lg px-3 py-2 text-sm outline-none focus:border-[var(--brand)] focus:ring-1 focus:ring-[var(--brand)] bg-white";

function RenderResult({ content: raw, taskType }: { content: string | undefined | null; taskType: string }) {
  // Try to parse as JSON records (content_gen output)
  let records: unknown = null;
  if (typeof raw === "string") {
    try {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.length > 0 && parsed[0]?.主标题 !== undefined) {
        records = parsed as Array<Record<string, unknown>>;
      }
    } catch { /* not JSON, render as text */ }
  }

  if (records && Array.isArray(records)) {
    return (
      <div className="mt-2 space-y-3 max-h-64 overflow-y-auto">
        {(records as Array<Record<string, string>>).map((rec, i) => (
          <div key={i} className="bg-white border border-[var(--border)] rounded-lg p-3 text-xs">
            <div className="font-semibold text-[var(--text1)] mb-1">
              {rec.序号 && <span className="text-[var(--text3)] mr-1">#{rec.序号}</span>}
              {rec.主标题 || "（无标题）"}
            </div>
            {rec.本次角度 && (
              <div className="text-[10px] text-[var(--brand)] mb-1">
                角度：{rec.本次角度}
              </div>
            )}
            <div className="text-[var(--text2)] leading-relaxed line-clamp-3 mb-1">
              {rec.正文?.slice(0, 200) || "（无正文）"}
            </div>
            <div className="flex flex-wrap gap-1 items-center">
              {rec.标签 && <span className="text-[10px] text-[var(--text3)]">{rec.标签}</span>}
              {rec.最佳发布时间 && (
                <span className="text-[10px] text-green-600 ml-auto">
                  最佳发布 {rec.最佳发布时间}
                </span>
              )}
              {rec.字数 && <span className="text-[10px] text-[var(--text3)]">{rec.字数}字</span>}
            </div>
          </div>
        ))}
      </div>
    );
  }

  // Fallback: render as plain text
  return (
    <div className="mt-2 bg-gray-50 rounded-lg p-3 text-xs text-[var(--text2)] whitespace-pre-wrap font-mono max-h-48 overflow-y-auto">
      {raw || "(empty)"}
    </div>
  );
}

/* ── DagNextSteps: context-aware CTA once the DAG finishes ──────── */

const TERMINAL = ["completed", "failed", "cancelled"];

function DagNextSteps({ tasks }: { tasks: DagTask[] }) {
  if (tasks.length === 0 || !tasks.every(t => TERMINAL.includes(t.status))) {
    return null;  // still running — no CTA yet
  }
  const completedTypes = new Set(
    tasks.filter(t => t.status === "completed").map(t => t.type)
  );

  // Map completed task types → where the user goes to consume the output.
  // analyst+intel both surface on 市场洞察, so collapse to one link.
  const steps: { href: string; label: string }[] = [];
  if (completedTypes.has("content")) {
    steps.push({ href: "/admin/content", label: "✍️ 去内容创作页查看 / 发布生成的笔记" });
  }
  if (completedTypes.has("analyst")) {
    steps.push({ href: "/admin/insight", label: "📊 去市场洞察查看分析报告" });
  } else if (completedTypes.has("intel")) {
    steps.push({ href: "/admin/insight", label: "🔍 去市场洞察查看采集数据" });
  }

  const allFailed = completedTypes.size === 0;

  return (
    <div className="mt-4 bg-white border border-[var(--border)] rounded-xl overflow-hidden">
      <div className="px-4 py-3 border-b border-[var(--border)] text-sm font-medium text-[var(--text1)]">
        {allFailed ? "⚠️ 执行结束" : "✅ 执行完毕 · 下一步"}
      </div>
      <div className="px-4 py-3">
        {allFailed ? (
          <p className="text-sm text-[var(--text2)]">
            本次没有成功完成的任务。展开上方各任务的「查看错误」排查后可点「🔁 重试」。
          </p>
        ) : (
          <>
            <div className="flex flex-wrap gap-2">
              {steps.map(s => (
                <Link key={s.href + s.label} href={s.href}
                  className="inline-flex items-center px-3 py-2 rounded-lg text-sm font-medium border border-[var(--border)] hover:border-[var(--brand)] hover:text-[var(--brand)] transition-colors">
                  {s.label} →
                </Link>
              ))}
            </div>
            <p className="mt-3 text-xs text-[var(--text3)]">
              提示：点击各任务的「展开结果」可查看产出详情。
            </p>
          </>
        )}
      </div>
    </div>
  );
}

export default function ConsolePage() {
  const qc = useQueryClient();
  const { activeGoalId } = useGoalsStore();
  const [tab, setTab] = useState<Tab>("dag");
  const [intent, setIntent] = useState("");
  const [plan, setPlan] = useState<PlanNode[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [activeDagId, setActiveDagId] = useState<string | null>(null);
  const [expandedTask, setExpandedTask] = useState<string | null>(null);

  // ── Single-agent state ─────────────────────────────────────
  const [agentType, setAgentType] = useState<NodeType>("intel");
  const [agentPrompt, setAgentPrompt] = useState("");
  const [agentTaskId, setAgentTaskId] = useState<string | null>(null);

  const { data: dagStatus } = useQuery<DagStatus>({
    queryKey: ["dag", activeDagId],
    queryFn: () => apiFetch<DagStatus>(`/api/v1/dag/${activeDagId}`),
    enabled: !!activeDagId,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return 2000;
      return data.tasks.every(t => ["completed", "failed", "cancelled"].includes(t.status)) ? false : 2000;
    },
  });

  // ── Retry mutation ──────────────────────────────────────────

  const retryMut = useMutation({
    mutationFn: (nodeId: string) =>
      apiFetch<{ ok: boolean }>(`/api/v1/dag/${activeDagId}/retry/${nodeId}`, { method: "POST" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["dag", activeDagId] });
    },
  });

  function generatePlan() {
    if (!intent.trim()) return;
    setPlan(mockPlan(intent.trim()));
    setActiveDagId(null);
  }

  function updatePrompt(id: string, prompt: string) {
    setPlan(p => p.map(n => n.id === id ? { ...n, prompt } : n));
  }

  async function submitDag() {
    if (plan.length === 0) return;
    setSubmitting(true);
    setActiveDagId(null);
    try {
      const res = await apiFetch<{ dag_id: string }>("/api/v1/dag", {
        method: "POST",
        body: JSON.stringify({ plan }),
      });
      setActiveDagId(res.dag_id);
    } finally {
      setSubmitting(false);
    }
  }

  const TABS = [
    { key: "dag" as Tab, label: "🧩 DAG 多步" },
    { key: "single" as Tab, label: "💬 单步" },
  ];

  return (
    <div className="max-w-3xl mx-auto">
      <h1 className="text-xl font-bold text-[var(--text1)] mb-4">🤖 Agent Console</h1>

      <div className="flex gap-1 mb-5 bg-[var(--border)] rounded-lg p-1 w-fit">
        {TABS.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${tab === t.key ? "bg-white text-[var(--text1)] shadow-sm" : "text-[var(--text2)] hover:text-[var(--text1)]"}`}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === "dag" && (
        <div>
          {/* Intent input */}
          <div className="bg-white border border-[var(--border)] rounded-xl p-4 mb-4">
            <label className="block text-sm font-medium text-[var(--text1)] mb-2">运营意图</label>
            <div className="flex gap-2">
              <input className={`${INPUT_CLS} flex-1`} placeholder="例：为深圳工厂点位招商写一批内容" value={intent}
                onChange={e => setIntent(e.target.value)} onKeyDown={e => e.key === "Enter" && generatePlan()} />
              <Button variant="outline" size="sm" onClick={generatePlan}>生成计划</Button>
            </div>
          </div>

          {/* Plan table */}
          {plan.length > 0 && (
            <div className="bg-white border border-[var(--border)] rounded-xl overflow-hidden mb-4">
              <div className="px-4 py-3 border-b border-[var(--border)] flex items-center justify-between">
                <span className="text-sm font-medium text-[var(--text1)]">执行计划</span>
                <span className="text-xs text-[var(--text2)]">{plan.length} 个任务</span>
              </div>
              <div className="divide-y divide-[var(--border)]">
                {plan.map(node => (
                  <div key={node.id} className="px-4 py-3">
                    <div className="flex items-center gap-2 mb-1.5">
                      <span className="text-base">{TYPE_ICON[node.type] ?? "⚙️"}</span>
                      <span className="text-xs font-mono text-[var(--text3)]">{node.id}</span>
                      {node.blocked_by.length > 0 && (
                        <span className="text-xs text-[var(--text3)]">→ 依赖 {node.blocked_by.join(", ")}</span>
                      )}
                    </div>
                    <textarea className={`${INPUT_CLS} h-14 resize-none text-xs font-mono`} value={node.prompt}
                      onChange={e => updatePrompt(node.id, e.target.value)} />
                  </div>
                ))}
              </div>
              <div className="px-4 py-3 border-t border-[var(--border)]">
                <Button size="sm" disabled={submitting} onClick={submitDag}
                  style={{ background: "var(--brand)", color: "white" }}>
                  {submitting ? "提交中…" : "🚀 提交 DAG"}
                </Button>
              </div>
            </div>
          )}

          {/* DAG Status */}
          {activeDagId && dagStatus && (
            <div className="bg-white border border-[var(--border)] rounded-xl overflow-hidden">
              <div className="px-4 py-3 border-b border-[var(--border)] flex items-center justify-between">
                <span className="text-sm font-medium text-[var(--text1)]">执行状态</span>
                <span className="text-xs font-mono text-[var(--text3)]">{activeDagId}</span>
              </div>
              <div className="divide-y divide-[var(--border)]">
                {dagStatus.tasks.map(task => (
                  <div key={task.id} className="px-4 py-3">
                    <div className="flex items-center gap-2 mb-1">
                      <span>{TYPE_ICON[task.type] ?? "⚙️"}</span>
                      <span className="text-sm font-medium text-[var(--text1)] flex-1">{task.id}</span>
                      <span className="text-xs px-2 py-0.5 rounded-full font-medium"
                        style={{ color: STATUS_COLOR[task.status], background: `${STATUS_COLOR[task.status]}18` }}>
                        {STATUS_LABEL[task.status] ?? task.status}
                      </span>
                      {/* Retry button for failed tasks */}
                      {task.status === "failed" && (
                        <Button size="xs" variant="outline"
                          onClick={() => retryMut.mutate(task.id)}
                          disabled={retryMut.isPending}>
                          🔁 重试
                        </Button>
                      )}
                    </div>

                    {/* Error display for failed/cancelled tasks */}
                    {(task.status === "failed" || task.status === "cancelled") && task.result && (
                      <div>
                        <button className="text-xs text-[var(--brand)] underline-offset-2 hover:underline"
                          onClick={() => setExpandedTask(expandedTask === task.id ? null : task.id)}>
                          {expandedTask === task.id ? "收起详情" : "查看错误"}
                        </button>
                        {expandedTask === task.id && (
                          <div className="mt-2 bg-red-50 border border-red-200 rounded-lg p-3 text-xs">
                            <div className="text-red-700 font-medium mb-1">
                              {task.result.error_type || "Error"}: {task.result.error || "未知错误"}
                            </div>
                            {task.result.content && (
                              <div className="text-[var(--text2)] whitespace-pre-wrap line-clamp-6 mt-1">
                                {task.result.content}
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    )}

                    {task.status === "completed" && task.result && (
                      <div>
                        <button className="text-xs text-[var(--brand)] underline-offset-2 hover:underline"
                          onClick={() => setExpandedTask(expandedTask === task.id ? null : task.id)}>
                          {expandedTask === task.id ? "收起结果" : "展开结果"}
                        </button>
                        {expandedTask === task.id && (
                          <RenderResult content={task.result.content} taskType={task.type} />
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
              <div className="px-4 py-2 border-t border-[var(--border)] bg-gray-50 flex gap-3 text-xs text-[var(--text2)]">
                {Object.entries(dagStatus.summary).map(([k, v]) => (
                  <span key={k}><span style={{ color: STATUS_COLOR[k] }}>●</span> {STATUS_LABEL[k] ?? k} {v}</span>
                ))}
              </div>
            </div>
          )}

          {/* Next-step CTA: shown once every task reaches a terminal state */}
          {activeDagId && dagStatus && <DagNextSteps tasks={dagStatus.tasks} />}

          {plan.length === 0 && (
            <div className="text-center py-16 text-[var(--text2)]">
              <div className="text-4xl mb-3">🧩</div>
              <p className="text-sm font-medium text-[var(--text1)] mb-1">DAG 多步协作</p>
              <p className="text-xs">输入运营意图，自动生成 Intel → Analyst → Content 执行计划</p>
            </div>
          )}
        </div>
      )}

      {tab === "single" && (
        <div>
          {/* Agent type + prompt */}
          <div className="bg-white border border-[var(--border)] rounded-xl p-4 mb-4 space-y-3">
            <label className="block text-sm font-medium text-[var(--text1)]">Agent 类型</label>
            <div className="flex gap-2">
              {(["intel", "analyst", "content"] as const).map(t => (
                <button key={t} onClick={() => setAgentType(t)}
                  className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${agentType === t ? "bg-[var(--brand)] text-white" : "bg-gray-100 text-[var(--text2)] hover:bg-gray-200"}`}>
                  {TYPE_ICON[t]} {t === "intel" ? "情报" : t === "analyst" ? "分析" : "内容"}
                </button>
              ))}
            </div>
            <div>
              <label className="block text-sm font-medium text-[var(--text1)] mb-1">指令</label>
              <textarea className={`${INPUT_CLS} h-24 resize-none`} placeholder={`例：搜索深圳自助售卖机点位招商相关内容`} value={agentPrompt} onChange={e => setAgentPrompt(e.target.value)} />
            </div>
            <Button size="sm" disabled={!agentPrompt.trim() || !!agentTaskId} style={{ background: "var(--brand)", color: "white" }}
              onClick={async () => {
                setAgentTaskId(null);
                const res = await apiFetch<{ task_id: string }>("/api/v1/agent/submit", {
                  method: "POST",
                  body: JSON.stringify({ agent_type: agentType, prompt: agentPrompt, goal_id: activeGoalId }),
                });
                setAgentTaskId(res.task_id);
              }}>
              🚀 提交任务
            </Button>
          </div>

          {/* Polling result */}
          {agentTaskId && <AgentResultCard taskId={agentTaskId} onDone={() => setAgentTaskId(null)} />}
        </div>
      )}
    </div>
  );
}
