"""Storage backend 工厂。

按 STORAGE_BACKEND env var 路由:
- "local"    → LocalJsonBackend()(默认,§A6 cutover 前)
- "postgres" → PgBackend()(§A6 cutover 后)

懒缓存:同一进程内复用同一 backend 实例(避免重复 init_pool)。
"""
from __future__ import annotations

import os
from typing import Optional

from storage.base import StorageBackend
from storage.local_json import LocalJsonBackend


_BACKEND: Optional[StorageBackend] = None
_LAST_TYPE: str = ""


def get_backend() -> StorageBackend:
    """根据 STORAGE_BACKEND env 返回 backend。同一类型实例复用。"""
    global _BACKEND, _LAST_TYPE
    btype = os.environ.get("STORAGE_BACKEND", "local").lower()
    if _BACKEND is not None and _LAST_TYPE == btype:
        return _BACKEND

    if btype == "local":
        _BACKEND = LocalJsonBackend()
    elif btype == "postgres":
        from storage.pg_backend import PgBackend
        _BACKEND = PgBackend()
    else:
        raise ValueError(f"unknown STORAGE_BACKEND: {btype!r}")

    _LAST_TYPE = btype
    return _BACKEND


def reset_backend() -> None:
    """主要给测试用:强制下次 get_backend 重建。"""
    global _BACKEND, _LAST_TYPE
    _BACKEND = None
    _LAST_TYPE = ""
