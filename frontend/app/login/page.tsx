"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { setToken } from "@/lib/api";
import { resetGoalsStore } from "@/stores/goals";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [jwt, setJwt] = useState("");
  const [err, setErr] = useState("");
  const initialError = params.get("error");

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr("");
    if (!jwt.trim()) {
      setErr("请粘贴 JWT");
      return;
    }
    if (jwt.split(".").length !== 3) {
      setErr("JWT 格式不对（应为 xxx.yyy.zzz 三段）");
      return;
    }
    setToken(jwt.trim());
    resetGoalsStore();
    router.push("/goals");
  };

  return (
    <div style={{ maxWidth: 600, margin: "10vh auto", padding: 24 }}>
      <h1>Spider_XHS · 登录</h1>
      <p>粘贴管理员发给你的 JWT（24 小时有效；过期联系管理员重签）。</p>
      {initialError === "token" && (
        <p style={{ color: "red" }}>登录已过期或 token 无效，请重新粘贴</p>
      )}
      <form onSubmit={submit}>
        <textarea
          value={jwt}
          onChange={(e) => setJwt(e.target.value)}
          rows={4}
          style={{ width: "100%", fontFamily: "monospace", fontSize: 12 }}
          placeholder="eyJhbGciOiJIUzI1NiIs..."
        />
        {err && <p style={{ color: "red" }}>{err}</p>}
        <button type="submit" style={{ marginTop: 12, padding: "8px 16px" }}>
          登录
        </button>
      </form>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<div style={{ padding: 24 }}>加载中…</div>}>
      <LoginForm />
    </Suspense>
  );
}
