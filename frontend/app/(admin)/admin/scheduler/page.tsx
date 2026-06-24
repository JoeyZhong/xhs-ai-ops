"use client";

import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

interface SchedulerJob {
  id: string;
  name: string;
  trigger: string;
  next_run_time: string | null;
}

interface SchedulerStatus {
  running: boolean;
  started?: boolean;
  jobs: SchedulerJob[];
}

function formatNextRun(iso: string | null): string {
  if (!iso) return "未调度";
  const d = new Date(iso);
  const now = new Date();
  const diffMs = d.getTime() - now.getTime();
  const diffMins = Math.round(diffMs / 60000);
  const diffHours = Math.round(diffMs / 3600000);

  if (diffMs < 0) return "已过期";
  if (diffMins < 60) return `${diffMins} 分钟后`;
  if (diffHours < 24) return `${diffHours} 小时后`;

  return d.toLocaleString("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/* ── Job Card ───────────────────────────────────────────────────────────── */

function JobCard({ job }: { job: SchedulerJob }) {
  return (
    <div className="bg-white border border-[var(--border)] rounded-xl px-4 py-3 flex items-center gap-4">
      <div className="w-10 h-10 rounded-lg bg-[var(--brand)]/10 flex items-center justify-center text-[var(--brand)] text-lg shrink-0">
        ⏰
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-[var(--text1)]">{job.name}</p>
        <p className="text-xs text-[var(--text2)] font-mono mt-0.5">{job.trigger}</p>
      </div>
      <div className="text-right shrink-0">
        <p className="text-xs text-[var(--text2)]">下次触发</p>
        <p className="text-sm font-medium text-[var(--brand)]">{formatNextRun(job.next_run_time)}</p>
      </div>
    </div>
  );
}

/* ── Main Page ──────────────────────────────────────────────────────────── */

export default function SchedulerPage() {
  const { data, isLoading, isError, refetch } = useQuery<SchedulerStatus>({
    queryKey: ["scheduler-status"],
    queryFn: () => apiFetch<SchedulerStatus>("/api/v1/scheduler/status"),
    refetchInterval: 30_000,
  });

  const isRunning = data?.running ?? false;

  return (
    <div className="max-w-2xl mx-auto">
      <div className="flex items-center justify-between mb-5">
        <h1 className="text-xl font-bold text-[var(--text1)]">自动化任务</h1>
        <button
          onClick={() => refetch()}
          className="text-xs text-[var(--text2)] hover:text-[var(--brand)] transition-colors"
        >
          🔄 刷新
        </button>
      </div>

      {/* Status Banner */}
      <div
        className={`rounded-xl px-4 py-3 mb-5 flex items-center gap-3 ${
          isRunning
            ? "bg-green-50 border border-green-200"
            : "bg-amber-50 border border-amber-200"
        }`}
      >
        <div
          className={`w-2.5 h-2.5 rounded-full ${
            isRunning ? "bg-green-500" : "bg-amber-500"
          }`}
        />
        <div className="flex-1">
          <p className={`text-sm font-medium ${isRunning ? "text-green-800" : "text-amber-800"}`}>
            {isRunning ? "调度器运行中" : "调度器未启动"}
          </p>
          <p className="text-xs text-[var(--text2)] mt-0.5">
            {isRunning
              ? `已注册 ${data?.jobs.length ?? 0} 个定时任务`
              : "在 config/settings.json 中设置 scheduler.enabled: true 后重启 FastAPI"}
          </p>
        </div>
      </div>

      {isLoading && (
        <div className="text-center py-16 text-sm text-[var(--text2)]">加载中…</div>
      )}

      {isError && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700">
          加载失败，请检查后端是否启动
        </div>
      )}

      {/* Jobs List */}
      {data && data.jobs.length > 0 && (
        <div className="space-y-2">
          <div className="text-[10px] text-white/30 uppercase tracking-widest mb-2 font-mono">
            已注册任务
          </div>
          {data.jobs.map((job) => (
            <JobCard key={job.id} job={job} />
          ))}
        </div>
      )}

      {data && data.jobs.length === 0 && isRunning && (
        <div className="text-center py-12 text-[var(--text2)]">
          <p className="text-sm">暂无注册任务</p>
          <p className="text-xs mt-1">调度器已启动但未注册任何 cron job</p>
        </div>
      )}

      {/* Info */}
      <div className="mt-6 bg-white border border-[var(--border)] rounded-xl p-4">
        <p className="text-sm font-medium text-[var(--text1)] mb-2">关于自动化任务</p>
        <ul className="text-xs text-[var(--text2)] space-y-1.5">
          <li>• 每日 06:00 — Cookie 健康检查（自动检测凭证是否失效）</li>
          <li>• 每周一 09:00 — Analyst 周报生成（自动写入 playbook draft）</li>
          <li>• 生成后的 draft 需在 <a href="/admin/playbook" className="text-[var(--brand)] hover:underline">Playbook 审阅</a> 页面手动采纳后方可生效</li>
        </ul>
      </div>
    </div>
  );
}
