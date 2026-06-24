-- Phase 4a · 002 · 启用 Row Level Security
-- 所有业务表 ENABLE RLS,policy 基于 SET LOCAL app.tenant_id 的会话变量。
-- ⚠️ 配合 db/session.py::get_rls_cursor 使用,业务层永远不要直接 SET app.tenant_id。

-- ── tenants(自身只允许 admin 读写)─────────────────────────────────────
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenants_self ON tenants
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid
           OR current_setting('app.is_admin', true) = 'true');

-- ── goals ──────────────────────────────────────────────────────────────
ALTER TABLE goals ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_goals ON goals
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);

-- ── personas ───────────────────────────────────────────────────────────
ALTER TABLE personas ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_personas ON personas
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);

-- ── collected_notes ────────────────────────────────────────────────────
ALTER TABLE collected_notes ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_notes ON collected_notes
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);

-- ── generated_content ──────────────────────────────────────────────────
ALTER TABLE generated_content ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_content ON generated_content
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);

-- ── agent_memory ───────────────────────────────────────────────────────
ALTER TABLE agent_memory ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_memory ON agent_memory
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);

-- ── skills(特殊:通用池 NULL 全可读,私有 tenant_id 匹配才可读写)──
ALTER TABLE skills ENABLE ROW LEVEL SECURITY;
-- SELECT:通用池 OR 自己
CREATE POLICY skills_read ON skills FOR SELECT
    USING (tenant_id IS NULL
           OR tenant_id = current_setting('app.tenant_id', true)::uuid);
-- INSERT/UPDATE/DELETE 私有 skill:必须匹配自己
CREATE POLICY skills_write_own ON skills FOR INSERT
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
CREATE POLICY skills_update_own ON skills FOR UPDATE
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
CREATE POLICY skills_delete_own ON skills FOR DELETE
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid);
-- 通用池(NULL)写操作走 admin 路径,见 004_audit_log_rls.sql

-- ── agent_equipment ────────────────────────────────────────────────────
ALTER TABLE agent_equipment ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_equipment ON agent_equipment
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);

-- ── cookies ────────────────────────────────────────────────────────────
ALTER TABLE cookies ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_cookies ON cookies
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
