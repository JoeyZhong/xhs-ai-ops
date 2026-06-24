# Spec Delta: agent-skills — YAML Frontmatter Format

> 修改现有 capability `agent-skills`（2026-05-15 上线，v2 bundle-format 于 2026-05-17，v3 read-budget 于 2026-05-17）。
> 将 bundle-format（`SKILL.md` + `skill.json` 双文件）改为 **单文件 `SKILL.md` 内嵌 YAML frontmatter**，对齐跨生态事实标准。

## Motivation

Spider_XHS 的 bundle-format 偏离了 Claude Code / Agent SDK / superpowers / Hermes Agent / OpenClaw 等已收敛到的单文件 frontmatter 标准。对齐标准带来零转换导入/导出能力，并降低未来 skill-hub 复杂度。

## ADDED: YAML Frontmatter + `_split_frontmatter()`

`SKILL.md` SHALL 以 `---\n` 开始，解析为 YAML frontmatter 块后接 Markdown body：

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
1. 调用 search.collect_notes...
```

Parser 函数 `_split_frontmatter(text: str) -> tuple[dict, str]`：
- 用正则 `^---\n(.*?)\n---\n` 提取 frontmatter 块
- `yaml.safe_load` 解析为 dict
- 剩余部分为 body（strip 后返回）
- 缺 `name` 字段 → `SkillParseError`
- 未识别字段 → 静默忽略
- 缺 `version` → 默认为 `"1.0.0"`
- 缺 `suggested_for` → 默认为 `[]`

## MODIFIED: `parse_skill_dir()` — 只读 SKILL.md

`parse_skill_dir(skill_dir: Path) -> ParsedSkill` 不再读取 `skill.json`：

1. 检查 `SKILL.md` 存在 → 否则 `SkillParseError`
2. `_split_frontmatter()` 提取 frontmatter dict + body
3. 构造 `ParsedSkill(name=..., description=..., version=..., suggested_for=..., body=..., allowed_tokens=..., license=...)`
4. frontmatter 中 `allowed_tools` / `license` → `ParsedSkill` 字段（Spider_XHS 自身不消费，为生态兼容预留）
5. YAML 布尔歧义规避：`str(fm.get("name") or "").strip()` 确保 name 始终为 str

### Scenario: 读取标准 frontmatter SKILL.md

- **GIVEN** 目录下存在 `SKILL.md`，内容为 `---\nname: test\ndescription: for testing\n---\n\n# body`
- **WHEN** `parse_skill_dir(skill_dir)` 被调用
- **THEN** 返回 `ParsedSkill(name="test", description="for testing", body="# body")`
- **AND** body 不含 frontmatter

### Scenario: 缺 SKILL.md

- **GIVEN** skill 目录下无 `SKILL.md`
- **WHEN** `parse_skill_dir(skill_dir)`
- **THEN** `raise SkillParseError(...)`

### Scenario: 缺 name 字段

- **GIVEN** frontmatter 不含 `name`
- **WHEN** `parse_skill_dir(skill_dir)`
- **THEN** `raise SkillParseError("missing required 'name' in frontmatter")`

### Scenario: 缺 version 字段

- **GIVEN** frontmatter 不含 `version`
- **WHEN** `parse_skill_dir(skill_dir)`
- **THEN** `ParsedSkill.version` 为 `"1.0.0"`

### Scenario: 未识别 frontmatter 字段

- **GIVEN** frontmatter 包含 `unknown: ignored`
- **WHEN** `parse_skill_dir(skill_dir)`
- **THEN** 静默忽略，不报错

## MODIFIED: `write_skill_to_dir()` — 写 frontmatter SKILL.md

`write_skill_to_dir(skill_dir, name, description, body, **extras)` 改为单文件写入：

1. `_build_frontmatter(name, description, **extras)` 用 `yaml.dump(sort_keys=False, allow_unicode=True, default_flow_style=False)` 生成 frontmatter 字符串
2. `SKILL.md` 写入 `---\n` + frontmatter + `\n---\n\n` + body
3. 不再写入 `skill.json`

## ADDED: `_build_frontmatter()` 安全输出

- 使用 `yaml.dump()`（非手动字符串插值）
- `sort_keys=False` 保持字段顺序
- `allow_unicode=True` 支持中文
- `default_flow_style=False` 使用块样式而非流样式
- `yaml.safe_load` 读取，`yaml.dump` 写入——双向 YAML 安全

## MODIFIED: Storage 适配（`storage/local_json.py`）

### `_read_skill_bundle()`

- 调用 `parse_skill_dir()` 读取 frontmatter + body
- 若 `skill.json` 存在，合并运行时元数据（`id`, `rev`, `status`, `created_at`, `updated_at`, `tenant_id`, `source_skill_id`）
- 返回 dict 保持与上层接口一致

### `create_skill()`

- **用户可见字段**（`name`, `description`, `version`, `suggested_for`, `allowed_tools`, `license`, `body`）→ 写入 `SKILL.md` frontmatter
- **运行时元数据**（`id`, `rev`, `status`, `created_at`, `updated_at`, `tenant_id`, `source_skill_id`）→ 写入 `skill.json` sidecar
- 两者通过 `skill_dir` 目录名关联

### `update_skill()`

- CAS（`expected_rev`）检查不变
- 写入路径：用户字段 → frontmatter SKILL.md，运行时字段 → skill.json
- 删除 skill 时同时删除 `SKILL.md` + `skill.json` + 整个目录

## MODIFIED: 运行时元数据旁路

`skill.json` sidecar 仅包含运行时/基础设施字段：
- `id`, `rev`, `status`, `created_at`, `updated_at`, `tenant_id`, `source_skill_id`

**不**包含用户可见字段（`name`, `description`, `version`, `suggested_for` 等）。

## ADDED: `allowed_tools` / `license` 字段

`ParsedSkill` dataclass 新增：
- `allowed_tools: list[str] = field(default_factory=list)`
- `license: str = ""`

Spider_XHS 自身不消费这两个字段，但为生态兼容预留接口。前端 UI 暂不提供编辑入口。

## UNCHANGED

- `MemoryLayer.list_skills()` / `read_skill()` 接口不变
- `skills.read` 工具注册及 scope 隔离不变
- skill read budget 熔断逻辑不变
- 前端 API 契约不变
- Agent 系统 prompt 中的 `{skills_block}` 渲染不变
- 4 个 skill 池（intel/content/analyst + universal pool）结构不变
- 无 SKILL.md 的子目录被 `list_skills()` 跳过（行为不变）
- `list_skills()` 按字母序返回（行为不变）

## Change history entry

| Date | Change | Rationale |
|------|--------|-----------|
| 2026-05-19 | v4: 单文件 YAML frontmatter (`add-skill-format-realign`) | 对齐跨生态标准 (Claude Code, superpowers, claude-mem, Hermes Agent, OpenClaw)，零转换导入/导出，降低 hub 复杂度 |
