-- db/migrations/006_aux_tables.sql
-- Phase 4a · §A4 · task_results + hot_keywords 表(原 §A2 schema 漏的两张)

-- ── task_results(AgentTask 执行结果存档,替代 xhs_data/tasks/*.json)─
CREATE TABLE task_results (
    task_id      TEXT PRIMARY KEY,
    tenant_id    UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    data         JSONB NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE task_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE task_results FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_tasks ON task_results
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
CREATE INDEX idx_tasks_tenant_created ON task_results (tenant_id, created_at DESC);

-- ── hot_keywords(热词监控产出,替代 hot_trends_*.xlsx)──────────────
CREATE TABLE hot_keywords (
    hot_id          TEXT PRIMARY KEY,            -- uuid 或 snapshot id
    tenant_id       UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    keyword         TEXT NOT NULL,
    score           NUMERIC(10, 2),
    raw             JSONB NOT NULL,
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE hot_keywords ENABLE ROW LEVEL SECURITY;
ALTER TABLE hot_keywords FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_hotwords ON hot_keywords
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
CREATE INDEX idx_hotwords_tenant_captured ON hot_keywords (tenant_id, captured_at DESC);
CREATE INDEX idx_hotwords_tenant_keyword ON hot_keywords (tenant_id, keyword);
