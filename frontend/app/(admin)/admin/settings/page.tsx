"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";

const INPUT = "w-full border border-[var(--border)] rounded-lg px-3 py-2 text-sm outline-none focus:border-[var(--brand)] focus:ring-1 focus:ring-[var(--brand)] bg-white";
const CARD = "bg-white rounded-xl border border-[var(--border)] p-5 mb-4";
const LABEL = "block text-sm font-medium text-[var(--text1)] mb-1";

function SettingsContent() {
  const searchParams = useSearchParams();
  const tokenError = searchParams.get("error") === "token";
  const qc = useQueryClient();

  const [kimiKey, setKimiKey] = useState("");
  const [kimiModel, setKimiModel] = useState("moonshot-v1-32k");
  const [kimiSaving, setKimiSaving] = useState(false);
  const [kimiTestResult, setKimiTestResult] = useState<{ ok: boolean; message?: string; error?: string } | null>(null);

  const [accountId, setAccountId] = useState("default");
  const [cookieStr, setCookieStr] = useState("");
  const [cookieSaving, setCookieSaving] = useState(false);
  const [cookieSaved, setCookieSaved] = useState(false);

  const { data: cookieStatus } = useQuery({
    queryKey: ["cookieStatus"],
    queryFn: () => apiFetch<{ valid: boolean; count?: number; reason?: string }>("/api/v1/settings/cookie/status"),
    retry: false,
  });

  async function testKimi() {
    setKimiTestResult(null);
    try {
      const r = await apiFetch<{ ok: boolean; message?: string; error?: string }>("/api/v1/settings/kimi/test");
      setKimiTestResult(r);
    } catch {
      setKimiTestResult({ ok: false, error: "请求失败" });
    }
  }

  async function saveKimi() {
    if (!kimiKey.trim()) return;
    setKimiSaving(true);
    try {
      await apiFetch("/api/v1/settings/kimi", {
        method: "POST",
        body: JSON.stringify({ api_key: kimiKey, model: kimiModel }),
      });
      setKimiKey("");
      setKimiTestResult(null);
    } finally {
      setKimiSaving(false);
    }
  }

  async function saveCookie() {
    if (!cookieStr.trim()) return;
    setCookieSaving(true);
    setCookieSaved(false);
    try {
      await apiFetch("/api/v1/settings/cookie", {
        method: "POST",
        body: JSON.stringify({ account_id: accountId, cookie: cookieStr }),
      });
      setCookieSaved(true);
      setCookieStr("");
      qc.invalidateQueries({ queryKey: ["cookieStatus"] });
    } finally {
      setCookieSaving(false);
    }
  }

  return (
    <div className="max-w-2xl mx-auto">
      <h1 className="text-xl font-bold text-[var(--text1)] mb-6">⚙️ API 配置</h1>

      {tokenError && (
        <div className="mb-4 bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-700">
          API Token 配置错误，请检查 <code className="font-mono">NEXT_PUBLIC_API_TOKEN</code> 环境变量
        </div>
      )}

      {/* Kimi 配置 */}
      <div className={CARD}>
        <h2 className="text-base font-semibold text-[var(--text1)] mb-4">Kimi API 配置</h2>

        <div className="mb-3">
          <label className={LABEL}>API Key</label>
          <input
            type="password"
            className={INPUT}
            placeholder="sk-..."
            value={kimiKey}
            onChange={(e) => setKimiKey(e.target.value)}
          />
          <p className="text-xs text-[var(--text3)] mt-1">留空不更改现有 key</p>
        </div>

        <div className="mb-4">
          <label className={LABEL}>模型</label>
          <select
            className={INPUT}
            value={kimiModel}
            onChange={(e) => setKimiModel(e.target.value)}
          >
            <option value="moonshot-v1-8k">moonshot-v1-8k</option>
            <option value="moonshot-v1-32k">moonshot-v1-32k</option>
            <option value="moonshot-v1-128k">moonshot-v1-128k</option>
          </select>
        </div>

        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={testKimi}>
            测试连接
          </Button>
          <Button size="sm" disabled={kimiSaving || !kimiKey.trim()} onClick={saveKimi}>
            {kimiSaving ? "保存中…" : "保存 Key"}
          </Button>
          {kimiTestResult && (
            <span className={`text-sm ${kimiTestResult.ok ? "text-[var(--color-completed)]" : "text-[var(--color-failed)]"}`}>
              {kimiTestResult.ok ? "✓ 连接成功" : `✗ ${kimiTestResult.error ?? kimiTestResult.message ?? "连接失败"}`}
            </span>
          )}
        </div>
      </div>

      {/* Cookie 配置 */}
      <div className={CARD}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-semibold text-[var(--text1)]">小红书 Cookie</h2>
          {cookieStatus && (
            <span className={`text-xs px-2 py-0.5 rounded-full ${
              cookieStatus.valid
                ? "bg-green-50 text-[var(--color-completed)]"
                : "bg-red-50 text-[var(--color-failed)]"
            }`}>
              {cookieStatus.valid ? `✓ 有效 (${cookieStatus.count ?? "?"}条)` : "✗ 无效"}
            </span>
          )}
        </div>

        <div className="mb-3">
          <label className={LABEL}>账号 ID</label>
          <input
            className={INPUT}
            placeholder="default"
            value={accountId}
            onChange={(e) => setAccountId(e.target.value)}
          />
        </div>

        <div className="mb-4">
          <label className={LABEL}>Cookie 字符串</label>
          <textarea
            className={`${INPUT} h-28 resize-none font-mono text-xs`}
            placeholder="粘贴小红书 Cookie，格式：a=xxx; b=yyy; ..."
            value={cookieStr}
            onChange={(e) => setCookieStr(e.target.value)}
          />
        </div>

        <div className="flex items-center gap-2">
          <Button size="sm" disabled={cookieSaving || !cookieStr.trim()} onClick={saveCookie}>
            {cookieSaving ? "保存中…" : "保存 Cookie"}
          </Button>
          {cookieSaved && (
            <span className="text-sm text-[var(--color-completed)]">✓ 已保存</span>
          )}
        </div>

        <details className="mt-5 text-sm text-[var(--text2)]">
          <summary className="cursor-pointer font-medium text-[var(--text1)] select-none">
            📖 如何获取小红书 Cookie？
          </summary>
          <ol className="mt-3 space-y-1.5 pl-4 list-decimal text-xs leading-relaxed text-[var(--text2)]">
            <li>浏览器打开 <strong>www.xiaohongshu.com</strong> 并登录</li>
            <li>按 F12 → Application → Cookies → www.xiaohongshu.com</li>
            <li>复制所有 Cookie 拼接成 <code className="font-mono bg-gray-100 px-1 rounded">key=value; key2=value2</code> 格式</li>
            <li>粘贴到上方文本框，点击保存</li>
          </ol>
        </details>
      </div>
    </div>
  );
}

export default function SettingsPage() {
  return (
    <Suspense>
      <SettingsContent />
    </Suspense>
  );
}
