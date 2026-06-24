-- lead-intent-radar · 012 · leads 表（主动获客线索）
-- ⚠️ PG 16 兼容语法。
-- 设计要点：
--   · 独立于 collected_notes（避免已知中英列错配 / goal 隔离断裂坑）
--   · 仅持久化「通过意图判定的合格线索」；被过滤的噪声信号不落库（见 spec 噪声过滤场景）
--   · 字段集对齐 Phase D 定稿（design/leads-inbox.html 冻结字段）
--   · tenant_id / goal_id / persona_id 三维度齐全，RLS 双保险

CREATE TABLE leads (
    lead_id        TEXT NOT NULL,                  -- lead_<hex>
    tenant_id      UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    goal_id        TEXT REFERENCES goals(goal_id) ON DELETE SET NULL,
    persona_id     TEXT REFERENCES personas(persona_id) ON DELETE SET NULL,

    -- ── 来源信号（原帖）──────────────────────────────────────────────
    source         TEXT NOT NULL DEFAULT 'xhs',    -- 信源：xhs（V1 仅小红书）
    source_url     TEXT,                           -- 原帖链接
    signal_key     TEXT NOT NULL,                  -- 去重键（note_id / url 归一），同 key 幂等
    author         TEXT,                           -- 发帖人
    posted_at      TEXT,                           -- 原帖发布时间（平台给的自然值）
    post_text      TEXT,                           -- 原帖全文
    excerpt        TEXT,                           -- 列表用摘要（前端两行）
    detected_at    TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 雷达捕获时刻（算检测延迟）

    -- ── 意图判定 ────────────────────────────────────────────────────
    is_intent      BOOLEAN NOT NULL DEFAULT TRUE,  -- 是否真实求购
    match_score    INTEGER,                        -- 画像匹配度 0-100
    trigger_type   TEXT,                           -- loan|bid|hitech|foreign|cancel
    judge_reason   TEXT,                           -- 判定理由

    -- ── 首触 ────────────────────────────────────────────────────────
    draft_text     TEXT,                           -- 首触草稿
    check_lure_pass BOOLEAN NOT NULL DEFAULT FALSE, -- 引流词校验通过
    check_dup_pass  BOOLEAN NOT NULL DEFAULT FALSE, -- 雷同度校验通过

    -- ── 生命周期 + 度量 ─────────────────────────────────────────────
    lead_status    TEXT NOT NULL DEFAULT 'qualified',  -- detected|qualified|drafted|pending|touched|skipped
    touched_at     TIMESTAMPTZ,                    -- 标记触达时刻
    outcome        TEXT,                           -- NULL|replied|converted（北极星=沟通机会数）

    meta           JSONB,                          -- 原始 Signal / Agent trace 等
    rev            INTEGER NOT NULL DEFAULT 1,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (tenant_id, lead_id),
    UNIQUE (tenant_id, signal_key)                 -- 同一原帖只生成一条 lead（幂等去重）
);

CREATE INDEX idx_leads_tenant_goal_status
    ON leads (tenant_id, goal_id, lead_status);
CREATE INDEX idx_leads_tenant_detected
    ON leads (tenant_id, detected_at DESC);

-- ── RLS（对齐 002_enable_rls.sql 范式）─────────────────────────────
ALTER TABLE leads ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_leads ON leads
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
