"""Test: Skills 系统 — frontmatter 格式解析/枚举/隔离/工具注册."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

@pytest.fixture(autouse=True)
def _default_skills_source_file(monkeypatch):
    """Keep file-mode tests independent from the developer's settings.json."""
    import agent_tools.skills as mod_skills

    monkeypatch.setattr(mod_skills, "_load_skills_source", lambda: "files")

from agents.skills import (
    ParsedSkill,
    SkillParseError,
    list_skills,
    parse_skill_dir,
    read_skill_content,
)


# ── helper: 创建测试用 skill 目录（frontmatter 格式）────────────────────────

def _build_skill_dir(
    tmp_path: Path,
    name: str = "test-skill",
    description: str = "for testing",
    body: str = "# Steps\n1. Do something",
    suggested_for: list[str] | None = None,
    version: str = "1.0.0",
    allowed_tools: list[str] | None = None,
    extra_frontmatter: dict | None = None,
) -> Path:
    """在 tmp_path 下创建 skill 目录（frontmatter SKILL.md），返回路径。"""
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True)
    fm_lines = [f"name: {name}", f"description: {description}"]
    if version != "1.0.0":
        fm_lines.append(f"version: {version}")
    if suggested_for:
        items = "\n" + "\n".join(f"  - {s}" for s in suggested_for)
        fm_lines.append(f"suggested_for:{items}")
    if allowed_tools:
        items = "\n" + "\n".join(f"  - {t}" for t in allowed_tools)
        fm_lines.append(f"allowed_tools:{items}")
    if extra_frontmatter:
        for k, v in extra_frontmatter.items():
            fm_lines.append(f"{k}: {v}")
    (skill_dir / "SKILL.md").write_text(
        "---\n" + "\n".join(fm_lines) + "\n---\n\n" + body, encoding="utf-8")
    return skill_dir


# ── 1. parse_skill_dir 基础解析 ─────────────────────────────────────────

def test_parse_skill_dir_valid():
    """标准 frontmatter 目录解析返回完整 ParsedSkill。"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        skill_dir = Path(tmp) / "my-skill"
        _build_skill_dir(Path(tmp), name="my-skill",
                         description="test description",
                         body="# Body\ncontent")
        ps = parse_skill_dir(skill_dir)
        assert ps.name == "my-skill"
        assert ps.description == "test description"
        assert ps.version == "1.0.0"
        assert ps.suggested_for == []
        assert "# Body" in ps.body
        assert "content" in ps.body
        assert not ps.body.startswith("---")


def test_parse_skill_dir_missing_skill_md():
    """缺 SKILL.md 抛 SkillParseError。"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        skill_dir = Path(tmp) / "bad-skill"
        skill_dir.mkdir()
        with pytest.raises(SkillParseError, match="SKILL.md"):
            parse_skill_dir(skill_dir)


def test_parse_skill_dir_missing_required_field():
    """frontmatter 中 name 或 description 缺失抛 SkillParseError。"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        # 缺 name
        skill_dir = Path(tmp) / "no-name"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\ndescription: x\n---\n\n# body", encoding="utf-8")
        with pytest.raises(SkillParseError, match="name"):
            parse_skill_dir(skill_dir)

        # 缺 description
        skill_dir2 = Path(tmp) / "no-desc"
        skill_dir2.mkdir()
        (skill_dir2 / "SKILL.md").write_text(
            "---\nname: test\n---\n\n# body", encoding="utf-8")
        with pytest.raises(SkillParseError, match="description"):
            parse_skill_dir(skill_dir2)


def test_parse_skill_dir_defaults():
    """version 和 suggested_for 缺省时返回默认值。"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        skill_dir = Path(tmp) / "defaults"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: defaults\ndescription: test desc\n---\n\n# body",
            encoding="utf-8")
        ps = parse_skill_dir(skill_dir)
        assert ps.version == "1.0.0"
        assert ps.suggested_for == []


