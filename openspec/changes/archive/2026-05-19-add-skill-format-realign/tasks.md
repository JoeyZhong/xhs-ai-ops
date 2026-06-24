# Tasks: add-skill-format-realign

Checklist format：`[ ]` = pending | `[x]` = done | `[-]` = skipped

## §0 Pre-flight

- [x] §0.1 实证 be8b3ac 文件清单
- [x] §0.2 未追踪/未暂存清单
- [x] §0.3 对照检查清单，确认零遗漏
- [x] §0.4 无需 backfill（全部文件已在树中）
- [x] §0.5 验收：git status 0 skill 条目、be8b3ac 在 log、全路径覆盖

## §1 Parser 重写

- [x] `_FRONTMATTER_RE` + `_split_frontmatter()` 辅助函数
- [x] `ParsedSkill` 增加 `allowed_tools` / `license` 字段
- [x] `parse_skill_dir()` 重写：只读 SKILL.md，解 frontmatter + body
- [x] 删除 skill.json 相关分支
- [x] `write_skill_to_dir()` 改写入 frontmatter SKILL.md
- [x] `_build_frontmatter()` 使用 `yaml.dump` 保障 YAML 安全

## §2 Storage 适配

- [x] `_read_skill_bundle()` 改读 frontmatter + 合并 runtime sidecar
- [x] `create_skill()` 写 frontmatter SKILL.md + runtime skill.json
- [x] `update_skill()` 改前 frontmatter 操作 + CAS 保留

## §3 迫迁

- [x] `scripts/migrate_skills_to_frontmatter.py` 创建
- [x] Dry-run 验证 17 skills 可迫迁
- [x] 真实迫迁（含自动备份）
- [x] 验证单 skill frontmatter + body + runtime skill.json

## §4 零改动验证

- [x] `agents/memory.py` — 零改动，通过 `parse_skill_dir` 自动适配
- [x] `agent_tools/skills.py` — 零改动，通过 `mem.read_skill` 获取纯 body
- [x] 手动测试：parse_skill_dir / read_skill_content / list_skills 均正常

## §5 UI Sync

- [x] `dashboard.py` Skills 管理页：读改用 `parse_skill_dir`，写改用 `_build_frontmatter`
- [x] `dashboard.py` 保存路径更新 `skill.json` runtime 元数据（若存在）
- [x] 前端 `skills/page.tsx` 零改动（API 契约不变）

## §6 Tests + Verify

- [x] `tests/test_skills.py` 删 2 个（skill.json 相关）+ 加 5 个（frontmatter 专项）
- [x] `_build_skill_dir` helper 改为 frontmatter 格式
- [x] `scripts/verify_skills.py` §2/§7 更新为 frontmatter 检查
- [x] `verify_skills.py` `_make_skill` 更新为 frontmatter
- [x] pytest: 33/33 passed
- [x] verify_skills: 30/30 passed
- [x] test_skills_api.py: API 契约不变
- [x] DAG 诊断验证通过

## §7 文档

- [x] `openspec/changes/add-skill-format-realign/proposal.md`
- [x] `openspec/changes/add-skill-format-realign/tasks.md`
- [x] `openspec/changes/add-skill-format-realign/specs/agent-skills/spec.md`

## §8 归档

- [x] spec MODIFIED 合入 `openspec/specs/agent-skills/spec.md`
- [x] `scripts/migrate_skills_to_frontmatter.py` → `scripts/archive/`
- [x] `openspec/changes/add-skill-format-realign/` → `openspec/changes/archive/2026-05-19-add-skill-format-realign/`
- [x] CLAUDE.md「Agent skills」格式示例更新
