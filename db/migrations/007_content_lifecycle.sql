-- Phase V1.1 · 007 · 内容生命周期统一模型
--
-- 关联:
--   - PRD docs/PRD_V1_1_SELF_LEARNING_XHS_AGENT_PLATFORM.md §8.A / §8.1 / §8.2 / §8.3
--   - openspec/changes/content-lifecycle-v1/{proposal.md, tasks.md, design.md}
--
-- 设计要点:
--   1. 三张新表(topics / calendar_items / content_strategies)+ generated_content 扩列
--   2. 所有业务表带 tenant_id UUID + RLS + FORCE RLS,与 002 / 006 风格保持一致
--   3. 状态机用 TEXT + CHECK,不引入 ENUM(避免后续 ALTER 麻烦)
--   4. OCC rev 字段统一加(对齐 goals / personas / agent_memory)
--   5. 软删除:calendar_items 用 deleted_at IS NULL + status='cancelled' 双信号
--   6. 索引第一列恒为 tenant_id(对齐 003 约定)
--   7. PG 16 兼容,禁用 PG 17 才有的语法


-- ── topics(内容选题,替代 config/goals.json 中的 goal.topic_library[])─────
CREATE TABLE topics (
    topic_id        TEXT PRIMARY KEY,
    tenant_id       UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    goal_id         TEXT REFERENCES goals(goal_id) ON DELETE SET NULL,
    persona_id      TEXT REFERENCES personas(persona_id) ON DELETE SET NULL,
    title           TEXT NOT NULL,
    angle           TEXT,
    funnel_stage    TEXT CHECK (funnel_stage IN ('traffic', 'trust', 'conversion')),
    source          TEXT NOT NULL DEFAULT 'manual'
                    CHECK (source IN ('ai', 'manual', 'market_insight', 'memory')),
    source_refs     JSONB NOT NULL DEFAULT '[]'::jsonb,    -- 关联笔记/memory/知识库 id 列表
    status          TEXT NOT NULL DEFAULT 'idea'
                    CHECK (status IN ('idea', 'planned', 'drafting', 'drafted',
                                       'scheduled', 'published', 'archived')),
    created_by      TEXT NOT NULL DEFAULT 'user'
                    CHECK (created_by IN ('user', 'orchestrator', 'intel',
                                           'analyst', 'content', 'scheduler', 'system')),
    rev             INTEGER NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE topics ENABLE ROW LEVEL SECURITY;
ALTER TABLE topics FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_topics ON topics
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);

CREATE INDEX idx_topics_tenant_goal ON topics (tenant_id, goal_id);
CREATE INDEX idx_topics_tenant_status ON topics (tenant_id, status, updated_at DESC);
CREATE INDEX idx_topics_tenant_persona ON topics (tenant_id, persona_id);


-- ── content_strategies(内容执行策略,服务于一个或一组选题)───────────────
-- PRD §8.2:每条策略都能追溯到至少一个 topic_id 或明确的 manual_input。
CREATE TABLE content_strategies (
    strategy_id        TEXT PRIMARY KEY,
    tenant_id          UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    topic_id           TEXT REFERENCES topics(topic_id) ON DELETE SET NULL,
    manual_input_hint  TEXT,                        -- 当 topic_id 为 NULL 时必须有
    target_reader      TEXT,
    funnel_stage       TEXT CHECK (funnel_stage IN ('traffic', 'trust', 'conversion')),
    angle              TEXT,
    hook               TEXT,
    key_points         JSONB NOT NULL DEFAULT '[]'::jsonb,
    cta                TEXT,
    avoid_points       JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence_refs      JSONB NOT NULL DEFAULT '[]'::jsonb,
    memory_refs        JSONB NOT NULL DEFAULT '[]'::jsonb,
    knowledge_refs     JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_by         TEXT NOT NULL DEFAULT 'user'
                       CHECK (created_by IN ('user', 'orchestrator', 'intel',
                                              'analyst', 'content', 'scheduler', 'system')),
    rev                INTEGER NOT NULL DEFAULT 1,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (topic_id IS NOT NULL OR manual_input_hint IS NOT NULL)
);

ALTER TABLE content_strategies ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_strategies FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_strategies ON content_strategies
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);

CREATE INDEX idx_strategies_tenant_topic ON content_strategies (tenant_id, topic_id);
CREATE INDEX idx_strategies_tenant_created ON content_strategies (tenant_id, created_at DESC);


