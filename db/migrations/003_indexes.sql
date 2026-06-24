-- Phase 4a · 003 · 性能索引
-- 多租户场景下,所有 hot path 查询都带 WHERE tenant_id = ...,
-- 复合索引以 tenant_id 为第一列,RLS + 索引双保险。

-- ── goals:按 tenant_id 列表 ────────────────────────────────────────────
-- PK 已隐式索引 goal_id,这里只补 tenant_id 维度
CREATE INDEX idx_goals_tenant ON goals (tenant_id);

-- ── personas ───────────────────────────────────────────────────────────
CREATE INDEX idx_personas_tenant ON personas (tenant_id);
CREATE INDEX idx_personas_tenant_active ON personas (tenant_id) WHERE is_active = TRUE;

-- ── collected_notes:常见查询是 "某 tenant + goal + 按 CES 排序" ─────
CREATE INDEX idx_notes_tenant_goal ON collected_notes (tenant_id, goal_id);
CREATE INDEX idx_notes_tenant_ces ON collected_notes (tenant_id, ces_score DESC);
CREATE INDEX idx_notes_tenant_collected ON collected_notes (tenant_id, collected_at DESC);

-- ── generated_content ──────────────────────────────────────────────────
CREATE INDEX idx_content_tenant_goal ON generated_content (tenant_id, goal_id);
CREATE INDEX idx_content_tenant_status ON generated_content (tenant_id, status, created_at DESC);

-- ── agent_memory:PK 已含 tenant_id 复合,无需补 ─────────────────────
-- (PK = tenant_id, scope, file, entry_id)

-- ── skills:通用池查询 "WHERE tenant_id IS NULL OR tenant_id = ..." ──
CREATE INDEX idx_skills_tenant ON skills (tenant_id);
-- suggested_for 是 TEXT[],按角色过滤需要 GIN
CREATE INDEX idx_skills_suggested_for ON skills USING GIN (suggested_for);

-- ── agent_equipment:PK 已含 tenant_id 复合 ─────────────────────────────

-- ── cookies:PK 已含 tenant_id 复合 ─────────────────────────────────────

-- ── audit_log:按 tenant + 时间倒序最常见 ──────────────────────────────
CREATE INDEX idx_audit_tenant_ts ON audit_log (tenant_id, ts DESC);
CREATE INDEX idx_audit_tenant_kind ON audit_log (tenant_id, kind, ts DESC);
