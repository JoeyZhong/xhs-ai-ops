-- Phase V1.3 · 011 · Orchestrator trace + pending 字段 + status 枚举更新
--
-- 关联:
--   - docs/handoff/orchestrator-coordinator-contracts.md §A
--   - P1 已为 local_json.py 加好 trace/pending;PG 侧至此对齐
--   - 本机 PG 不可达 → 本迁移 reviewed-only，PG 部署后随 migration_runner 执行
--
-- 设计要点:
--   1. trace JSONB：协调步骤事件数组(合约 §B 事件即 trace 元素)
--   2. pending JSONB：当前暂停状态(null=无暂停 / {kind,question} / {kind,card})
--   3. 新 status CHECK 替换旧值,PG 16 兼容
--   4. 两列均有 DEFAULT,不影响现有 INSERT 不显式传值的行为
--   5. 沿用 010 的 RLS / 索引设计,不新增


-- 1) 新增 trace / pending 列
ALTER TABLE orchestrator_sessions
    ADD COLUMN trace   JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE orchestrator_sessions
    ADD COLUMN pending JSONB DEFAULT 'null'::jsonb;  -- null = 无待用户输入


-- 2) 替换 status CHECK 约束
ALTER TABLE orchestrator_sessions
    DROP CONSTRAINT IF EXISTS orchestrator_sessions_status_check;

ALTER TABLE orchestrator_sessions
    ADD CONSTRAINT orchestrator_sessions_status_check
        CHECK (status IN ('thinking', 'awaiting_user', 'awaiting_decision', 'done', 'cancelled'));


-- 3) 可选:调整注释(便于查 pg_catalog)
COMMENT ON COLUMN orchestrator_sessions.trace   IS '协调步骤事件数组,见合约 §B';
COMMENT ON COLUMN orchestrator_sessions.pending  IS '当前暂停状态:null=无,{kind,question}=问用户,{kind,card}=出决策卡';
COMMENT ON COLUMN orchestrator_sessions.status   IS 'thinking|awaiting_user|awaiting_decision|done|cancelled';
