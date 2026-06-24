"use client";

import { type ReactNode, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiFetch, generateIdempotencyKey } from "@/lib/api";

/* ── Types ───────────────────────────────────────────────── */

interface PackagingRules {
  rules: string;
  updated_at: string;
}

/* ── Default rules (fallback, not source of truth) ──────── */

const DEFAULT_RULES = `# 小红书包装设计规则（运营可编辑）

> 此文件被 \`agent_tools/packaging_rules.py\` 读入并注入到所有内容生成 prompt。
> 修改后无需重启，LRU 缓存基于 mtime 自动失效。

## 五大爆文标题公式

1. **反直觉型** — 打破常识认知，制造信息差
   - 示例：「深圳学校自助机点位，他们不要钱，还要倒贴」
   - 写法：开头用「大多数人以为…实际上…」或「没人告诉你…」

2. **数字清单型** — 具体数字增加可信度，清单降低阅读门槛
   - 示例：「7个黄金点位判断标准，第3条90%的人看错了」
   - 写法：奇数列表更有传播力，数字要真实有依据

3. **本地汇总型** — 本地化信息搜集门槛高，读者愿意收藏备用
   - 示例：「2024深圳工厂区自助机最优点位地图（南山/宝安/龙岗）」
   - 写法：加上年份和区域，精准触达目标受众

4. **工具型** — 提供可直接使用的工具/表格/公式，收藏率高
   - 示例：「点位评分表：20分钟判断一个点位值不值（附模板）」
   - 写法：结尾提示「保存备用」或「收藏打印」提升互动

5. **焦虑共鸣型** — 描述目标读者正在经历的痛苦，引发强烈共鸣
   - 示例：「谈了3个月的工厂点位，被这一句话废了」
   - 写法：描述具体场景，不要抽象化；结尾给出解决方案

## CES 钩子规则（结尾必须埋）

CES 公式：点赞×1 + 收藏×1 + 评论×4 + 分享×4 + 关注×8

- **评论权重最高**：结尾必须有开放式提问引导评论
- **关注权重最高**：正文中段插入一句「我账号专门做 XX，关注不迷路」
- **收藏次之**：内容有清单/模板/数据表时，明示「保存备用」
- **避免**：单纯求赞、求转发等低权重引导

## 输出格式硬约束

- 主标题 ≤ 25 字
- 备选标题 2 条
- 正文 500-800 字
- 标签 3-8 个
- 最佳发布时间（工作日 12:00 或 20:30）`;

/* ── Markdown renderer (copied from content/page.tsx) ───── */