def test_parse_skill_dir_extra_fields_ignored():
    """frontmatter 中未识别字段静默忽略。"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        skill_dir = Path(tmp) / "extra"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: extra\ndescription: desc\n"
            "tools_referenced: [tool.a]\nauthor: test\n---\n\n# body",
            encoding="utf-8")
        ps = parse_skill_dir(skill_dir)
        assert ps.name == "extra"
        assert ps.description == "desc"
        assert not hasattr(ps, "tools_referenced")


# ── 新增 frontmatter 专项测试 ──────────────────────────────────────────────

def test_parse_skill_dir_valid_frontmatter():
    """标准 frontmatter + body 解析为 ParsedSkill。"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        skill_dir = Path(tmp) / "fm-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: fm-skill\ndescription: test description\n"
            "version: 2.0.0\nsuggested_for:\n  - intel\n---\n\n# Body\ncontent",
            encoding="utf-8")
        ps = parse_skill_dir(skill_dir)
        assert ps.name == "fm-skill"
        assert ps.description == "test description"
        assert ps.version == "2.0.0"
        assert ps.suggested_for == ["intel"]
        assert "# Body" in ps.body
        assert "content" in ps.body
        assert not ps.body.startswith("---")


def test_parse_skill_dir_missing_frontmatter():
    """SKILL.md 没有 frontmatter（无 --- 块）→ SkillParseError。"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        skill_dir = Path(tmp) / "no-fm"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "# Just body\nNo frontmatter.", encoding="utf-8")
        with pytest.raises(SkillParseError, match="frontmatter"):
            parse_skill_dir(skill_dir)


def test_parse_skill_dir_invalid_yaml():
    """frontmatter YAML 语法错误 → SkillParseError。"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        skill_dir = Path(tmp) / "bad-yaml"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: bad\nbad_yaml: [unclosed\n---\n\n# body",
            encoding="utf-8")
        with pytest.raises(SkillParseError):
            parse_skill_dir(skill_dir)


def test_parse_skill_dir_extra_frontmatter_fields_preserved():
    """allowed_tools / license 被保留到 ParsedSkill。"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        skill_dir = Path(tmp) / "ext-field"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: ext-field\ndescription: desc\n"
            "allowed_tools:\n  - tool.a\n  - tool.b\nlicense: MIT\n---\n\n# body",
            encoding="utf-8")
        ps = parse_skill_dir(skill_dir)
        assert ps.allowed_tools == ["tool.a", "tool.b"]
        assert ps.license == "MIT"


def test_parse_skill_dir_unknown_field_ignored():
    """未识别 frontmatter 字段不抛错，静默忽略。"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        skill_dir = Path(tmp) / "unknown"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: unknown\ndescription: desc\n"
            "future_field: some_value\nanother_unknown: 42\n---\n\n# body",
            encoding="utf-8")
        ps = parse_skill_dir(skill_dir)
        assert ps.name == "unknown"
        assert ps.description == "desc"


# ── 2. list_skills + read_skill_content ───────────────────────────────────

def test_list_skills_empty_dir(tmp_path):
    """No skills/ dir → empty list."""
    scope_dir = tmp_path / "intel"
    scope_dir.mkdir()
    skills = list_skills(scope_dir)
    assert skills == []


def test_list_skills_scans_subdirs(tmp_path):
    """多个子目录被正确枚举。"""
    skills_dir = tmp_path / "intel" / "skills"
    skills_dir.mkdir(parents=True)
    _build_skill_dir(skills_dir, name="skill-a", description="desc a")
    _build_skill_dir(skills_dir, name="skill-b", description="desc b")
    # 一个无 SKILL.md 的目录应被跳过
    empty_dir = skills_dir / "empty"
    empty_dir.mkdir()

    skills = list_skills(tmp_path / "intel")
    assert len(skills) == 2
    names = [s.name for s in skills]
    assert "skill-a" in names
    assert "skill-b" in names


def test_list_skills_skips_non_skill_dir(tmp_path):
    """子目录中没 SKILL.md 的被跳过。"""
    skills_dir = tmp_path / "intel" / "skills"
    skills_dir.mkdir(parents=True)
    _build_skill_dir(skills_dir, name="valid-skill", description="desc")
    # 没有 SKILL.md 的目录
    no_skill = skills_dir / "no-skill-dir"
    no_skill.mkdir()

    skills = list_skills(tmp_path / "intel")
    assert len(skills) == 1
    assert skills[0].name == "valid-skill"


