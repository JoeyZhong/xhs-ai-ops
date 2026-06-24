"use client";
import React from "react";
import { Search, BarChart3, Pencil, Calendar } from "lucide-react";
import { Composer } from "./Composer";

export type EmptyStateProps = {
  goalId: string | null;
  onGoalChange: (id: string | null) => void;
  onSend: (message: string) => void;
  goals?: { id: string; name: string }[];
};

function getGreeting(): string {
  const hour = new Date().getHours();
  if (hour < 6) return "夜深了";
  if (hour < 12) return "早上好";
  if (hour < 14) return "中午好";
  if (hour < 18) return "下午好";
  if (hour < 21) return "晚上好";
  return "夜深了";
}

// 三智能体语义灯条：恒定展示的能力提示（intel/analyst/content 三子 agent 始终可被编排），
// 颜色仅作强调、语义由文字承载（色盲安全），不接实时健康数据。
const AGENT_LEDS = [
  { label: "采集", color: "var(--agent-intel)" },
  { label: "分析", color: "var(--agent-analyst)" },
  { label: "生成", color: "var(--agent-content)" },
];

// 跨目标通用引导，不绑定具体业务；「当前目标」由后端按激活目标上下文解读。
const DEFAULT_CHIPS: { icon: React.ComponentType<{ className?: string }>; label: string }[] = [
  { icon: Search, label: "采集当前目标的竞品笔记" },
  { icon: BarChart3, label: "分析最近哪类内容互动最高" },
  { icon: Pencil, label: "围绕当前目标规划 3 篇内容" },
  { icon: Calendar, label: "看看这周的发布安排" },
];

export function EmptyState({
  goalId,
  onGoalChange,
  onSend,
  goals,
}: EmptyStateProps) {
  const greeting = getGreeting();
  const [composerValue, setComposerValue] = React.useState("");

  const handleChipClick = (label: string) => {
    onSend(label);
  };

  const handleComposerSend = () => {
    if (composerValue.trim()) {
      onSend(composerValue.trim());
      setComposerValue("");
    }
  };

  return (
    <div className="flex-1 flex flex-col items-center justify-center px-6 max-w-[720px] mx-auto w-full">
      <h1 className="text-[34px] font-medium tracking-[.01em] mb-2.5 text-center font-[Georgia,'Noto_Serif_SC',serif]">
        {greeting}
      </h1>
      <p className="text-[15px] text-[var(--text2)] mb-5 text-center leading-relaxed">
        一句话说出运营意图，主助手会替你动态调度下面三个智能体协作完成。
      </p>

      {/* 三智能体状态灯条（B 布局）：恒定能力提示，非实时监控 */}
      <div className="flex items-center gap-4 mb-6 text-[12.5px] text-[var(--text2)]">
        {AGENT_LEDS.map((led) => (
          <span key={led.label} className="inline-flex items-center gap-1.5">
            <span
              className="relative w-[7px] h-[7px] rounded-full"
              style={{ background: led.color }}
            >
              <span
                className="absolute -inset-[3px] rounded-full opacity-[.28]"
                style={{ background: led.color }}
              />
            </span>
            {led.label}
          </span>
        ))}
        <span className="text-[var(--text3)]">· 3 个智能体就绪</span>
      </div>

      <Composer
        value={composerValue}
        onChange={setComposerValue}
        onSend={handleComposerSend}
        goalId={goalId}
        onGoalChange={onGoalChange}
        variant="hero"
        goals={goals}
      />

      <div className="flex flex-wrap gap-2.5 justify-center mt-5">
        {DEFAULT_CHIPS.map((chip) => {
          const Icon = chip.icon;
          return (
            <button
              key={chip.label}
              onClick={() => handleChipClick(chip.label)}
              className="inline-flex items-center gap-2 text-[13.5px] text-[var(--text1)] bg-[var(--card-warm)] border border-[var(--border2)] rounded-full px-4 py-2 cursor-pointer transition-colors hover:border-[var(--brand-mid)] hover:bg-[var(--brand-light)]"
            >
              <Icon className="w-[15px] h-[15px] text-[var(--text2)]" />
              {chip.label}
            </button>
          );
        })}
      </div>

      {/* 状态脚注（B 布局）：操作台式三段，取代纯说明文案 */}
      <div className="flex items-center gap-2.5 text-xs text-[var(--text3)] mt-7 text-center">
        <span>
          主助手按意图动态编排{" "}
          <b className="text-[var(--text2)] font-semibold">采集 → 分析 → 生成</b>
        </span>
        <span className="w-[3px] h-[3px] rounded-full bg-[var(--border2)] shrink-0" />
        <span>
          <b className="text-[var(--text2)] font-semibold">真流式</b>协调
        </span>
        <span className="w-[3px] h-[3px] rounded-full bg-[var(--border2)] shrink-0" />
        <span>全程可暂停追问</span>
      </div>
    </div>
  );
}