function renderMarkdown(raw: string): string {
  let html = raw.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/^### (.+)$/gm, '<h3 class="text-sm font-bold mt-3 mb-1">$1</h3>');
  html = html.replace(/^## (.+)$/gm, '<h2 class="text-base font-bold mt-4 mb-2">$1</h2>');
  html = html.replace(/^# (.+)$/gm, '<h1 class="text-lg font-bold mt-4 mb-2">$1</h1>');
  html = html.replace(/^- (.+)$/gm, '<li class="ml-4 list-disc">$1</li>');
  html = html.replace(/^\> (.+)$/gm, '<blockquote class="border-l-2 border-[var(--brand)] pl-3 italic text-[var(--text2)] my-2">$1</blockquote>');
  html = html.replace(/\n\n/g, "</p><p class='mb-2'>");
  html = html.replace(/^(.+)$/gm, (_m, p1) => p1.startsWith("<") || p1.startsWith("- ") ? p1 : `<p class="mb-2">${p1}</p>`);
  return html;
}

/* ── Page Component ─────────────────────────────────────── */

export default function PackagingEditorPage(): ReactNode {
  const qc = useQueryClient();

  const { data, isLoading, isError, refetch } = useQuery<PackagingRules>({
    queryKey: ["packaging-rules"],
    queryFn: () => apiFetch<PackagingRules>("/api/v1/packaging/rules"),
  });

  const [draftRules, setDraftRules] = useState<string | null>(null);
  const [showPreview, setShowPreview] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const rules = draftRules ?? data?.rules ?? "";
  const dirty = draftRules !== null && draftRules !== (data?.rules ?? "");

  /* ── Save mutation ────────────────────────────────────── */

  const saveMut = useMutation({
    mutationFn: (body: string) =>
      apiFetch<PackagingRules>("/api/v1/packaging/rules", {
        method: "PUT",
        headers: { "Idempotency-Key": generateIdempotencyKey() },
        body: JSON.stringify({ rules: body }),
      }),
    onSuccess: (saved) => {
      qc.setQueryData(["packaging-rules"], saved);
      setDraftRules(null);
      setSaveSuccess(true);
      setSaveError(null);
      qc.invalidateQueries({ queryKey: ["packaging-rules"] });
    },
    onError: (err: Error) => {
      setSaveSuccess(false);
      setSaveError(err instanceof ApiError ? err.message : "保存失败，请稍后重试");
    },
  });

  /* ── Handlers ─────────────────────────────────────────── */

  function handleRestoreDefault() {
    setDraftRules(DEFAULT_RULES);
    setSaveSuccess(false);
    setSaveError(null);
  }

  function handleSave() {
    setSaveError(null);
    setSaveSuccess(false);
    saveMut.mutate(rules);
  }

  /* ── Loading state ────────────────────────────────────── */

  if (isLoading) {
    return (
      <div className="max-w-3xl mx-auto">
        <h1 className="text-xl font-bold text-[var(--text1)] mb-6">包装设计规则</h1>
        <div className="text-[var(--text2)]">加载中...</div>
      </div>
    );
  }

  /* ── Error state ──────────────────────────────────────── */

  if (isError) {
    return (
      <div className="max-w-3xl mx-auto">
        <h1 className="text-xl font-bold text-[var(--text1)] mb-6">包装设计规则</h1>
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-800">
          <p className="mb-2">加载失败，请确认后端服务是否运行。</p>
          <button
            onClick={() => refetch()}
            className="underline hover:no-underline"
          >
            重试
          </button>
        </div>
      </div>
    );
  }

  /* ── Normal render ────────────────────────────────────── */

  const updatedAt = data?.updated_at
    ? new Date(data.updated_at).toLocaleString("zh-CN")
    : "";

  return (
    <div className="max-w-3xl mx-auto">
      <h1 className="text-xl font-bold text-[var(--text1)] mb-6">包装设计规则（运营可编辑）</h1>

      {/* Toast: save success */}
      {saveSuccess && (
        <div className="mb-4 bg-green-50 border border-green-200 rounded-lg px-4 py-3 text-sm text-green-800">
          已保存
        </div>
      )}

      {/* Toast: save error (422 etc.) */}
      {saveError && (
        <div className="mb-4 bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-800 whitespace-pre-wrap">
          {saveError}
        </div>
      )}

      {/* Textarea editor */}
      <textarea
        value={rules}
        onChange={(e) => {
          setDraftRules(e.target.value);
          setSaveSuccess(false);
          setSaveError(null);
        }}
        className="w-full font-mono text-sm border border-[var(--border)] rounded-xl p-4 h-96 resize-y focus:outline-none focus:ring-2 focus:ring-[var(--brand)]"
        spellCheck={false}
      />

      {/* Meta info & required-fields hint */}
      <div className="flex items-center justify-between mt-3 mb-4">
        <span className="text-xs text-[var(--text2)]">
          最后更新: {updatedAt}
          {dirty && <span className="ml-2 text-amber-600">（有未保存更改）</span>}
        </span>
        <span className="text-xs text-[var(--text2)]">
          必须包含「五大爆文标题公式」和「CES」
        </span>
      </div>

      {/* Action buttons */}
      <div className="flex gap-2">
        <button
          onClick={handleSave}
          disabled={saveMut.isPending || !dirty}
          className="px-4 py-2 bg-[var(--brand)] text-white rounded-lg text-sm font-medium hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {saveMut.isPending ? "保存中..." : "保存"}
        </button>
        <button
          onClick={handleRestoreDefault}
          className="px-4 py-2 border border-[var(--border)] rounded-lg text-sm text-[var(--text1)] hover:bg-gray-50"
        >
          恢复默认
        </button>
        <button
          onClick={() => setShowPreview(!showPreview)}
          className="px-4 py-2 border border-[var(--border)] rounded-lg text-sm text-[var(--text1)] hover:bg-gray-50"
        >
          {showPreview ? "编辑" : "预览"}
        </button>
      </div>

      {/* Markdown preview */}
      {showPreview && (
        <div className="mt-4 bg-white border border-[var(--border)] rounded-xl p-4">
          <div
            className="prose prose-sm max-w-none"
            dangerouslySetInnerHTML={{ __html: renderMarkdown(rules) }}
          />
        </div>
      )}
    </div>
  );
}
