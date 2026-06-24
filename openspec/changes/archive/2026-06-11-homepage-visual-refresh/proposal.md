# 提案 · 主助手首页视觉刷新（暖纸视觉 + 操作台布局）

> 立项 2026-06-10。选定方案：**A 暖纸视觉 + B 操作台布局** 混搭（三套设计方向验收后由用户拍板）。
> 视觉基线：`.mockup/homepage-redesign.html`。

## Why（动机）

chat-first 首页已交付（暖纸 Claude 风，`frontend/components/chat/EmptyState.tsx`），但用户希望首屏更有
**"运营操作台"的专业体感**，并把**"三智能体协作"在首屏可视化**。验收三套设计方向（A 晨光暖纸 / B 深色专业台
/ C 活力卡片）后，选定 **A 的暖纸视觉 + B 的操作台布局**：保留现有暖纸克制美学，叠加 B 的信息结构
（三智能体状态灯条、输入框品牌红柔光圈、状态脚注），让首屏既温和又"专业、能力可见"。

## What（范围）

纯前端**空状态首屏（EmptyState）**视觉刷新，**不改任何后端契约 / 交互流程 / 路由 / 对话流**。

- `frontend/app/globals.css`：补暖色 token（三智能体灯色、输入框品牌红柔光圈 `--ring`、暖纸底微调）。
- `frontend/components/chat/EmptyState.tsx`：问候与输入框之间新增「**三智能体状态灯条**」（采集/分析/生成 · 就绪）；
  底部从纯说明文案改为「**状态脚注**」（动态编排 · 真流式 · 可暂停追问）。
- `frontend/components/chat/Composer.tsx`（仅 `hero` 变体）：聚焦时品牌红柔光圈。
- **去 emoji（全局）**：`EmptyState` / `Composer` / `ChatTopbar` 现有 emoji（问候手势 / chips / 目标 / 管理后台齿轮）
  全部换 `lucide-react`（前端已装）线性图标——用户明确要求，见 memory `feedback_no_emoji_in_ui`。
- 视觉基线：`.mockup/homepage-redesign.html`（已落盘，全程无 emoji）。

**不在范围**：对话流（活动态）气泡、`/admin` 后台、深色主题、`Composer` 的 `docked` 变体、任何后端/SSE/agent 逻辑。

## Impact（影响范围）

- 仅 4 个前端文件（globals.css / EmptyState / Composer / ChatTopbar）+ 1 个 mockup；无 API / 存储 / agent / 路由改动；无破坏性。
- 不改 `EmptyState` 的 props 契约与 `onSend`/`onGoalChange` 行为——纯展示层叠加。
- 三智能体状态灯条是**能力可见性 affordance**（intel/analyst/content 三子 agent 恒定注册可用，"就绪"是
  对"主助手能编排什么"的真陈述，非伪造实时指标），与现有脚注「自动编排 采集→分析→生成」同类，
  符合"展示元素要么承载真信息、要么明确是装饰"原则——此处明确为**恒真的能力提示 + 品牌化强调**。

## Risk（风险）

- **低**。改动局限在空状态展示层，无数据依赖、无行为变更。
- 柔光圈用 `box-shadow`（聚焦环）实现，注意暗色/对比无障碍（暖纸底对比充足；非装饰性焦点态保留）。
- 灯条文案固定，不声称实时健康状态（避免"假状态卡"）。

## 横切维度影响审查

- [x] tenant_id：不涉及（纯前端视觉）
- [x] goal_id：已保留（`Composer` 目标选择器行为不变）
- [x] persona_id：不涉及
- [x] funnel_stage：不涉及

## Spec delta

**无 capability 契约变更**（纯视觉层）。orchestrator 行为、SSE 事件契约、路由结构均不变，
故不新增/修改 `openspec/specs/*`。归档时仅移目录 + STATUS 标完成。
