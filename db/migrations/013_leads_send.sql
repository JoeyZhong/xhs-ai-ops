-- lead-intent-radar-v2 · 013 · leads 加「一键发送」字段
-- ⚠️ PG 16 兼容语法。
-- 背景：V2 小红书半自动一键发送（默认 dryrun，真发走 ReaJason comment_note）。
--   真发成功时记录发送时刻 / 平台返回 id / 引擎名；dryrun 不写这些字段。
--   多源信源由 goal.lead_sources 配置承载（goals 侧），leads 表沿用 source 列即可，本迁移只补写端三列。
-- 幂等：IF NOT EXISTS，可重复执行。

ALTER TABLE leads ADD COLUMN IF NOT EXISTS sent_at          TIMESTAMPTZ;  -- 真发时间戳（dryrun 为 NULL）
ALTER TABLE leads ADD COLUMN IF NOT EXISTS send_platform_id TEXT;         -- 平台返回的评论 id
ALTER TABLE leads ADD COLUMN IF NOT EXISTS send_engine      TEXT;         -- 真发引擎：reajason（dryrun 不落库）
