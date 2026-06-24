"""
Memory 层（包装 Storage backend，加权限、entry 模式、OCC 版本戳）。

Phase 2 提供：
- snapshot(scope) — 读取 scope 下所有文件，返回 dict[filename] -> content
- read / write 单文件
- 写入权限按 (scope -> 允许写入的 agent role) 矩阵控制
- on_write hook

Phase 3 增加：
- entry-based add_entry / replace_entry / remove_entry（§id: 分隔）
- 注入检测在写入时强制执行

P1 (AOECA-Lite) 增加：
- OCC 乐观并发控制：每条 entry 带 meta.rev 版本戳
- replace/remove 用 CAS（compare-and-swap），冲突抛 WriteConflictError
- read_entry 返回 (body, rev)
- 旧 entry（无 §rev）向后兼容：缺省 rev=0，第一次写入升到 rev=1
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Tuple

from agents.skills import ParsedSkill, list_skills as _list_skills, read_skill_content as _read_skill


# ── Entry 数据类（P1 OCC）──────────────────────────────────────────────────

@dataclass
class Entry:
    """Memory 中的单个 entry，含 OCC 版本戳 + 审阅元字段。"""
    id: str
    body: str
    rev: int = 0
    status: str = "active"       # "active" | "draft" | "rejected"
    source: str = "manual"       # "manual" | "scheduler"
    confidence: str = "high"     # "high" | "low"


# ── 异常 ───────────────────────────────────────────────────────────────────

class WritePermissionDenied(Exception):
    """跨 scope 写入"""


class MemoryInjectionDetected(Exception):
    """写入内容含可疑指令模式（Phase 3 启用）"""


class WriteConflictError(Exception):
    """OCC 版本冲突：读到的 rev 与写入时的 expected_rev 不符。"""

    def __init__(self, entry_id: str, expected: int, actual: int):
        self.entry_id = entry_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"WriteConflict on entry '{entry_id}': "
            f"expected rev={expected}, actual rev={actual}"
        )


# ── Entry 解析/序列化（§id: + §rev: 分隔模式）──────────────────────────────

# 行头格式：§id: <id> §rev: <int> [§status: <st>] [§source: <src>] [§confidence: <val]
ENTRY_HEADER_RE = re.compile(
    r"^§id:\s*(\S+)"
    r"(?:\s+§rev:\s*(\d+))?"
    r"(?:\s+§status:\s*(\S+))?"
    r"(?:\s+§source:\s*(\S+))?"
    r"(?:\s+§confidence:\s*(\S+))?"
    r"\s*$"
)


def parse_entries(content: str) -> tuple[str, dict[str, Entry]]:
    """
    解析 §id: 分隔的 entry 文件。
    返回 (header_text, {entry_id: Entry(id, body, rev)})。
    第一个 §id: 之前的内容算作 header（如初始基线说明）。
    旧 entry 无 §rev: 时 rev 默认为 0。
    """
    if not content:
        return "", {}
    header_lines: list[str] = []
    entries: dict[str, Entry] = {}
    current_id: Optional[str] = None
    current_rev: int = 0
    current_status: str = "active"
    current_source: str = "manual"
    current_confidence: str = "high"
    current_lines: list[str] = []

    for line in content.splitlines():
        m = ENTRY_HEADER_RE.match(line)
        if m:
            if current_id is not None:
                entries[current_id] = Entry(
                    id=current_id,
                    body="\n".join(current_lines).strip(),
                    rev=current_rev,
                    status=current_status,
                    source=current_source,
                    confidence=current_confidence,
                )
            current_id = m.group(1)
            current_rev = int(m.group(2)) if m.group(2) else 0
            current_status = m.group(3) if m.group(3) else "active"
            current_source = m.group(4) if m.group(4) else "manual"
            current_confidence = m.group(5) if m.group(5) else "high"
            current_lines = []
        else:
            if current_id is None:
                header_lines.append(line)
            else:
                current_lines.append(line)

    if current_id is not None:
        entries[current_id] = Entry(
            id=current_id,
            body="\n".join(current_lines).strip(),
            rev=current_rev,
            status=current_status,
            source=current_source,
            confidence=current_confidence,
        )

    return "\n".join(header_lines).rstrip(), entries


def serialize_entries(header: str, entries: dict[str, Entry]) -> str:
    """序列化 entry 为文件内容，含 §rev/§status/§source/§confidence 行头。"""
    parts = []
    if header.strip():
        parts.append(header.rstrip())
    for eid, entry in entries.items():
        meta = (
            f"§id: {eid} §rev: {entry.rev}"
            f" §status: {entry.status} §source: {entry.source}"
            f" §confidence: {entry.confidence}"
        )
        parts.append(f"{meta}\n{entry.body.strip()}")
    return "\n\n".join(parts) + ("\n" if parts else "")


# ── 权限矩阵 ───────────────────────────────────────────────────────────────

# scope -> 允许写入的 agent role（"*" 表示任何 agent）
_WRITE_PERMISSIONS = {
    "shared":  ["master"],          # 由 Master 维护人设、benchmarks
    "intel":   ["intel"],
    "content": ["analyst"],         # ★ 反馈闭环关键：Analyst 写、Content 只读
    "analyst": ["analyst"],
}


# ── 主体 ───────────────────────────────────────────────────────────────────

@dataclass
class MemoryLayer:
    storage: object                                 # StorageBackend
    on_write_hooks: list[Callable] = field(default_factory=list)

    # ── 读取 ──────────────────────────────────────────────────────────

    def read(self, tenant_id: str, scope: str, file: str) -> Optional[str]:
        return self.storage.load_memory(tenant_id, scope, file)

    def snapshot(self, tenant_id: str, scope: str,
                  files: Optional[list[str]] = None) -> dict[str, str]:
        """
        读取 scope 下指定文件（或常用文件）的当前内容快照。
        返回 {filename: content}，文件不存在时不在返回值中。
        """
        if files is None:
            files = {
                "shared":  ["persona.md", "benchmarks.md",
                             "title_formulas.md", "content_dimensions.md"],
                # 注：orchestration.md（Planner 编排图）有意不在 shared 默认列表，
                # 由 Planner 路径显式 read，避免 ContentAgent/IntelAgent 主循环吃 token。
                "intel":   ["findings.md"],
                "content": ["playbook.md"],
                "analyst": ["methodology.md"],
            }.get(scope, [])

        result: dict[str, str] = {}
        for f in files:
            content = self.read(tenant_id, scope, f)
            if content:
                result[f] = content
        return result

    # ── Skills ─────────────────────────────────────────────────────────

    def _scope_path(self, tenant_id: str, scope: str) -> Path:
        """派生 scope 磁盘路径用于 skills 目录枚举。"""
        mem_dir = getattr(self.storage, "memory_dir", Path.cwd() / "memory")
        return mem_dir / tenant_id / scope

    def list_skills(self, tenant_id: str, scope: str) -> list[ParsedSkill]:
        """枚举 scope 下 skills/*.md，返回解析后的 skill 列表。"""
        scope_dir = self._scope_path(tenant_id, scope)
        return _list_skills(scope_dir)

    def read_skill(self, tenant_id: str, scope: str, name: str) -> Optional[str]:
        """按 name 读取 skill 文件全文。未找到返回 None。"""
        scope_dir = self._scope_path(tenant_id, scope)
        return _read_skill(scope_dir, name)

    # ── 写入（带权限）────────────────────────────────────────────────

    def write(self, tenant_id: str, scope: str, file: str,
              content: str, agent_role: str) -> None:
        """
        写入 memory。检查：
        1. 该 agent 是否有权写入此 scope
        2. (Phase 3) 内容是否含注入模式
        3. 触发 on_write hook
        """
        allowed = _WRITE_PERMISSIONS.get(scope, [])
        if agent_role not in allowed and "*" not in allowed:
            raise WritePermissionDenied(
                f"agent role '{agent_role}' cannot write to scope '{scope}'"
            )

        self._check_injection(content)
        self.storage.save_memory(tenant_id, scope, file, content)

        for hook in self.on_write_hooks:
            try:
                hook(tenant_id=tenant_id, scope=scope, file=file,
                     content=content, agent_role=agent_role)
            except Exception:
                pass

    # ── 注入检测（Phase 3 完整实现）───────────────────────────────────

    _INJECTION_PATTERNS = [
        re.compile(r"ignore\s+previous\s+instructions", re.I),
        re.compile(r"忽略.{0,5}(之前|前面|上面).{0,3}(指令|指示)"),
        re.compile(r"<\|im_start\|>"),
        re.compile(r"<system>", re.I),
        re.compile(r"^\s*\[SYSTEM\]\s*:", re.M),
        re.compile(r"###\s*system\s*:", re.I),
        re.compile(r"`{5,}"),
    ]

    def _check_injection(self, content: str) -> None:
        if not content:
            return
        for pat in self._INJECTION_PATTERNS:
            if pat.search(content):
                raise MemoryInjectionDetected(
                    f"suspected injection pattern: {pat.pattern}"
                )
        if re.search(r"(.)\1{50,}", content):
            raise MemoryInjectionDetected("excessive repeated characters")

    # ── Entry-based 操作（OCC 版本，P1）──────────────────────────────

    def list_entries(self, tenant_id: str, scope: str, file: str) -> dict[str, Entry]:
        """返回 {entry_id: Entry}。文件不存在时返回空 dict。"""
        content = self.read(tenant_id, scope, file) or ""
        _, entries = parse_entries(content)
        return entries

    def get_entry(self, tenant_id: str, scope: str, file: str,
                   entry_id: str) -> Optional[str]:
        """仅返回 body（向后兼容）。"""
        entry = self.list_entries(tenant_id, scope, file).get(entry_id)
        return entry.body if entry else None

    def read_entry(self, tenant_id: str, scope: str, file: str,
                    entry_id: str) -> Optional[Tuple[str, int]]:
        """
        P1 新接口：读取单条 entry，返回 (body, rev)。
        entry 不存在时返回 None。
        """
        entry = self.list_entries(tenant_id, scope, file).get(entry_id)
        if entry is None:
            return None
        return entry.body, entry.rev

    def add_entry(self, tenant_id: str, scope: str, file: str,
                   entry_id: str, content: str, agent_role: str,
                   entry_meta: Optional[dict] = None) -> str:
        """
        新增 entry。自动设 rev=1。
        如果 entry_id 已存在，抛 ValueError（用 replace_entry 显式覆盖）。
        返回 op 摘要：'added' / 'no-op'。
        entry_meta: optional dict with keys 'status', 'source', 'confidence'.
        """
        if not entry_id or not entry_id.strip():
            raise ValueError("entry_id cannot be empty")
        existing = self.read(tenant_id, scope, file) or ""
        header, entries = parse_entries(existing)
        if entry_id in entries:
            raise ValueError(f"entry_id '{entry_id}' already exists; use replace_entry")
        meta = entry_meta or {}
        entries[entry_id] = Entry(
            id=entry_id, body=content.strip(), rev=1,
            status=meta.get("status", "active"),
            source=meta.get("source", "manual"),
            confidence=meta.get("confidence", "high"),
        )
        self.write(tenant_id, scope, file,
                   serialize_entries(header, entries),
                   agent_role)
        return "added"

    def replace_entry(self, tenant_id: str, scope: str, file: str,
                       entry_id: str, content: str, agent_role: str,
                       *, expected_rev: Optional[int] = None,
                       entry_meta: Optional[dict] = None) -> int:
        """
        覆盖现有 entry（OCC CAS 语义）。

        - entry_id 不存在时等价于 add（rev=1）
        - expected_rev 不为 None 时，检查磁盘当前 rev 是否匹配；
          不匹配抛 WriteConflictError
        - 返回新 rev
        - entry_meta: optional dict with keys 'status', 'source', 'confidence'.
        """
        existing = self.read(tenant_id, scope, file) or ""
        header, entries = parse_entries(existing)

        old_entry = entries.get(entry_id)
        if old_entry is not None and expected_rev is not None:
            if old_entry.rev != expected_rev:
                raise WriteConflictError(
                    entry_id, expected_rev, old_entry.rev
                )

        new_rev = 1 if old_entry is None else old_entry.rev + 1
        meta = entry_meta or {}
        entries[entry_id] = Entry(
            id=entry_id, body=content.strip(), rev=new_rev,
            status=meta.get("status", old_entry.status if old_entry else "active"),
            source=meta.get("source", old_entry.source if old_entry else "manual"),
            confidence=meta.get("confidence", old_entry.confidence if old_entry else "high"),
        )
        self.write(tenant_id, scope, file,
                   serialize_entries(header, entries),
                   agent_role)
        return new_rev

    def remove_entry(self, tenant_id: str, scope: str, file: str,
                      entry_id: str, agent_role: str,
                      *, expected_rev: Optional[int] = None) -> str:
        """
        删除 entry（OCC CAS 语义）。

        - expected_rev 不为 None 时检查版本；不匹配抛 WriteConflictError
        - entry 不存在时返回 'no-op'
        - 返回 'removed' / 'no-op'
        """
        existing = self.read(tenant_id, scope, file) or ""
        header, entries = parse_entries(existing)

        old_entry = entries.get(entry_id)
        if old_entry is None:
            return "no-op"

        if expected_rev is not None and old_entry.rev != expected_rev:
            raise WriteConflictError(
                entry_id, expected_rev, old_entry.rev
            )

        del entries[entry_id]
        self.write(tenant_id, scope, file,
                   serialize_entries(header, entries),
                   agent_role)
        return "removed"

    # ── 旧 entry 兼容性迁移 helper（P1.1.7）──────────────────────────

    def _bump_old_entries(self, tenant_id: str, scope: str, file: str,
                          agent_role: str) -> int:
        """
        将文件中所有 rev=0 的旧 entry 升到 rev=1。
        返回升级的 entry 数量。
        """
        existing = self.read(tenant_id, scope, file) or ""
        header, entries = parse_entries(existing)
        bumped = 0
        for eid, entry in entries.items():
            if entry.rev == 0:
                entry.rev = 1
                bumped += 1
        if bumped:
            self.write(tenant_id, scope, file,
                       serialize_entries(header, entries),
                       agent_role)
        return bumped

    # ── Hook 注册 ─────────────────────────────────────────────────────

    def register_on_write(self, hook: Callable) -> None:
        self.on_write_hooks.append(hook)
