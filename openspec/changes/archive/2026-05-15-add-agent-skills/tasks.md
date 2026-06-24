# Tasks: 为每个 Sub Agent 引入 Skills 系统

## 1. Skills 加载与解析
- [x] 1.1 新增 `agents/skills.py`：YAML frontmatter 解析 + body 提取 + 字段校验
- [x] 1.2 `agents/memory.py` 加 `list_skills(tenant_id, scope)` 返回 [(name, when_to_use, path)]
- [x] 1.3 `agents/memory.py` 加 `read_skill(tenant_id, scope, name)` 返回完整文本
- [x] 1.4 写入复用现有 MemoryLayer.write，自动经过注入检测（skills 为只读加载，无写入路径）

## 2. 把 skills 接入 Agent 主循环
- [x] 2.1 `agents/base.py`：`_collect_memory_snapshot` 把 skills 索引（name + when_to_use）注入 snapshot 作为 `_derived__skills_block.md`
- [x] 2.2 三个 Sub Agent 的 system prompt 模板加 `{skills_block}` 区段
- [x] 2.3 Intel/Content/Analyst 的 `build_system_prompt` 渲染该区段

## 3. skills.read 工具
- [x] 3.1 新增 `agent_tools/skills.py`：注册 `skills.read` 工具，签名 `(scope, name) -> {ok, content}`
- [x] 3.2 工具内部检查 scope 是否等于调用 agent 的 role（防越界读其他 agent 的 skill）
- [x] 3.3 把 `skills.read` 加入三个 agent 的 enabled_tool_patterns

## 4. 初始 skills 内容（每个 agent 提供 2 个高质量样板）
- [x] 4.1 `memory/default/intel/skills/爆款规律分析.md`
- [x] 4.2 `memory/default/intel/skills/关键词扩展.md`
- [x] 4.3 `memory/default/content/skills/钩子三段式写作.md`
- [x] 4.4 `memory/default/content/skills/评论引导句库.md`
- [x] 4.5 `memory/default/analyst/skills/10-3-1实操.md`
- [x] 4.6 `memory/default/analyst/skills/流量异常排查.md`

## 5. Dashboard 集成（Skills 管理页）
- [x] 5.1 侧边栏加 `🎯 Skills 管理` 入口（在 Agent Console 下方）
- [x] 5.2 三个 tab 分别显示 intel/content/analyst 的 skills 列表
- [x] 5.3 支持：查看 / 新增 / 编辑 / 删除 单个 skill 文件
- [x] 5.4 编辑时校验 frontmatter，无效则提示

## 6. 验证
- [x] 6.1 `scripts/verify_skills.py`：自动化验证（解析、隔离、注册、MemoryLayer、agent patterns）→ 17/17 通过
- [x] 6.2 通过 Agent Console 验证:让 Intel 做"爆款规律分析"、Analyst 做"周报"、Content 做"库存清点"各跑一轮，均触发 skills.read 首发。
  > 验收证据: xhs_data/audit/audit_20260518.jsonl 当日 8 次 agent 跑均触发
  > skills.read 首发(intel/analyst/content 全覆盖),含 task_id="debug_run"
  > 接力诊断专跑(22:08)真实调用 6 个工具。

## 7. 归档
- [x] 7.1 spec 合入 `openspec/specs/agent-skills/spec.md`
- [x] 7.2 移动到 `openspec/changes/archive/2026-05-15-add-agent-skills/`（副本）
