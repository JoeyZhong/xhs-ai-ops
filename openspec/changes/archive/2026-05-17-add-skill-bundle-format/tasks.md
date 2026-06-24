# Tasks: 把 Spider_XHS skill 物理格式升级为 Claude Code 标准

> ⚠️ 本提案是后续 `add-skill-hub` / `add-skill-assignment` 的前置依赖，**不要顺手把 hub 或 assignment 的功能塞进来**。范围严格限定在「格式 + 迫迁 + 措辞」。

## 1. Parser 重写（`agents/skills.py`）
- [x] 1.1 新增 `parse_skill_dir(dir_path: Path) -> ParsedSkill`，签名替代旧 `parse_skill_file(content)`
- [x] 1.2 `ParsedSkill` dataclass 字段更新：
  - 删除 `when_to_use`、`tools_referenced`
  - 新增 `description: str`、`version: str = "1.0.0"`、`suggested_for: list[str] = []`
  - 保留 `name: str`、`body: str`
- [x] 1.3 解析流程：先读 `dir/skill.json` 取元数据 → 再读 `dir/SKILL.md` 作为 body；任一文件缺失则抛 `SkillParseError`
- [x] 1.4 `skill.json` JSON 解析失败、必填字段缺失（`name`/`description`）→ 抛 `SkillParseError`
- [x] 1.5 `version` 缺省时回退 `"1.0.0"`；`suggested_for` 缺省时回退 `[]`
- [x] 1.6 删除旧 `parse_skill_file()` 函数及其相关测试用例（不保留向后兼容）
- [x] 1.7 顶部 docstring 更新：明确锚定后的 SKILL.md + skill.json 标准

## 2. 枚举逻辑改造（`agents/memory.py`）
- [x] 2.1 `list_skills(tenant_id, scope)`：扫描 `<scope_dir>/skills/` 下的**子目录**（不再扫 `*.md`），每个含 `SKILL.md` 的目录算一个 skill
- [x] 2.2 `read_skill(tenant_id, scope, name)`：定位 `<scope_dir>/skills/<name>/SKILL.md`，返回纯 body 文本（无 frontmatter）
- [x] 2.3 子目录排序：按目录名字母序，保证 list_skills 输出稳定
- [x] 2.4 路径找不到 / 不是目录 → 返回空列表或 None（与旧行为一致）

## 3. Tool 适配（`agent_tools/skills.py`）
- [x] 3.1 `_read_skill_handler` 内部调用 `mem.read_skill(...)` 的语义不变，只需验证返回的 body 不含 frontmatter（确保 LLM 注入的是纯方法论文本）
- [x] 3.2 工具 schema description 更新为「Inject a skill's methodology text into context, by name and scope」
- [x] 3.3 与 `add-skill-read-budget` 的熔断逻辑**不冲突**：本次只改 mem.read_skill 的返回内容形态，计数器/clear_budget 逻辑独立

## 4. 措辞修订（`agents/base.py`）
- [x] 4.1 `_build_skills_block()` 标题改为 `【🎯 可用方法论 (Methodology Library)】`
- [x] 4.2 每条 skill 摘要展示字段改为 `s.description`（替代 `s.when_to_use`），仍取前 80 字
- [x] 4.3 引导句修订为定稿（**字面量按 design.md §4 不可改**）：
```
当某条方法论的 description 匹配当前任务场景时，调用 skills.read 把它注入你的工作记忆；
随后依方法论指导你的 Tool 调用与思考过程。方法论本身不执行任何动作，只指导你如何选择 Tool。
```
- [x] 4.4 验证：修订后引导句不含 `skills.read(scope=, name=)` 完整调用格式字面量，避免与 stuck-detection 正则冲突（参考 `add-skill-read-budget` design.md §3 教训）

## 5. 内置 6 个 skill 迫迁
- [x] 5.1 写一次性脚本 `scripts/migrate_skills_to_bundle.py`（仅用于本次迫迁，迫迁完即归档 `scripts/archive/`），扫描 `memory/default/{intel,content,analyst}/skills/*.md`：
  - 读取原 YAML frontmatter + body
  - 在同目录创建 `<name>/` 子目录
  - 把 body（去 frontmatter）写入 `<name>/SKILL.md`
  - 派生 `skill.json`：`name` 沿用、`description` 从 `when_to_use` 取、`version: "1.0.0"`、`suggested_for: [<当前 role>]`
  - 删除原 `<name>.md` 文件
