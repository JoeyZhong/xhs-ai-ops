# Design: Agent Hub Skill Resolution

## Minimal Architecture

Do not add a new `memory.search_skills` tool for V1. The repo already has the right conceptual tool: `skills.read`. The bug is that both the prompt index and the read tool still point to legacy file-backed skills instead of the Skills Hub equipment table.

Use this bridge:

```text
AgentBase._build_skills_block()
  ├─ settings.skills_source == "hub"
  │    ├─ storage.factory.get_backend()
  │    ├─ agents.equipment_loader.load(tenant_id, role, backend)
  │    └─ agents.equipment_loader.render_prompt_block(equipped)
  └─ otherwise legacy MemoryLayer.list_skills()

agent_tools.skills.read
  ├─ validate scope == agent_role
  ├─ if settings.skills_source == "hub"
  │    ├─ list_equipment(ctx.tenant_id, agent_role)
  │    ├─ resolve by skill_id first, exact name second
  │    └─ backend.get_skill(skill_id, ctx.tenant_id).body
  └─ otherwise legacy MemoryLayer.read_skill(ctx.tenant_id, scope, name)
```

## Prompt Contract

The hub prompt block must be explicit enough for weaker LLM behavior:

```markdown
## Available Skills

When the user explicitly mentions one of these skill names, first call `skills.read`
with its `skill_id`, then follow the returned SKILL.md body.

- `skill_id=...` **测试技能导入**: ...
```

This avoids requiring semantic search. For V1, exact name match is enough.

## Tool Contract

`skills.read` keeps backward compatibility:

- Existing args still work: `{ "scope": "content", "name": "钩子三段式写作" }`
- New hub-safe args also work: `{ "scope": "content", "skill_id": "<uuid>" }`
- At least one of `name` or `skill_id` is required.
- In hub mode, the skill must be equipped for the calling agent role.
- In file mode, only `name` is supported.

## Safety

Imported skill body is user-controlled content. It is methodology, not system instruction. `skills.read` should return it as data. Existing AgentBase already sanitizes tool results with untrusted-data spotlighting before feeding them back to the model; keep that path.

## Why Not `memory.search_skills`

A search tool would be useful later, but it adds ranking and ambiguity problems. The current bug is deterministic: the skill is already equipped and often explicitly named by the user. A prompt-visible equipment list plus `skills.read(skill_id)` closes the loop with fewer moving parts.

