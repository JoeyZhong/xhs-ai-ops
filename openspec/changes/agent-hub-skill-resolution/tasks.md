# Tasks: Agent Hub Skill Resolution

## 1. Prompt Source Bridge

- [x] Update `agents/base.py::_build_skills_block()` to use `agents.equipment_loader` when `settings.skills_source == "hub"`.
- [x] Use `storage.factory.get_backend()` for the hub path.
- [x] Keep the current `MemoryLayer.list_skills()` behavior when `skills_source` is missing or `"files"`.
- [x] Update `agents/equipment_loader.py::render_prompt_block()` wording to explicitly instruct exact-name match and `skills.read(skill_id=...)`.

## 2. Hub-Aware `skills.read`

- [x] Extend `agent_tools/skills.py` schema to accept optional `skill_id`.
- [x] Keep `scope` required.
- [x] Require at least one of `name` or `skill_id` in handler logic.
- [x] In hub mode, resolve only from `backend.list_equipment(ctx.tenant_id, agent_role)`.
- [x] In hub mode, match by `skill_id` first; if absent, match exact `name`.
- [x] In hub mode, fetch body via `backend.get_skill(skill_id, ctx.tenant_id)`.
- [x] In hub mode, return a clear error if the skill exists but is not equipped for this role.
- [x] In file mode, preserve the current `MemoryLayer.read_skill()` path.

## 3. Tests

- [x] Add or update tests for `equipment_loader.render_prompt_block()` showing skill id, name, description, and read instruction.
- [x] Add `skills.read` hub-mode test: equipped skill can be read by `skill_id`.
- [x] Add `skills.read` hub-mode test: equipped skill can be read by exact `name`.
- [x] Add `skills.read` hub-mode test: unequipped skill returns an error.
- [x] Add regression test for legacy file mode, if an existing test does not already cover it.

## 4. Manual Verification（待手工验证）

- [ ] Set `"skills_source": "hub"` in `config/settings.json`.
- [ ] Upload/import `测试技能导入` through Skills Hub.
- [ ] Equip it to Content.
- [ ] Run a Content Agent task that explicitly says: `请使用测试技能导入来生成一篇内容`.
- [ ] Confirm `AgentResult.tool_calls` contains `skills.read`.
- [ ] Confirm the final answer reflects the uploaded skill body.

## 5. Documentation

- [x] Add a short note to `docs/SEED_USER_SERVER_RUNBOOK.md` or `docs/USER_GUIDE.md`: V1 Agent skill usage requires `"skills_source": "hub"` and equipped skills.