def test_list_skills_alphabetical_order(tmp_path):
    """subdir 按字母序排序。"""
    skills_dir = tmp_path / "intel" / "skills"
    skills_dir.mkdir(parents=True)
    _build_skill_dir(skills_dir, name="z-skill", description="desc")
    _build_skill_dir(skills_dir, name="a-skill", description="desc")
    _build_skill_dir(skills_dir, name="m-skill", description="desc")

    skills = list_skills(tmp_path / "intel")
    assert [s.name for s in skills] == ["a-skill", "m-skill", "z-skill"]


def test_read_skill_content_found(tmp_path):
    """read_skill_content 按 name 找到并返回纯 body。"""
    skills_dir = tmp_path / "intel" / "skills"
    skills_dir.mkdir(parents=True)
    _build_skill_dir(skills_dir, name="testing-skill",
                     description="test desc",
                     body="# Steps\n1. Collect data\n2. Analyze")

    content = read_skill_content(tmp_path / "intel", "testing-skill")
    assert content is not None
    assert "# Steps" in content
    assert "1. Collect data" in content
    # 确保返回的是纯 body，不含 frontmatter
    assert not content.startswith("---")


def test_read_skill_returns_pure_body(tmp_path):
    """read_skill 返回的 body 不含 frontmatter（确保 LLM 注入纯文本）。"""
    skills_dir = tmp_path / "intel" / "skills"
    skills_dir.mkdir(parents=True)
    _build_skill_dir(skills_dir, name="pure-body", description="desc",
                     body="# Just body\nNo YAML here.")
    content = read_skill_content(tmp_path / "intel", "pure-body")
    assert content is not None
    assert not content.startswith("---")


def test_read_skill_content_not_found(tmp_path):
    """不存在的 skill 返回 None。"""
    content = read_skill_content(tmp_path / "intel", "不存在")
    assert content is None


# ── 3. MemoryLayer integration ────────────────────────────────────────

class FakeStorage:
    """Minimal storage stub with memory_dir attribute."""
    def __init__(self, memory_dir: Path):
        self.memory_dir = memory_dir


def make_memory(tmp_path: Path):
    from agents.memory import MemoryLayer
    return MemoryLayer(storage=FakeStorage(tmp_path))


def test_memory_list_skills(tmp_path):
    mem = make_memory(tmp_path)
    skills_dir = tmp_path / "default" / "intel" / "skills"
    skills_dir.mkdir(parents=True)
    _build_skill_dir(skills_dir, name="mem-skill", description="desc")

    result = mem.list_skills("default", "intel")
    assert len(result) == 1
    assert result[0].name == "mem-skill"
    assert result[0].description == "desc"


def test_memory_read_skill(tmp_path):
    mem = make_memory(tmp_path)
    skills_dir = tmp_path / "default" / "intel" / "skills"
    skills_dir.mkdir(parents=True)
    _build_skill_dir(skills_dir, name="mem-read", description="desc",
                     body="# Read test\ncontent here")

    result = mem.read_skill("default", "intel", "mem-read")
    assert result is not None
    assert "# Read test" in result
    assert not result.startswith("---")


# ── 4. skills.read tool scope isolation ───────────────────────────────

def test_skills_tool_scope_check():
    """skills.read tool rejects cross-scope access."""
    from agent_tools.skills import _check_scope

    assert _check_scope("intel", "intel") is True
    assert _check_scope("content", "content") is True
    assert _check_scope("analyst", "analyst") is True
    assert _check_scope("content", "intel") is False
    assert _check_scope("intel", "analyst") is False
    assert _check_scope("analyst", "content") is False


def test_skills_tool_not_found():
    """skills.read returns ok=False when no memory layer in context."""
    from agent_tools.registry import invoke, ToolContext

    ctx = ToolContext(extra={"agent_role": "intel"})
    result = invoke("skills.read", {"scope": "intel", "name": "不存在"}, ctx)
    assert result["ok"] is False
    assert "error" in result


# ── 5. Tool registration ──────────────────────────────────────────────

def test_skills_tool_registered():
    """skills.read is registered and accessible."""
    from agent_tools.registry import list_tools
    all_tools = list_tools()
    assert "skills.read" in all_tools


