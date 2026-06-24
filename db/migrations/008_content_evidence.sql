-- Phase P2 · 008 · Insight Evidence Pool
--
-- 关联:
--   - openspec/changes/content-lifecycle-v2/design.md §2（表设计）/ §4（ranking SQL）
--   - openspec/changes/content-lifecycle-v2/tasks.md P2.1 + P2.2
--
-- 设计要点:
--   1. content_evidence 表:高 CES 笔记提取的 {angle, hook, key_insight} 结构化存储
--   2. 所有业务表带 tenant_id UUID + RLS + FORCE RLS,与 007 风格保持一致
--   3. 幂等键 (tenant_id, source_note_id) UNIQUE → ON CONFLICT 写入
--   4. 索引第一列恒为 tenant_id(对齐 003 约定)
--   5. PG 16 兼容,禁用 PG 17 才有的语法


CREATE TABLE content_evidence (
    evidence_id    TEXT PRIMARY KEY,
    tenant_id      UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    source_note_id TEXT,
    angle          TEXT,
    funnel_stage   TEXT CHECK (funnel_stage IN ('traffic', 'trust', 'conversion')),
    hook           TEXT,
    key_insight    TEXT,
    ces_score      NUMERIC,
    extracted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw            JSONB,
    UNIQUE (tenant_id, source_note_id)
);

ALTER TABLE content_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_evidence FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_evidence ON content_evidence
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);

CREATE INDEX idx_evidence_tenant_angle ON content_evidence (tenant_id, angle);
CREATE INDEX idx_evidence_tenant_funnel ON content_evidence (tenant_id, funnel_stage);
