# Tasks · 首页视觉刷新（暖纸视觉 + 操作台布局）

> 视觉基线 `.mockup/homepage-redesign.html`。前端无 JS 测试 runner，验证 = `tsc --noEmit` + `eslint` + `next build` + 预览人工核。
> ⚠️ 改路由/layout 无关；写码前仍读 `frontend/AGENTS.md` 铁律。
> **铁律：不用 emoji**——图标统一 `lucide-react`（已装）；现有 emoji 一并清掉（用户明确要求，见 memory `feedback_no_emoji_in_ui`）。
>
> **落地说明（2026-06-11）**：3 处微调，详见提案/实现说明——
> ① mockup 的 `--ring` 与 shadcn 已有 `--ring`（全局 `outline-ring/50`）冲突 → 改用 `--ring-brand`；
> ② Composer 目标选择器是 `<select>`，`<option>` 塞不进 SVG → Target 图标移到 select 外侧同排；
> ③ T0.4 去 emoji 仅覆盖 **chat-first 首页 + 对话流**（本 change 范围）；`/admin/*` 后台与 `Sidebar` 的 emoji 属另一独立任务，本次不动。

## T0 · 去 emoji（全局换 lucide-react 线性图标）
- [x] T0.1 `EmptyState.tsx`：问候去手势 emoji（纯文字）；4 个 chips 的 `🔍/📊/✍️/📅` → `Search/BarChart3/Pencil/Calendar`；脚注里的 emoji 去掉。
- [x] T0.2 `Composer.tsx`：目标选择 `🎯` → `Target`；发送键 `↑` → `ArrowUp`。
- [x] T0.3 `ChatTopbar.tsx`：`⚙️ 管理后台` → `Settings` 图标 + 文字（头像「铺」是文字，保留）。
- [x] T0.4 chat-first 表面（`EmptyState`/`Composer`/`ChatTopbar`/`bubbles`）emoji 清零（grep 验证）。`/admin/*` + `Sidebar` 后台 emoji 属独立任务、本次不在范围。

## T1 · 主题 token（`globals.css`）
- [x] T1.1 `:root` 新增 `--ring-brand`、`--agent-intel`、`--agent-analyst`、`--agent-content`；`--bg` 保守不动（保留现值 `#faf9f5`，不全站偏移底色）。
- [x] T1.2 `@theme inline` 注册新增色变量（`--color-agent-*`，与现有 `--color-*` 风格一致）。
- [x] T1.3 `next build` 通过，现有页面无样式崩坏。

## T2 · 三智能体状态灯条（`EmptyState.tsx`）
- [x] T2.1 在副标题与 `<Composer>` 之间插入灯条：三段（采集/分析/生成）各一个 7px 圆点（`--agent-*` 色 + .28 柔晕）+ 文字 + 「· 3 个智能体就绪」。
- [x] T2.2 副标题文案微调为「…主助手会替你动态调度下面三个智能体协作完成。」
- [x] T2.3 颜色仅作强调，语义由文字承载（色盲安全）；恒定展示、不接实时数据。

## T3 · 状态脚注（`EmptyState.tsx`）
- [x] T3.1 底部「主助手会自动编排 采集 → 分析 → 生成 …」整句改为小圆点分隔的状态脚注：
      `动态编排 采集→分析→生成 · 真流式协调 · 可暂停追问`（无 emoji）。

## T4 · 输入框柔光圈（`Composer.tsx`，仅 hero 变体）
- [x] T4.1 `hero` 变体暖卡 `focus-within` 时加 `box-shadow: 0 0 0 4px var(--ring-brand)`；`docked` 变体不变。
- [x] T4.2 保留 textarea 默认焦点可辨识性（不破坏无障碍）。

## T5 · 验收
- [x] T5.1 `cd frontend && npx tsc --noEmit && npx eslint . && npx next build` 全绿（tsc 0 err · eslint 0 err · build 34 路由全过）。
- [x] T5.2 用户本地 `localhost:3000` 预览验收通过：灯条/柔光圈/状态脚注到位，空状态发一句仍正常进对话流。
- [x] T5.3 经用户本地实时预览口头验收（2026-06-11）；未单独存档截图文件。

## T6 · Commit + 归档
- [x] T6.1 `feat(chat): warm-cockpit homepage refresh (agent strip + status foot + focus ring)`
- [x] T6.2 用户验收通过（2026-06-11）→ 目录移 `archive/2026-06-11-homepage-visual-refresh/`；STATUS 标完成（纯视觉，无 spec 合入）。
