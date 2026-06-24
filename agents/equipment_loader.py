"""
Agent 启动时拉本租户该 role 装备的 skill summary 列表。
被 feature flag skills_source 控制：
  - "files"（默认/旧）：返回 None，调用方走旧 _derived__skills_block.md 路径
  - "hub"：返回 list[dict]，调用方拼新 system prompt block
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from storage.base import StorageBackend

_SETTINGS_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.json"


def _load_skills_source() -> str:
    """从 settings.json 读取 skills_source，缺省返回 "files"。"""
    try:
        if _SETTINGS_PATH.exists():
            return json.loads(_SETTINGS_PATH.read_text(encoding="utf-8")).get("skills_source", "files")
    except Exception:
        pass
    return "files"


def load(
    tenant_id: str,
    agent_role: str,
    backend: StorageBackend,
) -> Optional[list[dict]]:
    """返回 None 表示 feature flag 关闭；空 list 表示装备为空。"""
    if _load_skills_source() != "hub":
        return None
    return backend.list_equipment(tenant_id, agent_role)


def render_prompt_block(equipped: list[dict]) -> str:
    """注入 system prompt 的 'Available Skills' 段。"""
    if not equipped:
        return "## Available Skills\n（暂无装备的 skill）\n"
    lines = [
        "## Available Skills",
        "",
        "When the user explicitly mentions one of these skill names, "
        "first call `skills.read` with its `skill_id`, "
        "then follow the returned SKILL.md body.",
        "",
    ]
    for s in equipped:
        desc = s.get("description", "")
        lines.append(f"- `skill_id={s['id']}` **{s['name']}**: {desc}")
    lines.append("")
    return "\n".join(lines)
