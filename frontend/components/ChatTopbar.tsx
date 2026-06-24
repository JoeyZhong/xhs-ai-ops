"use client";
import Link from "next/link";
import { Settings } from "lucide-react";

export function ChatTopbar() {
  return (
    <header className="flex items-center justify-between px-6 py-4 shrink-0">
      <Link href="/" className="flex items-center gap-2">
        <span className="w-6 h-6 rounded-lg bg-[var(--brand)] text-white text-xs font-bold grid place-items-center font-mono">
          S
        </span>
        <span className="text-sm font-semibold tracking-tight">Spider_XHS</span>
      </Link>
      <div className="flex items-center gap-3">
        <Link
          href="/admin/goals"
          className="inline-flex items-center gap-1.5 text-sm text-[var(--text2)] border border-[var(--border)] rounded-lg px-3 py-1.5 hover:text-[var(--text1)] hover:border-[var(--text3)] transition-colors"
        >
          <Settings className="w-4 h-4" />
          管理后台
        </Link>
        <span
          className="w-8 h-8 rounded-full grid place-items-center text-white text-xs font-semibold"
          style={{ background: "linear-gradient(135deg,#e8445a,#f59e0b)" }}
        >
          铺
        </span>
      </div>
    </header>
  );
}
