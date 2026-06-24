-- Phase V1.3 · 010 · Orchestrator 主助手会话持久化
--
-- 关联:
--   - openspec/changes/orchestrator-mvp/{proposal.md, design.md, tasks.md P2.1}
--   - 纯 service 等价实现: agents/orchestrator.py（会话状态机）
--   - 本机 PG 不可达 → 本迁移 reviewed-only，PG 部署后随 migration_runner 执行
--
-- 设计要点:
--   1. orchestrator_sessions 表:一次「意图→计划→确认」对话的完整状态快照
--   2. 带 tenant_id UUID + RLS + FORCE RLS,与 007 / 008 风格保持一致
--   3. 状态机用 TEXT + CHECK,不引入 ENUM
--   4. OCC rev 字段(对齐 topics / generated_content)
--   5. messages / proposed_plan / decision_cards 用 JSONB(对话历史 + 计划 + 卡片)
--   6. 索引第一列恒为 tenant_id(对齐 003 约定)
--   7. PG 16 兼容,禁用 PG 17 才有的语法


CREATE TABLE orchestrator_sessions (
    session_id      TEXT PRIMARY KEY,
    tenant_id       UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    goal_id         TEXT REFERENCES goals(goal_id) ON DELETE SET NULL,
    status          TEXT NOT NULL DEFAULT 'gathering'
                    CHECK (status IN ('gathering', 'planned', 'dispatched', 'cancelled')),
    messages        JSONB NOT NULL DEFAULT '[]'::jsonb,   -- [{role, text}]
    proposed_plan   JSONB NOT NULL DEFAULT '[]'::jsonb,   -- [TaskNode asdict]
    decision_cards  JSONB NOT NULL DEFAULT '[]'::jsonb,   -- [{card_id, kind, status, ...}]
    dag_id          TEXT,                                 -- 确认后挂的 submit_dag id
    rev             INTEGER NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE orchestrator_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE orchestrator_sessions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_orch_sessions ON orchestrator_sessions
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);

CREATE INDEX idx_orch_sessions_tenant_status
    ON orchestrator_sessions (tenant_id, status, updated_at DESC);
CREATE INDEX idx_orch_sessions_tenant_goal
    ON orchestrator_sessions (tenant_id, goal_id);