-- ── calendar_items(内容日历,替代 config/goals.json 的 goal.content_calendar[])─
CREATE TABLE calendar_items (
    calendar_item_id  TEXT PRIMARY KEY,
    tenant_id         UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    topic_id          TEXT REFERENCES topics(topic_id) ON DELETE SET NULL,
    content_id        TEXT REFERENCES generated_content(content_id) ON DELETE SET NULL,
    scheduled_date    DATE NOT NULL,
    scheduled_time    TEXT,                          -- HH:MM 或自然语言(LLM 给出)
    funnel_stage      TEXT CHECK (funnel_stage IN ('traffic', 'trust', 'conversion')),
    status            TEXT NOT NULL DEFAULT 'planned'
                      CHECK (status IN ('planned', 'drafted', 'scheduled',
                                         'published', 'cancelled')),
    delete_mode       TEXT NOT NULL DEFAULT 'soft'
                      CHECK (delete_mode IN ('soft', 'hard')),
    deleted_at        TIMESTAMPTZ,                   -- NULL = 未删除,值 = 软删除时间戳
    created_by        TEXT NOT NULL DEFAULT 'user'
                      CHECK (created_by IN ('user', 'orchestrator', 'intel',
                                             'analyst', 'content', 'scheduler', 'system')),
    rev               INTEGER NOT NULL DEFAULT 1,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE calendar_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE calendar_items FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_calendar ON calendar_items
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);

CREATE INDEX idx_calendar_tenant_date ON calendar_items (tenant_id, scheduled_date)
    WHERE deleted_at IS NULL;
CREATE INDEX idx_calendar_tenant_status ON calendar_items (tenant_id, status)
    WHERE deleted_at IS NULL;
CREATE INDEX idx_calendar_tenant_topic ON calendar_items (tenant_id, topic_id);
CREATE INDEX idx_calendar_tenant_content ON calendar_items (tenant_id, content_id);


-- ── generated_content 扩列 ────────────────────────────────────────────────
-- 原表来自 001_init_schema.sql,本次只 ADD COLUMN,不改动既有列。
-- IF NOT EXISTS 兜底重跑(PG 9.6+ 支持)。

ALTER TABLE generated_content
    ADD COLUMN IF NOT EXISTS topic_id          TEXT REFERENCES topics(topic_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS strategy_id       TEXT REFERENCES content_strategies(strategy_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS calendar_item_id  TEXT REFERENCES calendar_items(calendar_item_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS knowledge_refs    JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS memory_refs       JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS rev               INTEGER NOT NULL DEFAULT 1;

-- 注:status 列原 DEFAULT 'draft',无 CHECK 约束,PRD §8.3 扩展集合(draft / edited /
-- scheduled / published / rejected)无需 DDL 改动;由 Pydantic 层做 in-set 校验。
-- rev 列对齐 topics / content_strategies / calendar_items 的 OCC 风格,
-- 草稿编辑用 WHERE content_id = %s AND rev = %s,版本不匹配返回 409
-- (回应 design.md §Schema 反馈 #1)。

CREATE INDEX IF NOT EXISTS idx_content_tenant_topic ON generated_content (tenant_id, topic_id);
CREATE INDEX IF NOT EXISTS idx_content_tenant_calendar ON generated_content (tenant_id, calendar_item_id);
CREATE INDEX IF NOT EXISTS idx_content_tenant_strategy ON generated_content (tenant_id, strategy_id);


-- ── 数据迁移注记 ──────────────────────────────────────────────────────────
-- 一次性脚本 scripts/migrate_goals_json_to_pg.py(待写)负责:
--   1. 读 config/goals.json 每个 goal 下的 topic_library[] → 写入 topics 表
--      映射:title / angle / keywords[] → source_refs.keywords / created_at,
--      tenant_id 从环境注入或默认 'default' UUID,source='manual',status='idea'
--   2. 读每个 goal 下的 content_calendar[] → 写入 calendar_items 表
--      映射:日期 → scheduled_date,选题 → 关联到 topics(按 title 模糊匹配),
--      类型 → funnel_stage,状态 → status
--   3. 幂等:用 topic_id / calendar_item_id 的固定派生策略(如 sha1(tenant + title + idx))
--   4. 备份:迁移前 cp config/goals.json -> config/goals.json.bak
--   5. 迁移后 goals.json 中的两个数组保留为空(不删除字段,兼容旧读路径)
