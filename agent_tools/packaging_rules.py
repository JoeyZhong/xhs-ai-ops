"""包装公式 + CES 钩子的 loader。

source of truth: memory/_universal/packaging_rules.md
被 server/routers/content.py 的 prompt builder 注入到 Kimi 上下文里。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_RULES_PATH = Path(__file__).parent.parent / "memory" / "_universal" / "packaging_rules.md"


@lru_cache(maxsize=8)
def _read(path: str, mtime_ns: int) -> str:
    # mtime_ns 作为 cache key 的一部分，文件改动就自动失效
    return Path(path).read_text(encoding="utf-8")


def load_packaging_rules() -> str:
    """读 packaging_rules.md。基于 mtime 自动失效缓存。"""
    if not _RULES_PATH.exists():
        return ""
    return _read(str(_RULES_PATH), _RULES_PATH.stat().st_mtime_ns)


load_packaging_rules.cache_clear = _read.cache_clear  # type: ignore[attr-defined]
