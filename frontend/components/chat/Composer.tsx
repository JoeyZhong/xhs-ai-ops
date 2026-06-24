"use client";
import { Target, ArrowUp } from "lucide-react";

export type ComposerProps = {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  goalId: string | null;
  onGoalChange: (id: string | null) => void;
  variant: "hero" | "docked";
  goals?: { id: string; name: string }[];
};

export function Composer({
  value,
  onChange,
  onSend,
  goalId,
  onGoalChange,
  variant,
  goals,
}: ComposerProps) {
  const currentGoal = goals?.find((g) => g.id === goalId);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (value.trim()) onSend();
    }
  };

  const containerClass =
    variant === "hero"
      ? "w-full bg-[var(--card-warm)] border border-[var(--border2)] rounded-2xl px-4 py-4 shadow-[0_6px_24px_rgba(60,50,30,.06)] transition-shadow focus-within:shadow-[0_0_0_4px_var(--ring-brand),0_6px_24px_rgba(60,50,30,.06)]"
      : "w-full bg-[var(--card-warm)] border border-[var(--border)] rounded-xl px-4 py-3";

  return (
    <div className={containerClass}>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        rows={variant === "hero" ? 1 : 1}
        placeholder={
          variant === "hero"
            ? "描述你的运营意图，例如：这周帮我规划 3 篇面向深圳工厂物业的内容…"
            : "输入你的运营意图…"
        }
        className="w-full border-none outline-none resize-none bg-transparent text-[15.5px] text-[var(--text1)] placeholder:text-[var(--text3)] leading-relaxed"
      />
      <div className="flex items-center justify-between mt-2.5">
        <div className="flex items-center gap-2">
          {goals && goals.length > 0 ? (
            <span className="inline-flex items-center gap-1.5 text-xs text-[var(--text2)] bg-[var(--bg)] border border-[var(--border)] rounded-lg pl-2.5 pr-2 py-1.5">
              <Target className="w-3.5 h-3.5 shrink-0" />
              <select
                value={goalId ?? ""}
                onChange={(e) => onGoalChange(e.target.value || null)}
                className="bg-transparent border-none outline-none appearance-none cursor-pointer text-xs text-[var(--text2)] pr-1"
              >
                <option value="">全部目标</option>
                {goals.map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.name}
                  </option>
                ))}
              </select>
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 text-xs text-[var(--text2)] bg-[var(--bg)] border border-[var(--border)] rounded-lg px-2.5 py-1.5">
              <Target className="w-3.5 h-3.5 shrink-0" />
              当前目标：{currentGoal?.name ?? "未选择"}
            </span>
          )}
          {variant === "hero" && (
            <span className="text-xs text-[var(--text3)] hidden sm:inline">
              Enter 发送 · Shift+Enter 换行
            </span>
          )}
        </div>
        <button
          onClick={() => {
            if (value.trim()) onSend();
          }}
          disabled={!value.trim()}
          aria-label="发送"
          className="w-9 h-9 rounded-xl bg-[var(--brand)] text-white flex items-center justify-center disabled:opacity-40 transition-opacity cursor-pointer border-none shrink-0"
        >
          <ArrowUp className="w-[18px] h-[18px]" />
        </button>
      </div>
    </div>
  );
}