def test_skills_tool_schema():
    """skills.read has correct schema params."""
    from agent_tools.registry import get
    t = get("skills.read")
    props = t.schema["parameters"]["properties"]
    assert "scope" in props
    assert "name" in props
    assert "skill_id" in props
    assert props["scope"]["enum"] == ["intel", "content", "analyst"]
    assert "scope" in t.schema["parameters"]["required"]


# ── 6. ParsedSkill dataclass ───────────────────────────────────────────

def test_parsed_skill_defaults():
    """ParsedSkill 默认值正确。"""
    ps = ParsedSkill()
    assert ps.name == ""
    assert ps.description == ""
    assert ps.suggested_for == []
    assert ps.version == "1.0.0"
    assert ps.body == ""
    assert ps.allowed_tools == []
    assert ps.license == ""


def test_parsed_skill_no_when_to_use_or_tools():
    """ParsedSkill 无 when_to_use 和 tools_referenced 字段。"""
    ps = ParsedSkill(name="test", description="desc", body="# body")
    assert not hasattr(ps, "when_to_use")
    assert not hasattr(ps, "tools_referenced")
    assert ps.description == "desc"


def test_parsed_skill_suggested_for(tmp_path):
    """suggested_for 正确存储。"""
    _build_skill_dir(tmp_path, name="sk", description="d",
                     suggested_for=["intel", "analyst"])
    ps = parse_skill_dir(tmp_path / "sk")
    assert ps.suggested_for == ["intel", "analyst"]


def test_parsed_skill_allowed_tools(tmp_path):
    """allowed_tools / license 正确存储。"""
    _build_skill_dir(tmp_path, name="at", description="d",
                     allowed_tools=["tool.a", "tool.b"])
    ps = parse_skill_dir(tmp_path / "at")
    assert ps.allowed_tools == ["tool.a", "tool.b"]


# ── 7. skill_read_budget 熔断 ─────────────────────────────────────────────

from agent_tools.skills import (
    _BUDGET_COUNTERS,
    _BUDGET_EXHAUSTED_RESPONSE,
    _load_budget,
    clear_budget as _clear_skill_budget,
)


def _build_scope_with_skills(basedir: Path, scope: str, names: list[str]) -> Path:
    """在 basedir/{tenant}/{scope}/skills/ 下建 N 个 skill 目录。"""
    skills_dir = basedir / "default" / scope / "skills"
    skills_dir.mkdir(parents=True)
    for name in names:
        _build_skill_dir(skills_dir, name=name, description=f"desc {name}",
                         body=f"# {name}\nSteps for {name}")
    return skills_dir


@pytest.fixture(autouse=True)
def _reset_budget():
    """每个测试前清空 budget counters。"""
    _BUDGET_COUNTERS.clear()
    yield
    _BUDGET_COUNTERS.clear()


def _make_ctx(tmp_path: str, task_id: str = "t1", agent_role: str = "intel") -> "ToolContext":
    from agent_tools.registry import ToolContext
    from agents.memory import MemoryLayer

    class _FakeStorage:
        def __init__(self, p: str):
            self.memory_dir = Path(p)
    mem = MemoryLayer(storage=_FakeStorage(tmp_path))
    return ToolContext(
        tenant_id="default",
        task_id=task_id,
        extra={"memory": mem, "agent_role": agent_role, "task_id": task_id},
    )


def test_budget_exhausted_after_n_reads(tmp_path):
    """budget=2: 前 2 次 read 成功，第 3 次返回熔断。"""
    _ = _build_scope_with_skills(tmp_path, "intel", ["s1", "s2", "s3"])
    from agent_tools.registry import invoke

    ctx = _make_ctx(tmp_path, task_id="exhaust-test")

    r1 = invoke("skills.read", {"scope": "intel", "name": "s1"}, ctx)
    assert r1["ok"] is True
    r2 = invoke("skills.read", {"scope": "intel", "name": "s2"}, ctx)
    assert r2["ok"] is True
    r3 = invoke("skills.read", {"scope": "intel", "name": "s3"}, ctx)
    assert r3["ok"] is False
    assert "SKILL_BUDGET_EXHAUSTED" in r3.get("error", "")