- [x] 5.2 脚本输出每个 skill 的迫迁前后路径对照表，便于人工核对
- [x] 5.3 执行脚本：6 个 skill 全部迫迁到新格式
- [x] 5.4 人工 diff 检查：6 个 `SKILL.md` 的 body 字字对齐原 markdown 正文部分（无丢失）
- [x] 5.5 人工 diff 检查：6 个 `skill.json` 字段正确（特别是中文 `description` 编码是 UTF-8 无 BOM）

## 6. Dashboard 适配（`dashboard.py`）
- [x] 6.1 Skills 管理页的「编辑表单」从「YAML frontmatter 文本框 + body 文本框」改为「skill.json 字段表单（4 个字段） + SKILL.md 文本框」
- [x] 6.2 「新建 skill」流程：用户填 name → 系统自动创建 `<name>/` 目录 → 生成空 SKILL.md + skill.json
- [x] 6.3 「删除 skill」流程：递归删除整个 `<name>/` 目录（包括 references/）
- [x] 6.4 `references/` 子目录**只展示文件列表 + 内容预览**，不做新增/编辑/删除（留给 `add-skill-hub` 后续提案处理）
- [x] 6.5 lint 校验：保存时校验 `skill.json` 是合法 JSON 且必填字段齐全，否则拒保

## 7. 单元测试（`tests/test_skills.py`）
- [x] 7.1 抽取 `_build_skill_dir(tmp_path, name, description, body, suggested_for, version)` helper，集中创建测试用 skill 目录
- [x] 7.2 删除旧 `parse_skill_file` 相关测试用例（含 frontmatter-only、缺失字段等老形态用例）
- [x] 7.3 新增 5 类 case：
  - `test_parse_skill_dir_valid` — 标准目录解析返回完整 ParsedSkill
  - `test_parse_skill_dir_missing_skill_md` — 缺 SKILL.md 抛 SkillParseError
  - `test_parse_skill_dir_missing_skill_json` — 缺 skill.json 抛 SkillParseError
  - `test_parse_skill_dir_invalid_json` — skill.json 是损坏 JSON 抛 SkillParseError
  - `test_parse_skill_dir_missing_required_field` — name 或 description 缺失抛 SkillParseError
  - `test_parse_skill_dir_defaults` — version 和 suggested_for 缺省时返回默认值
  - `test_list_skills_scans_subdirs` — 多个子目录被正确枚举
  - `test_list_skills_skips_non_skill_dir` — 子目录中没 SKILL.md 的被跳过
  - `test_read_skill_returns_pure_body` — read_skill 返回的 body 不含 frontmatter（确保 LLM 注入纯文本）
- [x] 7.4 保留并更新 18 个原有用例中仍有意义的部分（如 scope 隔离、不存在的 skill 等）；预期最终 ≥20 个用例

## 8. 验证脚本（`scripts/verify_skills.py`）
- [x] 8.1 17 个原有 case 全部更新为新格式
- [x] 8.2 新增 5 个格式校验 case（与 §7.3 对应）
- [x] 8.3 目标：22/22 通过
- [x] 8.4 同时跑 `add-skill-read-budget` 的验证（如已实施），确认两份提案的代码改动不互相破坏

## 9. CLAUDE.md 同步
- [x] 9.1 更新 `CLAUDE.md` 「Agent skills」段落（如有引用 frontmatter 字段名 `when_to_use`，统一改 `description`）
- [x] 9.2 在「目录结构速查」中 `memory/default/` 部分更新 skill 路径示例为目录形态

## 10. 归档
- [x] 10.1 把本 change 的 `specs/agent-skills/spec.md` 中 MODIFIED 条款合入 `openspec/specs/agent-skills/spec.md`
- [x] 10.2 把 `openspec/changes/add-skill-bundle-format/` 移动到 `openspec/changes/archive/<YYYY-MM-DD>-add-skill-bundle-format/`
- [x] 10.3 `scripts/migrate_skills_to_bundle.py` 一次性迫迁脚本移入 `scripts/archive/`
