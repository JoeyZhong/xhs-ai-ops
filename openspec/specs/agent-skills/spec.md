# Specification: Agent Skills System

## Status
- **Implemented**: 2026-05-15 (v1), 2026-05-17 (v2 — bundle format), 2026-05-19 (v4 — frontmatter)
- **Verification**: `scripts/verify_skills.py` (30/30) + `tests/test_skills.py` (33/33)
- **Capability**: `agent-skills`

## Design

Each Sub Agent (Intel/Content/Analyst) gets a `skills/` subdirectory under its memory scope:

```
memory/{tenant}/
├── intel/skills/
│   ├── 关键词扩展/
│   │   ├── SKILL.md          # YAML frontmatter + body
│   │   └── skill.json        # 仅运行时元数据 (id/rev/status/timestamps)
│   └── 爆款规律分析/
│       ├── SKILL.md
│       └── skill.json
├── content/skills/
│   ├── 钩子三段式写作/
│   │   ├── SKILL.md
│   │   └── skill.json
│   └── 评论引导句库/
│       ├── SKILL.md
│       └── skill.json
└── analyst/skills/
    ├── 10-3-1实操/
    │   ├── SKILL.md
    │   └── skill.json
    └── 流量异常排查/
        ├── SKILL.md
        └── skill.json
```

### Skill format (SKILL.md with YAML frontmatter)

Single-file **YAML frontmatter** embedded in `SKILL.md`, aligned with Claude Code / superpowers / Hermes Agent / OpenClaw standard:

```markdown
---
name: 爆款规律分析
description: 用户问「为什么这些笔记火了」「找共性」「找规律」
version: 1.0.0
suggested_for:
  - intel
allowed_tools:
  - skills.read
license: MIT
---

# 步骤
1. 调用 search.collect_notes(query=<用户关键词>, limit=30) 采集近期高互动笔记
2. ...
```

Required fields: `name`, `description`. Optional: `version` (default `1.0.0`), `suggested_for` (default `[]`), `allowed_tools`, `license`.

Runtime metadata (`id`, `rev`, `status`, `created_at`, `updated_at`, `tenant_id`, `source_skill_id`) lives in `skill.json` sidecar — never in frontmatter.

### Loading mechanism

1. Agent startup → `_collect_memory_snapshot()` calls `MemoryLayer.list_skills(tenant_id, scope)` → enumerates subdirectories in `skills/` (each containing `SKILL.md` is a skill)
2. Each skill parsed via `parse_skill_dir()` → reads `SKILL.md` YAML frontmatter for metadata + body
3. Skills index rendered as `_derived__skills_block.md` in snapshot, containing name + description summary
4. System prompt template renders `{skills_block}` at the end

### Runtime access

- Agent sees summary (name + description) in its system prompt under `【🎯 可用方法论 (Methodology Library)】`
- To load: calls `skills.read(scope=<own_role>, name=<skill_name>)` via OpenAI tool_calls
- Tool validates scope == agent_role (cross-scope isolation)
- Returns pure SKILL.md body text (no metadata, no frontmatter)

### Key files

| Path | Purpose |
|------|---------|
| `agents/skills.py` | `parse_skill_dir()` directory parser + subdir enumeration |
| `agents/memory.py` | `list_skills()` + `read_skill()` on MemoryLayer |
| `agents/base.py` | `_build_skills_block()` → injects into snapshot (methodology wording) |
| `agent_tools/skills.py` | `skills.read` tool registration (returns pure body) |
| `agents/{intel,content,analyst}.py` | `{skills_block}` in prompt + `skills.read` in patterns |
| `memory/default/{scope}/skills/{name}/{SKILL.md,skill.json}` | 6 initial skills (frontmatter SKILL.md + runtime skill.json) |
| `dashboard.py` | Skills management page (3 tabs, CRUD for frontmatter format) |

### Isolation guarantees

- Scope isolation: `skills.read` checks `agent_role == requested_scope`
- Invalid/missing frontmatter files: silently skipped during enumeration
- `references/` subdirectory (optional): not consumed by agent loading, only displayed in dashboard
- No write path: skills are read-only; no `skills.write` tool exists

### Read budget (throttle)

Per-task `skills.read` throttle — see `openspec/changes/archive/2026-05-17-add-skill-read-budget/design.md`.

- `skill_read_budget` in `config/settings.json` (default 2)
- `_BUDGET_COUNTERS` module-level dict in `agent_tools/skills.py`
- `clear_budget(task_id)` called from `AgentBase.run()` finally
- Failed reads (scope mismatch, not found, missing args) do not consume budget
- Empty/missing `task_id` skips budget logic entirely
- `_load_budget()` falls back to 2 when settings.json lacks the field

### Change history

| Date | Change | Rationale |
|------|--------|-----------|
| 2026-05-15 | v1: single .md with YAML frontmatter | Initial implementation |
| 2026-05-17 | v2: SKILL.md + skill.json bundle per `add-skill-bundle-format` | Align with Claude Code standard, enable hub/distribution |
| 2026-05-17 | v3: per-task read budget throttle per `add-skill-read-budget` | Prevent LLM greedy-read loops, complement stuck-detection |
| 2026-05-19 | v4: 单文件 YAML frontmatter per `add-skill-format-realign` | 对齐跨生态标准 (Claude Code, superpowers, claude-mem, Hermes Agent, OpenClaw); 零转换导入/导出; 降 hub 复杂度 |
