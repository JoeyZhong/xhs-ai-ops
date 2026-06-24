"""
Skills 系统：为每个 Sub Agent 引入可复用的工作流知识。

物理格式（单文件 YAML frontmatter）：
  memory/{tenant}/{role}/skills/<skill-id>/
    ├── SKILL.md           ← YAML frontmatter + markdown body
    └── references/        ← 可选，纯文本补充材料

Frontmatter 字段：
  - name:          str         (必填)
  - description:   str         (必填)
  - version:       str         (可选，默认 "1.0.0")
  - suggested_for: list[str]   (可选，默认 [])
  - allowed_tools: list[str]   (可选，默认 []；Spider_XHS 不消费，forward-compat)
  - license:       str         (可选，默认 ""；forward-compat)
  未识别字段 → 静默忽略（forward-compat）。

运行时元数据（id, rev, status, tenant_id, source_skill_id, created_at, updated_at）
由 storage 层通过可选的 skill.json sidecar 管理，parse_skill_dir 不处理。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


# ── 正则 ──────────────────────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(
    r"^---\s*\n"      # 开头的 ---
    r"(.*?)"           # frontmatter 内容（non-greedy）
    r"\n---\s*\n"      # 结尾的 ---
    r"(.*)$",          # body
    re.DOTALL,
)


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """返回 (frontmatter_dict, body_str)；缺失或解析失败时返回 ({}, text.strip())。"""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text.strip()
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        fm = None
    if not isinstance(fm, dict):
        fm = {}
    body = m.group(2).strip()
    return fm, body


# ── 数据结构 ───────────────────────────────────────────────────────────────

@dataclass
class ParsedSkill:
    name: str = ""
    description: str = ""
    suggested_for: list[str] = field(default_factory=list)
    version: str = "1.0.0"
    body: str = ""
    # ── 前向兼容字段 ──────────────────────────────────────────────────
    allowed_tools: list[str] = field(default_factory=list)
    license: str = ""
    # ── 运行时元数据（由 storage 层通过 skill.json sidecar 填充） ─────
    id: str = ""
    tenant_id: Optional[str] = None
    source_skill_id: Optional[str] = None
    status: str = "active"
    rev: int = 1


# ── 异常 ───────────────────────────────────────────────────────────────────

class SkillParseError(Exception):
    """Skill bundle 解析失败"""


# ── 解析 ───────────────────────────────────────────────────────────────────

def parse_skill_dir(dir_path: Path) -> ParsedSkill:
    """
    解析 skill 目录（SKILL.md 单文件 YAML frontmatter）。

    流程：
    1. 校验 dir/SKILL.md 存在
    2. 用 _split_frontmatter 解析 YAML frontmatter + body
    3. name / description 从 frontmatter 读取（必填）
    4. 未识别 frontmatter 字段静默忽略

    运行时元数据（id/rev/status 等）不在此处处理——由 storage 层
    通过可选的 skill.json sidecar 管理。
    """
    skill_md = dir_path / "SKILL.md"
    if not skill_md.is_file():
        raise SkillParseError(f"missing SKILL.md in {dir_path}")

    text = skill_md.read_text(encoding="utf-8")
    fm, body = _split_frontmatter(text)

    if not isinstance(fm, dict) or not fm:
        raise SkillParseError(f"missing or invalid YAML frontmatter in {dir_path}")

    name = str(fm.get("name") or "").strip()
    description = str(fm.get("description") or "").strip()

    if not name:
        raise SkillParseError(f"frontmatter missing required field: 'name' in {dir_path}")
    if not description:
        raise SkillParseError(f"frontmatter missing required field: 'description' in {dir_path}")

    version = str(fm.get("version", "1.0.0") or "1.0.0")
    suggested_for = fm.get("suggested_for", [])
    if not isinstance(suggested_for, list):
        suggested_for = []
    allowed_tools = fm.get("allowed_tools", [])
    if not isinstance(allowed_tools, list):
        allowed_tools = []
    license_ = str(fm.get("license", "") or "")

    return ParsedSkill(
        name=name,
        description=description,
        suggested_for=suggested_for,
        version=version,
        body=body,
        allowed_tools=allowed_tools,
        license=license_,
    )


# ── 目录操作 ───────────────────────────────────────────────────────────────

def list_skills(scope_dir: Path) -> list[ParsedSkill]:
    """
    枚举 scope_dir/skills/ 下的子目录，每个含 SKILL.md 的子目录视为一个 skill。
    解析失败的 skill 静默跳过，不会阻断其余 skill。
    按子目录名字母序返回。
    """
    skills_dir = scope_dir / "skills"
    if not skills_dir.is_dir():
        return []

    result: list[ParsedSkill] = []
    for entry in sorted(skills_dir.iterdir()):
        if not entry.is_dir():
            continue
        try:
            ps = parse_skill_dir(entry)
            result.append(ps)
        except (SkillParseError, OSError):
            continue
    return result


def read_skill_content(scope_dir: Path, name: str) -> Optional[str]:
    """
    按 name 查找 scope_dir/skills/ 下的 skill，返回 SKILL.md 纯 body 文本（无 frontmatter）。
    name 匹配 frontmatter 中的 name 字段（不区分目录名）。
    未找到返回 None。
    """
    skills_dir = scope_dir / "skills"
    if not skills_dir.is_dir():
        return None

    for entry in skills_dir.iterdir():
        if not entry.is_dir():
            continue
        try:
            ps = parse_skill_dir(entry)
            if ps.name == name:
                return ps.body
        except (SkillParseError, OSError):
            continue
    return None


# ── Frontmatter 序列化 ─────────────────────────────────────────────────────

def _build_frontmatter(skill: ParsedSkill) -> str:
    """从 ParsedSkill 构建 YAML frontmatter 字符串（不含 --- 包裹符）。

    name/description/version/suggested_for 总是写入 frontmatter（即使等于默认值），
    保证 round-trip 稳定。allowed_tools/license 仅当非默认值才写入，
    避免社区共享 skill 时充斥空列表噪音。
    """
    data: dict = {
        "name": skill.name,
        "description": skill.description,
        "version": skill.version,
    }
    if skill.suggested_for:
        data["suggested_for"] = skill.suggested_for
    else:
        data["suggested_for"] = []
    if skill.allowed_tools:
        data["allowed_tools"] = skill.allowed_tools
    if skill.license:
        data["license"] = skill.license
    return yaml.dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False).strip()


# ── Hub 辅助 ─────────────────────────────────────────────────────────────

def write_skill_to_dir(dir_path: Path, skill: ParsedSkill) -> None:
    """
    将 ParsedSkill 写回 SKILL.md（YAML frontmatter + body）。
    用于 REST API 的 create/update 操作。
    运行时元数据（id/rev/status 等）不由本函数管理。
    """
    dir_path.mkdir(parents=True, exist_ok=True)
    frontmatter = _build_frontmatter(skill)
    content = f"---\n{frontmatter}\n---\n\n{skill.body}"
    (dir_path / "SKILL.md").write_text(content, encoding="utf-8")