def test_clear_budget_resets_counter(tmp_path):
    """耗尽后 clear_budget 让额度恢复。"""
    _ = _build_scope_with_skills(tmp_path, "intel", ["a", "b", "c"])
    from agent_tools.registry import invoke

    ctx = _make_ctx(tmp_path, task_id="clear-test")

    invoke("skills.read", {"scope": "intel", "name": "a"}, ctx)
    invoke("skills.read", {"scope": "intel", "name": "b"}, ctx)
    r3 = invoke("skills.read", {"scope": "intel", "name": "c"}, ctx)
    assert r3["ok"] is False  # 耗尽

    _clear_skill_budget("clear-test")

    r4 = invoke("skills.read", {"scope": "intel", "name": "c"}, ctx)
    assert r4["ok"] is True


def test_failed_read_does_not_consume(tmp_path):
    """失败路径 (scope mismatch / not found / missing args) 不递增计数器。"""
    _ = _build_scope_with_skills(tmp_path, "intel", ["only-one"])
    from agent_tools.registry import invoke

    ctx = _make_ctx(tmp_path, task_id="fail-no-consume")

    # 3 次失败：scope mismatch + not found + missing name
    r1 = invoke("skills.read", {"scope": "analyst", "name": "only-one"}, ctx)  # mismatch
    assert r1["ok"] is False

    r2 = invoke("skills.read", {"scope": "intel", "name": "不存在"}, ctx)
    assert r2["ok"] is False

    r3 = invoke("skills.read", {"scope": "intel"}, ctx)  # 缺 name
    assert r3["ok"] is False

    # 额度应未消耗，仍可读
    r4 = invoke("skills.read", {"scope": "intel", "name": "only-one"}, ctx)
    assert r4["ok"] is True

    # 再读一次（budget=2 → 第 2 次应为成功，第 3 次才耗尽）
    r5 = invoke("skills.read", {"scope": "intel", "name": "only-one"}, ctx)
    assert r5["ok"] is True

    r6 = invoke("skills.read", {"scope": "intel", "name": "only-one"}, ctx)
    assert r6["ok"] is False  # budget 2 耗尽


def test_read_without_idempotency_counts_each_call(tmp_path):
    """同一 skill 重复读因 idempotency 不适用，每次都计数。"""
    _ = _build_scope_with_skills(tmp_path, "intel", ["dup-skill"])
    from agent_tools.registry import invoke

    ctx = _make_ctx(tmp_path, task_id="no-idem-test")

    # skills.read 不在 idempotency 白名单中，每次调用都经过 handler
    # budget=2: 前 2 次应成功，第 3 次熔断
    r1 = invoke("skills.read", {"scope": "intel", "name": "dup-skill"}, ctx)
    assert r1["ok"] is True
    r2 = invoke("skills.read", {"scope": "intel", "name": "dup-skill"}, ctx)
    assert r2["ok"] is True
    r3 = invoke("skills.read", {"scope": "intel", "name": "dup-skill"}, ctx)
    assert r3["ok"] is False  # budget 2 耗尽


def test_load_budget_falls_back_when_settings_missing(monkeypatch):
    """settings.json 缺 skill_read_budget 时 _load_budget 返回 2。"""
    import tempfile, json
    from pathlib import Path
    from agent_tools.skills import _SETTINGS_PATH

    orig = _SETTINGS_PATH
    with tempfile.TemporaryDirectory() as tmp:
        dummy_settings = Path(tmp) / "settings.json"
        dummy_settings.write_text(json.dumps({"llm_provider": "kimi"}), encoding="utf-8")
        # 必须替换模块级路径，否则 _load_budget 仍读原文件
        import agent_tools.skills as mod_skills
        monkeypatch.setattr(mod_skills, "_SETTINGS_PATH", dummy_settings)
        assert _load_budget() == 2

    # 文件不存在
    with tempfile.TemporaryDirectory() as tmp:
        dummy_missing = Path(tmp) / "nonexistent.json"
        monkeypatch.setattr(mod_skills, "_SETTINGS_PATH", dummy_missing)
        assert _load_budget() == 2


