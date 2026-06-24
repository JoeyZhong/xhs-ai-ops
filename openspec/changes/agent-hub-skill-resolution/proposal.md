# Proposal: Agent Hub Skill Resolution

## Why

V1 skill zip import has proven that storage, import, and equipment work: uploaded skills are persisted and can be equipped in Skills Hub. However, Sub Agents still cannot reliably use an equipped uploaded skill when the user names it in a task.

Root cause:

- Agent startup currently builds its skill context from `MemoryLayer.list_skills()`, which enumerates old file-based role skills.
- `agent_tools.skills.read` currently reads via `MemoryLayer.read_skill()`, also from old file-based role skills.
- PG Skills Hub equipment is available through `StorageBackend.list_equipment()` and `StorageBackend.get_skill()`, but `AgentBase` and `skills.read` do not use that path.
- `agents/equipment_loader.py` already exists as a partial bridge, but `AgentBase._build_skills_block()` does not use it.

Therefore an imported and equipped PG skill can appear in the frontend, while the Agent prompt/tool path remains pointed at the legacy file source.

## What

Implement a small bridge from Skills Hub equipment into the Agent runtime:

- When `config/settings.json` has `"skills_source": "hub"`, Agent prompt skill summaries come from `StorageBackend.list_equipment(tenant_id, role)`.
- The Available Skills block includes `skill_id`, name, description, and explicit instructions:
  - if the user names a listed skill, match by exact name first;
  - then call `skills.read` before answering;
  - pass `skill_id` when available, otherwise pass `name`.
- Extend `skills.read` so it can read an equipped hub skill by `skill_id` or exact `name`.
- Keep the legacy file path when `skills_source` is absent or `"files"`.
- Add focused tests for prompt rendering and `skills.read` hub behavior.

## Out Of Scope

- Semantic search over all skills.
- Direct GitHub/Clawhub fetching.
- Auto-selecting unequipped skills.
- Full marketplace ranking/recommendation.
- Changing the frontend zip import flow.
- Changing Agent tool policies beyond the existing `skills.read` allowlist.

## Acceptance

- With `skills_source=hub`, a ContentAgent with an equipped skill named `测试技能导入` has that name in its system prompt skill block.
- The block tells the model to call `skills.read` when the user explicitly mentions a listed skill.
- `skills.read` can return the full body of an equipped hub skill by `skill_id`.
- `skills.read` can return the full body of an equipped hub skill by exact `name`.
- `skills.read` rejects a skill that is not equipped for the current agent role.
- With `skills_source=files`, existing file-based skill behavior still works.

