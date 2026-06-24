# Proposal: 把 Spider_XHS skill 物理格式升级为 Claude Code 标准 (SKILL.md + skill.json)

## Why

经 [Claude Code Skill Definition Anchored] 概念锚定后，确认 Skill 是**纯文本方法论**（pure-text methodology），其物理载体是 `SKILL.md` + `skill.json` 双文件、目录化的标准 bundle 格式。当前 Spider_XHS 的 skill 是单 `.md` 文件 + YAML frontmatter 的**简化变体**，与 Claude Code 生态标准存在如下偏差：

| 维度 | 当前 Spider_XHS | Claude Code 标准 |
|---|---|---|
| 物理载体 | 单 `.md` 文件 | `<skill-id>/` 目录 + `SKILL.md` + `skill.json` |
| 元数据声明 | YAML frontmatter 在 .md 顶部 | `skill.json` sidecar 文件 |
| 主要字段名 | `when_to_use` / `tools_referenced` | `description`（标准）|
| 附加参考材料 | 不支持 | `references/*.md` 子目录 |

不升级会造成的具体损失：

1. **未来 hub 安装器（计划中的 `add-skill-hub`）必须背负两套格式兼容**，工作量翻倍且引入 bug 面
2. **从 GitHub / clawhub.ai 拿到的社区 XHS 运营 skill 包无法直接消费**，需要每次手动转换
3. **XHS 业务团队产出的 skill 无法对外分发**（没有标准包结构）
4. **概念漂移**：当前 `_build_skills_block()` 文案把"读 skill"与"做事"混在一句话里描述（`agents/base.py:619-622`），LLM 会把"方法论加载"和"动作执行"耦合理解，违背锚定后的概念分离原则

本提案**只做格式与措辞规范化**，明确**不在范围内**的事项见 design.md §7。

## What

5 大改动 + 6 个内置 skill 迫迁 + 1 处措辞修订：

### 改动 1：物理格式升级

```
memory/{tenant}/{role}/skills/<skill-id>/
├── SKILL.md           ← 纯方法论文本，markdown body，无 frontmatter
├── skill.json         ← 元数据 sidecar
└── references/        ← 可选，纯文本补充材料 (*.md)
    └── *.md
```

### 改动 2：`skill.json` schema

```json
{
  "name": "钩子三段式写作",
  "description": "用户写笔记时遇到「开头怎么钩住读者」「3秒钩子」「反直觉钩子」",
  "version": "1.0.0",
  "suggested_for": ["content"]
}
```

- `name`（必需）、`description`（必需）、`version`（必需，semver）、`suggested_for`（可选，advisory）
- **删除** 原 `tools_referenced` 字段（per 锚定原则：方法论描述用什么 Tool 应在 SKILL.md body 内自然语言阐述，不需独立元数据）

### 改动 3：parser 重写

- `agents/skills.py:parse_skill_file()` → 改为 `parse_skill_dir(dir_path)`
- 读取流程：
  1. 校验 `dir/SKILL.md` 存在 + `dir/skill.json` 存在
  2. 解析 `skill.json` 取元数据
  3. 读 `SKILL.md` 原文为 body
  4. 返回 `ParsedSkill(name, description, suggested_for, version, body)`
- **彻底放弃** 单文件 + frontmatter 格式，不保留双轨兼容（迫迁一次完成）

### 改动 4：枚举逻辑改造

- `agents/memory.py:list_skills()` → 改为扫描 `skills/` **子目录**（每个子目录若含 `SKILL.md` 即认为是一个 skill），不再扫 `*.md`
- `agents/memory.py:read_skill()` → 返回 `<dir>/SKILL.md` 内容（保持原有签名）

### 改动 5：6 个内置 skill 迫迁

```
迫迁前                                  迫迁后
intel/skills/关键词扩展.md         →   intel/skills/关键词扩展/{SKILL.md, skill.json}
intel/skills/爆款规律分析.md       →   intel/skills/爆款规律分析/{SKILL.md, skill.json}
content/skills/钩子三段式写作.md   →   content/skills/钩子三段式写作/{SKILL.md, skill.json}
content/skills/评论引导句库.md     →   content/skills/评论引导句库/{SKILL.md, skill.json}
analyst/skills/10-3-1实操.md       →   analyst/skills/10-3-1实操/{SKILL.md, skill.json}
analyst/skills/流量异常排查.md     →   analyst/skills/流量异常排查/{SKILL.md, skill.json}
```