def test_budget_no_task_id_skips_check(tmp_path):
    """task_id 为空时跳过预算检查（兼容直接调用场景）。"""
    _ = _build_scope_with_skills(tmp_path, "intel", ["free-skill"])
    from agent_tools.registry import invoke
    from agent_tools.registry import ToolContext
    from agents.memory import MemoryLayer

    class _FakeStorage:
        def __init__(self, p: str):
            self.memory_dir = Path(p)
    mem = MemoryLayer(storage=_FakeStorage(str(tmp_path)))
    ctx = ToolContext(
        tenant_id="default",
        task_id="",  # no task_id
        extra={"memory": mem, "agent_role": "intel"},
    )

    # 没有 task_id 应该可以无限读
    for i in range(10):
        r = invoke("skills.read", {"scope": "intel", "name": "free-skill"}, ctx)
        assert r["ok"] is True, f"第 {i+1} 次 read 应跳过预算"


# ── 8. equipment_loader.render_prompt_block tests ─────────────────────────

EXPECTED_EQUIPPED = [
    {"id": "uuid-111", "name": "测试技能导入", "description": "用于测试的技能"},
    {"id": "uuid-222", "name": "钩子三段式写作", "description": "三段式标题钩子写作方法"},
]


def test_render_prompt_block_empty():
    """空装备列表返回占位语句。"""
    from agents.equipment_loader import render_prompt_block
    result = render_prompt_block([])
    assert "暂无装备" in result


def test_render_prompt_block_lists_skills():
    """装备列表包含 skill_id, name, description。"""
    from agents.equipment_loader import render_prompt_block
    result = render_prompt_block(EXPECTED_EQUIPPED)
    assert "skill_id=uuid-111" in result
    assert "测试技能导入" in result
    assert "用于测试的技能" in result
    assert "skill_id=uuid-222" in result
    assert "钩子三段式写作" in result
    assert "三" in result


def test_render_prompt_block_has_read_instruction():
    """prompt block 包含明确的 skills.read 调用指令。"""
    from agents.equipment_loader import render_prompt_block
    result = render_prompt_block(EXPECTED_EQUIPPED)
    assert "skills.read" in result
    assert "skill_id" in result


# ── 9. skills.read hub mode tests ─────────────────────────────────────────

class _FakeHubBackend:
    """Minimal backend stub for hub mode tests."""

    def __init__(self):
        self.skills = {
            "uuid-111": {
                "id": "uuid-111",
                "name": "测试技能导入",
                "body": "# 测试技能\n1. 执行步骤一\n2. 执行步骤二",
            },
            "uuid-222": {
                "id": "uuid-222",
                "name": "钩子三段式写作",
                "body": "# 钩子写作\n1. 写开头\n2. 写正文\n3. 写结尾",
            },
        }
        self.equipment = {
            "content": ["uuid-111", "uuid-222"],
            "analyst": [],
        }

    def list_equipment(self, tenant_id: str, agent_role: str) -> list[dict]:
        result = []
        for sid in self.equipment.get(agent_role, []):
            s = self.skills.get(sid, {})
            result.append({
                "id": s["id"],
                "name": s["name"],
                "description": f"desc_{s['name']}",
            })
        return result

    def get_skill(self, skill_id: str, tenant_id: str) -> dict:
        s = self.skills.get(skill_id)
        if s is None:
            raise KeyError(f"skill '{skill_id}' not found")
        return dict(s)


def _hub_ctx(backend=None, agent_role="content", task_id="t1"):
    """构建 hub 模式的 ToolContext。"""
    from agent_tools.registry import ToolContext
    return ToolContext(
        tenant_id="default",
        task_id=task_id,
        storage=backend or _FakeHubBackend(),
        extra={"agent_role": agent_role, "task_id": task_id},
    )


def test_hub_read_by_skill_id(monkeypatch):
    """hub 模式：按 skill_id 读取已装备 skill。"""
    import agent_tools.skills as mod_skills
    monkeypatch.setattr(mod_skills, "_load_skills_source", lambda: "hub")

    from agent_tools.registry import invoke
    ctx = _hub_ctx(agent_role="content")
    result = invoke("skills.read", {"scope": "content", "skill_id": "uuid-111"}, ctx)
    assert result["ok"] is True
    assert "测试技能" in result["data"]["content"]
    assert "执行步骤一" in result["data"]["content"]


def test_hub_read_by_name(monkeypatch):
    """hub 模式：按 exact name 读取已装备 skill。"""
    import agent_tools.skills as mod_skills
    monkeypatch.setattr(mod_skills, "_load_skills_source", lambda: "hub")

    from agent_tools.registry import invoke
    ctx = _hub_ctx(agent_role="content")
    result = invoke("skills.read", {"scope": "content", "name": "钩子三段式写作"}, ctx)
    assert result["ok"] is True
    assert "钩子写作" in result["data"]["content"]
    assert "写正文" in result["data"]["content"]


