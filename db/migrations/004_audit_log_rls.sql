-- Phase 4a · 004 · audit_log RLS + admin 跨租户读 + skills 通用池 admin 写
-- 拆出来单文件:这里的 policy 比 002 复杂,涉及 is_admin 旁路。

-- ── audit_log:租户自己只能读自己的;admin 可跨租户读 ───────────────
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY audit_read_own ON audit_log FOR SELECT
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid);

CREATE POLICY audit_read_admin ON audit_log FOR SELECT
    USING (current_setting('app.is_admin', true) = 'true');

-- 写:任何租户(应用层)都能写自己的;admin 可代写任意租户(回放/迁移场景)
CREATE POLICY audit_write_own ON audit_log FOR INSERT
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid
                OR current_setting('app.is_admin', true) = 'true');

-- 不允许 UPDATE/DELETE audit_log(append-only)
-- 不显式建 UPDATE/DELETE policy 即默认拒绝(因为 ENABLE RLS 后默认拒一切非匹配)


-- ── skills:admin 可写通用池(tenant_id = NULL)─────────────────────────
-- 002 中的 skills_write_own/update_own/delete_own 已限制必须 tenant_id 匹配,
-- 这里加 admin 路径允许写 NULL tenant_id 的通用池条目。

CREATE POLICY skills_admin_write ON skills FOR INSERT
    WITH CHECK (current_setting('app.is_admin', true) = 'true');

CREATE POLICY skills_admin_update ON skills FOR UPDATE
    USING (current_setting('app.is_admin', true) = 'true')
    WITH CHECK (current_setting('app.is_admin', true) = 'true');

CREATE POLICY skills_admin_delete ON skills FOR DELETE
    USING (current_setting('app.is_admin', true) = 'true');


-- ── tenants:admin 已在 002 的 tenants_self 含 is_admin OR 分支 ─────
-- 此处无需补充 admin policy。
