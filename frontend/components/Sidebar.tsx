"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Radar } from "lucide-react";
import { useGoalsStore } from "@/stores/goals";
import { apiFetch } from "@/lib/api";

const NAV_ITEMS: { href: string; icon: string | ReactNode; label: string }[] = [
  { href: "/admin/goals",       icon: "🎯", label: "目标对齐" },
  { href: "/admin/insight",     icon: "📊", label: "市场洞察" },
  { href: "/admin/leads",       icon: <Radar size={15} />, label: "线索雷达" },
  { href: "/admin/topics",      icon: "📋", label: "选题策划" },
  { href: "/admin/content",     icon: "✍️",  label: "内容创作" },
  { href: "/admin/drafts",      icon: "🗂", label: "草稿箱" },
  { href: "/admin/packaging",   icon: "🎨", label: "包装设计" },
  { href: "/admin/skills",      icon: "🧠", label: "技能中枢" },
  { href: "/admin/analytics",   icon: "📈", label: "数据追踪" },
  { href: "/admin/console",     icon: "🤖", label: "Agent Console" },
  { href: "/admin/playbook",    icon: "📖", label: "Playbook 审阅" },
  { href: "/admin/scheduler",   icon: "📅", label: "自动化任务" },
  { href: "/admin/personas",    icon: "🎭", label: "人设管理" },
  { href: "/admin/settings",    icon: "⚙️",  label: "API 配置" },
];

interface CookieStatus { valid: boolean; age_minutes?: number }

function CookieBadge({ status }: { status?: CookieStatus }) {
  if (!status) return <span className="text-xs text-white/30">检测中…</span>;
  const { valid, age_minutes = 0 } = status;
  if (!valid) return <span className="text-xs text-[var(--color-failed)]">● Cookie 已失效</span>;
  if (age_minutes > 360) return <span className="text-xs text-[var(--color-cookie-warn)]">● Cookie 较旧</span>;
  return <span className="text-xs text-[var(--color-completed)]">● Cookie 有效</span>;
}

export function Sidebar({ cookieStatus }: { cookieStatus?: CookieStatus }) {
  const pathname = usePathname();
  const { activeGoalId, activeGoalName } = useGoalsStore();
  const { data: draftCount } = useQuery<{ count: number }>({
    queryKey: ["playbook-drafts-count"],
    queryFn: () => apiFetch<{ count: number }>("/api/v1/playbook/drafts/count"),
    refetchInterval: 60_000,
    retry: false,
  });

  return (
    <aside
      className="w-[220px] flex-shrink-0 flex flex-col overflow-y-auto"
      style={{ background: "var(--sidebar-bg)" }}
    >
      {/* Logo */}
      <div className="px-4 py-4 border-b border-white/[0.06]">
        <div className="flex items-center gap-2">
          <div
            className="w-7 h-7 rounded-lg flex items-center justify-center text-white font-bold text-sm"
            style={{ background: "var(--brand)", fontFamily: "var(--font-mono)" }}
          >
            S
          </div>
          <div>
            <div className="text-[13px] font-bold text-white">Spider_XHS</div>
            <div className="text-[10px] text-white/35">内容运营平台</div>
          </div>
        </div>
        <Link
          href="/"
          className="mt-3 flex items-center gap-2 rounded-lg border border-white/[0.08] px-3 py-2 text-[12px] text-white/65 transition-colors hover:bg-white/[0.07] hover:text-white"
        >
          <span>←</span>
          <span>返回主助手</span>
        </Link>
      </div>

      {/* 当前目标 */}
      <div className="px-3 py-3">
        <div className="text-[10px] text-white/30 uppercase tracking-widest mb-1.5 font-mono">
          当前目标
        </div>
        <div className="text-[12px] text-white/70 bg-white/[0.06] rounded-lg px-2.5 py-1.5 truncate" title={activeGoalId}>
          {activeGoalName || activeGoalId}
        </div>
      </div>

      {/* 导航 */}
      <nav className="flex-1 px-2 pb-2">
        {NAV_ITEMS.map(({ href, icon, label }) => {
          const active = pathname === href || pathname.startsWith(href + "/");
          const isPlaybook = href === "/admin/playbook";
          const unread = isPlaybook && draftCount && draftCount.count > 0 ? draftCount.count : 0;
          return (
            <Link
              key={href}
              href={href}
              className={`flex items-center gap-2.5 px-3 py-2 rounded-lg text-[13px] mb-0.5 transition-colors ${
                active
                  ? "bg-[var(--brand)] text-white"
                  : "text-white/60 hover:bg-white/[0.07] hover:text-white"
              }`}
            >
              <span className="flex items-center justify-center w-[18px]">{icon}</span>
              <span className="flex-1">{label}</span>
              {unread > 0 && (
                <span className="text-[10px] min-w-[18px] h-[18px] px-1 rounded-full bg-[var(--brand)] text-white flex items-center justify-center font-bold">
                  {unread}
                </span>
              )}
            </Link>
          );
        })}
      </nav>

      {/* Cookie 状态 */}
      <div className="px-4 py-3 border-t border-white/[0.06]">
        <CookieBadge status={cookieStatus} />
      </div>
    </aside>
  );
}
