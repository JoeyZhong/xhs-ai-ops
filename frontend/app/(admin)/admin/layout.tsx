"use client";

import { useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/Sidebar";
import { useQuery } from "@tanstack/react-query";
import { apiFetch, getToken } from "@/lib/api";

interface CookieStatus {
  valid: boolean;
  age_minutes?: number;
}

export default function MainLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();

  // 未登录 → 跳 /login
  useEffect(() => {
    if (!getToken()) router.replace("/login");
  }, [router]);

  const { data: cookieStatus } = useQuery<CookieStatus>({
    queryKey: ["cookieStatus"],
    queryFn: () => apiFetch<CookieStatus>("/api/v1/settings/cookie/status"),
    refetchInterval: 60_000,
    retry: false,
    staleTime: 30_000,
  });

  const { data: draftCount } = useQuery<{ count: number }>({
    queryKey: ["playbook-drafts-count"],
    queryFn: () => apiFetch<{ count: number }>("/api/v1/playbook/drafts/count"),
    refetchInterval: 60_000,
    retry: false,
    staleTime: 30_000,
  });

  const cookieInvalid = cookieStatus && !cookieStatus.valid;
  const hasDrafts = draftCount && draftCount.count > 0;

  return (
    <div className="flex h-full w-full overflow-hidden">
      <Sidebar cookieStatus={cookieStatus} />
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Global Alert Banner */}
        {cookieInvalid && (
          <Link
            href="/admin/settings"
            className="shrink-0 bg-red-600 text-white px-6 py-2 flex items-center gap-3 hover:bg-red-700 transition-colors"
          >
            <span className="text-sm">
              🍪 Cookie 已失效，点击此处前往设置
            </span>
            <span className="text-xs underline hover:no-underline ml-auto">
              前往设置 →
            </span>
          </Link>
        )}
        {hasDrafts && (
          <div className="shrink-0 bg-amber-500 text-white px-6 py-2 flex items-center gap-3">
            <span className="text-sm">
              📖 Playbook 有 <strong>{draftCount!.count}</strong> 条待审阅 draft
            </span>
            <Link
              href="/admin/playbook"
              className="text-xs underline hover:no-underline ml-auto"
            >
              前往审阅 →
            </Link>
          </div>
        )}
        <main className="flex-1 overflow-y-auto p-6 bg-[var(--bg)]">
          {children}
        </main>
      </div>
    </div>
  );
}
