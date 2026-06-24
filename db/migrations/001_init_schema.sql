-- Phase 4a · 001 · 初始 Schema
-- ⚠️ 严格使用 PG 16 兼容语法,禁用 PG 17 才有的 feature(MERGE...RETURNING, JSON path 新语法等)
-- ⚠️ 所有业务表必须带 tenant_id UUID;cookies 使用 pgcrypto 列加密(在 storage 层调用 pgp_sym_*)

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";


-- ── tenants(租户根表)─────────────────────────────────────────────────────
CREATE TABLE tenants (
    tenant_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        TEXT NOT NULL,
    is_admin    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    note        TEXT
);


-- ── goals(运营目标)──────────────────────────────────────────────────────
CREATE TABLE goals (
    goal_id     TEXT PRIMARY KEY,
    tenant_id   UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    data        JSONB NOT NULL,           -- 完整 goal payload(关键词、漏斗策略等)
    rev         INTEGER NOT NULL DEFAULT 1,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ── personas(账号人设)───────────────────────────────────────────────────
CREATE TABLE personas (
    persona_id  TEXT PRIMARY KEY,
    tenant_id   UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    data        JSONB NOT NULL,           -- 完整 persona payload
    is_active   BOOLEAN NOT NULL DEFAULT FALSE,
    rev         INTEGER NOT NULL DEFAULT 1,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ── collected_notes(采集笔记原始数据,替代 xhs_data/*.xlsx)─────────────
CREATE TABLE collected_notes (
    note_id          TEXT NOT NULL,             -- XHS 平台 note_id
    tenant_id        UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    goal_id          TEXT REFERENCES goals(goal_id) ON DELETE SET NULL,
    keyword          TEXT,
    title            TEXT,
    author           TEXT,
    likes            INTEGER,
    comments_count   INTEGER,
    shares           INTEGER,
    collects         INTEGER,
    ces_score        NUMERIC(10, 2),            -- 预计算的 CES 分
    raw              JSONB NOT NULL,            -- 完整原始记录
    collected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, note_id)
);


-- ── generated_content(内容生成结果,替代 generated_content_*.xlsx)──────
CREATE TABLE generated_content (
    content_id   TEXT PRIMARY KEY,
    tenant_id    UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    goal_id      TEXT REFERENCES goals(goal_id) ON DELETE SET NULL,
    persona_id   TEXT REFERENCES personas(persona_id) ON DELETE SET NULL,
    title        TEXT,
    body         TEXT,
    hashtags     TEXT[] NOT NULL DEFAULT '{}',
    publish_at   TEXT,                     -- 自然语言时间,LLM 给的
    status       TEXT NOT NULL DEFAULT 'draft',   -- draft | approved | published | rejected
    meta         JSONB,                    -- 备选标题/角度/Agent trace 等
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ── agent_memory(playbook / methodology / 行级 entries)─────────────────
CREATE TABLE agent_memory (
    tenant_id   UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    scope       TEXT NOT NULL,             -- shared | intel | content | analyst
    file        TEXT NOT NULL,             -- playbook.md / methodology.md / agent_equipment.json
    entry_id    TEXT NOT NULL DEFAULT '',  -- '' = 整文件;非空 = 行级 entry
    body        TEXT NOT NULL,
    rev         INTEGER NOT NULL DEFAULT 1,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, scope, file, entry_id)
);


-- ── skills(技能库,通用池 tenant_id IS NULL,私有池 tenant_id = ...)───
CREATE TABLE skills (
    skill_id          TEXT PRIMARY KEY,
    tenant_id         UUID REFERENCES tenants(tenant_id) ON DELETE CASCADE,  -- NULL = universal pool
    name              TEXT NOT NULL,
    description       TEXT NOT NULL,
    version           TEXT NOT NULL DEFAULT '1.0.0',
    suggested_for     TEXT[] NOT NULL DEFAULT '{}',
    allowed_tools     TEXT[] NOT NULL DEFAULT '{}',
    license           TEXT NOT NULL DEFAULT '',
    body              TEXT NOT NULL,
    source_skill_id   TEXT REFERENCES skills(skill_id) ON DELETE SET NULL,
    status            TEXT NOT NULL DEFAULT 'active',
    rev               INTEGER NOT NULL DEFAULT 1,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ── agent_equipment(Agent 装备映射)─────────────────────────────────────
CREATE TABLE agent_equipment (
    tenant_id   UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    role        TEXT NOT NULL,             -- intel | content | analyst
    skill_id    TEXT NOT NULL REFERENCES skills(skill_id) ON DELETE CASCADE,
    equipped_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, role, skill_id)
);


-- ── cookies(XHS 凭证加密存储)──────────────────────────────────────────
-- 写入:pgp_sym_encrypt(cookie_str, master_key) → BYTEA
-- 读取:pgp_sym_decrypt(cookie_encrypted, master_key)::text
CREATE TABLE cookies (
    tenant_id          UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    account_id         TEXT NOT NULL,            -- = personas.persona_id
    cookie_encrypted   BYTEA NOT NULL,
    last_update_time   TIMESTAMPTZ NOT NULL DEFAULT now(),
    note               TEXT,
    PRIMARY KEY (tenant_id, account_id)
);


-- ── audit_log(审计日志,所有 agent / tool 调用)─────────────────────────
CREATE TABLE audit_log (
    log_id      BIGSERIAL PRIMARY KEY,
    tenant_id   UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind        TEXT NOT NULL,             -- agent_start | tool_call | agent_complete | ...
    data        JSONB NOT NULL
);
