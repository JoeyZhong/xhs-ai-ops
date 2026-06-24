-- Phase 4a · 005 · 双角色模式 + FORCE RLS(defense in depth)
--
-- 问题:docker POSTGRES_USER=spider 默认建为 SUPERUSER,而 PG superuser 永远 BYPASS RLS,
--      会让 RLS policies 形同虚设。
-- 方案:仿照 Aliyun RDS 模式——分离 admin(spider,用于 migrations/ops)和 app(spider_app,
--      用于运行时连接,NOSUPERUSER,无 BYPASSRLS)。所有业务表 FORCE RLS,即使万一误用
--      spider 跑业务查询,RLS 仍然兜底。
--
-- ⚠️ migration_runner.py 在跑本 migration 前会 SET LOCAL app.app_password = '<env var>',
--    本 migration 用 current_setting('app.app_password') 读取该密码建 role。

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'spider_app') THEN
        EXECUTE format(
            'CREATE ROLE spider_app WITH LOGIN PASSWORD %L NOINHERIT NOSUPERUSER',
            current_setting('app.app_password')
        );
    END IF;
END $$;

-- 授予 spider_app 最小权限
GRANT USAGE ON SCHEMA public TO spider_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO spider_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO spider_app;
-- 未来新表也自动授权(给 spider 这个 default privileges 拥有者)
ALTER DEFAULT PRIVILEGES FOR ROLE spider IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO spider_app;
ALTER DEFAULT PRIVILEGES FOR ROLE spider IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO spider_app;

-- FORCE RLS:让 owner(spider)也受 RLS 约束(defense in depth)
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
ALTER TABLE goals FORCE ROW LEVEL SECURITY;
ALTER TABLE personas FORCE ROW LEVEL SECURITY;
ALTER TABLE collected_notes FORCE ROW LEVEL SECURITY;
ALTER TABLE generated_content FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_memory FORCE ROW LEVEL SECURITY;
ALTER TABLE skills FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_equipment FORCE ROW LEVEL SECURITY;
ALTER TABLE cookies FORCE ROW LEVEL SECURITY;
ALTER TABLE audit_log FORCE ROW LEVEL SECURITY;
