# 提案：Skill 格式标准化 — 单文件 YAML frontmatter

## 为什么现在做

Spider_XHS 的 skill bundle-format（双文件 `skill.json` + `SKILL.md`）是 2026-05-17 才定稿的格式，仅 2 天后（2026-05-19）即发现它偏离了跨生态事实标准。沉没成本极小——仅 17 个内置 skill + parser/storage 代码——是 hub 立项前最后窗口期。

## 为什么这个改动

Anthropic Claude Code / Agent SDK、superpowers、claude-mem、Hermes Agent、OpenClaw 等都已收敛到**单文件 SKILL.md 内嵌 YAML frontmatter** 格式。对齐标准带来三个不可对抗的网络效应：

1. **零转换导入**——社区 skill zip 下载后直接 `cp -r` 即可使用，无需格式转换
2. **零转换导出**——Spider_XHS 自产 XHS 运营方法论 skill 可直接对外分发
3. **hub 复杂度大降**——未来说 `add-skill-hub` 时 `install = unzip + cp -r`

## 范围 In Scope

- Parser 重写：`agents/skills.py` — 从 `SKILL.md` 读取 YAML frontmatter，不再依赖 `skill.json`
- Storage 适配：`storage/local_json.py` — 创建/更新/读取统一走 frontmatter
- 迫迁脚本：`scripts/migrate_skills_to_frontmatter.py` — 一次性的 17 个内置 skill 转换
- 全部 4 个 skill 池：intel/content/analyst + universal/_universal
- Dashboard Skills 管理页：单文件读写
- Tests：删 2 加 5，33/33 pass；verify 30/30
- 行为验证：DAG 诊断确认 skills.read 在 agent loop 中正常工作

## 范围 Out of Scope

- `add-skill-hub`：zip 上传/解包安装入口（独立提案）
- `add-skill-assignment`：skill 派发语义（独立提案）
- 前端 UI 加 `allowed_tools` / `license` 编辑入口（等真有需求再补）
- skill subdir 扩展（`scripts/`、`assets/`）的扫描与 UI 展示——沿用当前 `references/` 同等待遇

## 设计决策

1. **运行时元数据旁路**：`id/rev/status/created_at/updated_at/tenant_id/source_skill_id` 保留在 `skill.json` sidecar，不进入 frontmatter。CAS (expected_rev) 逻辑不变。
2. **前向兼容字段**：`allowed_tools: list[str]` 和 `license: str` 加入 `ParsedSkill`，Spider_XHS 自身不消费，但给生态兼容预留接口。
3. **未识别 frontmatter 字段**：静默忽略，不报错。
4. **YAML 安全**：使用 `yaml.safe_load` 读取，`yaml.dump(sort_keys=False)` 写入，规避 `yes/no/true/false` 布尔歧义。