每个 skill 迫迁动作：
- 拆分原文件：frontmatter → `skill.json`、body → `SKILL.md`
- 字段映射：`when_to_use` → `description`、删除 `tools_referenced`
- `suggested_for` 字段按当前所在 role 自动填充（intel 的 skill 默认 `["intel"]`，依此类推）

### 改动 6：`_build_skills_block()` 措辞修订

`agents/base.py:615-622` 当前文案：

```
【🎯 可用 Skills（你积累的工作方法）】
  • <name> → <when_to_use 80字>
当某项 skill 的 when_to_use 匹配当前任务时，调用 skills.read(scope=<你的role>, name=<skill名>) 获取完整步骤执行。
```

修订为：

```
【🎯 可用方法论 (Methodology Library)】
  • <name> → <description 80字>
当某条方法论的 description 匹配当前任务场景时，调用 skills.read 把它注入你的工作记忆；
随后依方法论指导你的 Tool 调用与思考过程。方法论本身不执行任何动作，只指导你如何选择 Tool。
```

## Impact

**修改 capability：**
- `agent-skills` — 物理格式 / 元数据 schema / 解析器 / 枚举逻辑 / 加载措辞 五处 MODIFIED

**改造现有代码：**
- `agents/skills.py` — `parse_skill_file()` 改为 `parse_skill_dir()`，签名与返回字段变化
- `agents/memory.py` — `list_skills` / `read_skill` 改扫描子目录
- `agent_tools/skills.py` — handler 签名不变，但内部 mem.read_skill 调用语义变化（验证不破坏）
- `agents/base.py` — `_build_skills_block()` 措辞与字段名（`when_to_use` → `description`）
- `dashboard.py` — Skills 管理页编辑表单适配「目录 + skill.json + SKILL.md」三件套（references/ 子目录在本提案**不**做编辑 UI，仅展示文件列表）
- `tests/test_skills.py` — 18 个用例全部按新格式重写或调整断言
- `scripts/verify_skills.py` — 17 个用例同步调整

**改造内置数据：**
- `memory/default/{intel,content,analyst}/skills/` 下 6 个 `.md` 文件全部迫迁为目录形态

**不影响：**
- `add-skill-read-budget` 提案（其逻辑层与格式无关，熔断器照常工作）
- `ToolPolicy` / `enabled_tool_patterns`（`skills.read` 工具签名不变）
- `MemoryLayer` 写入注入检测（继续覆盖 SKILL.md 内容）
- `agents/{intel,content,analyst}.py` 的 system prompt 模板中 `{skills_block}` 占位符

## Risk

| 风险 | 缓解 |
|------|------|
| parser 重写引入解析 bug，6 个内置 skill 加载失败 | 迫迁前后跑 `scripts/verify_skills.py`，要求 22+/22+ 通过（17 老用例 + 5 新增格式用例）|
| Dashboard 编辑页改造破坏现有 CRUD 流程 | 保留单 skill 视图，仅把"frontmatter 表单"换成"skill.json 字段表单"；references/ 仅展示不编辑 |
| 测试套件需要大规模重写 | `tests/test_skills.py` 把 fixtures 集中到 `_build_skill_dir()` helper，一次重构所有 case |
| 迫迁脚本写错路径导致丢失 skill 内容 | 迫迁前 `git status` 干净 + 迫迁动作走脚本而非手工 + 完成后人工核对 6 个 SKILL.md body 完整 |
| LLM 看到新措辞 `methodology library` 反而困惑 | 措辞同时保留中文「可用方法论」+ 英文 `Methodology Library` 双标签；body 引导句明确「方法论本身不执行任何动作」 |
| 未来 hub 提案要求引入其他 skill.json 字段 | `skill.json` 解析时遇未识别字段静默忽略（向前兼容）|
| 现有 `_cached_system_prompt` 跨 run 缓存的旧 skills_block 文案 | 此提案变更属代码改动，重启进程后自然重生成；不需要主动 invalidate |

## 开发量估算

- 单 capability delta，6 个文件改 + 6 份内置 skill 迫迁 + 测试套件调整
- 不涉及 LLM 调用模式、不涉及任何后端 API、不涉及前端框架
- 预计 1 phase 完成（约 2-3 小时实施 + 验证 + Dashboard 手测）
