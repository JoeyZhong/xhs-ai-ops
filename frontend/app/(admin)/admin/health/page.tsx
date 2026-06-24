"use client";

import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

interface HealthResponse {
  status: string;
  version: string;
}

export default function HealthPage() {
  const { data, isLoading, error } = useQuery<HealthResponse>({
    queryKey: ["health"],
    queryFn: () => apiFetch<HealthResponse>("/api/v1/health"),
    refetchInterval: 10_000,
  });

  return (
    <div className="max-w-lg">
      <h1 className="text-xl font-bold mb-4">后端健康检查</h1>

      {isLoading && (
        <div className="text-[var(--text2)]">连接中…</div>
      )}

      {error && (
        <div className="rounded-lg border border-[var(--color-failed)] bg-red-50 p-4 text-sm text-[var(--color-failed)]">
          ❌ 连接失败：{(error as Error).message}
          <p className="mt-1 text-xs text-[var(--text2)]">
            请确认 FastAPI 已在 :8000 运行（python -m uvicorn server.main:app --port 8000）
          </p>
        </div>
      )}

      {data && (
        <div className="rounded-lg border border-[var(--color-completed)] bg-green-50 p-4">
          <div className="flex items-center gap-2 text-[var(--color-completed)] font-medium">
            <span>●</span>
            <span>后端运行正常</span>
          </div>
          <dl className="mt-3 text-sm space-y-1 text-[var(--text2)]">
            <div className="flex gap-3">
              <dt className="font-mono w-20">status</dt>
              <dd>{data.status}</dd>
            </div>
            <div className="flex gap-3">
              <dt className="font-mono w-20">version</dt>
              <dd>{data.version}</dd>
            </div>
          </dl>
        </div>
      )}
    </div>
  );
}
