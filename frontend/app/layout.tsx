import type { Metadata } from "next";
import { Providers } from "@/lib/providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "Spider_XHS · 内容运营平台",
  description: "小红书全链路 AI 内容运营平台",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="zh-CN"
      className="h-full"
    >
      <body className="h-full flex overflow-hidden bg-[var(--bg)] text-[var(--text1)]">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