def test_hub_read_unequipped_skill_id(monkeypatch):
    """hub 模式：skill_id 未装备 → 返回错误。"""
    import agent_tools.skills as mod_skills
    monkeypatch.setattr(mod_skills, "_load_skills_source", lambda: "hub")

    from agent_tools.registry import invoke
    # analyst 只有空 equipment
    ctx = _hub_ctx(agent_role="analyst")
    result = invoke("skills.read", {"scope": "analyst", "skill_id": "uuid-111"}, ctx)
    assert result["ok"] is False
    assert "not equipped" in result.get("error", "") or "not equipped" in str(result)


def test_hub_read_unequipped_name(monkeypatch):
    """hub 模式：name 未装备 → 返回错误。"""
    import agent_tools.skills as mod_skills
    monkeypatch.setattr(mod_skills, "_load_skills_source", lambda: "hub")

    from agent_tools.registry import invoke
    ctx = _hub_ctx(agent_role="analyst")
    result = invoke("skills.read", {"scope": "analyst", "name": "测试技能导入"}, ctx)
    assert result["ok"] is False
    assert "not equipped" in result.get("error", "") or "not equipped" in str(result)


def test_hub_read_scope_mismatch(monkeypatch):
    """hub 模式：scope ≠ agent_role 仍然拒绝。"""
    import agent_tools.skills as mod_skills
    monkeypatch.setattr(mod_skills, "_load_skills_source", lambda: "hub")

    from agent_tools.registry import invoke
    ctx = _hub_ctx(agent_role="content")
    result = invoke("skills.read", {"scope": "analyst", "skill_id": "uuid-111"}, ctx)
    assert result["ok"] is False
    assert "scope mismatch" in result.get("error", "")


def test_hub_read_budget_respected(monkeypatch):
    """hub 模式：预算熔断仍然生效。"""
    import agent_tools.skills as mod_skills
    monkeypatch.setattr(mod_skills, "_load_skills_source", lambda: "hub")
    # 设置 budget=2
    monkeypatch.setattr(mod_skills, "_load_budget", lambda: 2)
    mod_skills._BUDGET_COUNTERS.clear()

    from agent_tools.registry import invoke
    ctx = _hub_ctx(agent_role="content", task_id="hub-budget")

    r1 = invoke("skills.read", {"scope": "content", "skill_id": "uuid-111"}, ctx)
    assert r1["ok"] is True

    r2 = invoke("skills.read", {"scope": "content", "skill_id": "uuid-222"}, ctx)
    assert r2["ok"] is True

    r3 = invoke("skills.read", {"scope": "content", "skill_id": "uuid-111"}, ctx)
    assert r3["ok"] is False
    assert "SKILL_BUDGET_EXHAUSTED" in r3.get("error", "")


def test_hub_read_missing_args(monkeypatch):
    """hub 模式：缺 skill_id 和 name → 错误。"""
    import agent_tools.skills as mod_skills
    monkeypatch.setattr(mod_skills, "_load_skills_source", lambda: "hub")

    from agent_tools.registry import invoke
    ctx = _hub_ctx(agent_role="content")
    result = invoke("skills.read", {"scope": "content"}, ctx)
    assert result["ok"] is False
    assert "required" in result.get("error", "")


def test_file_mode_regression_no_skill_id(tmp_path):
    """file 模式拒绝 skill_id（仅支持 name）。"""
    from agent_tools.registry import invoke, ToolContext
    from agents.memory import MemoryLayer

    class _FakeStorage:
        def __init__(self, p: str):
            self.memory_dir = Path(p)
    mem = MemoryLayer(storage=_FakeStorage(str(tmp_path)))
    ctx = ToolContext(
        tenant_id="default",
        task_id="",
        extra={"memory": mem, "agent_role": "intel"},
    )
    # file 模式默认 skills_source=files，缺 name 应报错
    result = invoke("skills.read", {"scope": "intel", "skill_id": "uuid-xxx"}, ctx)
    assert result["ok"] is False
    assert "name is required in file mode" in result.get("error", "")
