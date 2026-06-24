"""
Phase 4a · 主密钥读取。

唯一对外函数:get_master_key() 返回 MASTER_ENCRYPTION_KEY 的字符串值。
缺失 raise EnvironmentError fail-fast,防止隐式跑出无加密路径。
该值用作 pgcrypto pgp_sym_encrypt/decrypt 的对称口令。
"""
from __future__ import annotations

import os


def get_master_key() -> str:
    """返回 MASTER_ENCRYPTION_KEY env var(hex 字符串)。

    缺失立即 raise,不返回空串或 None,防止下游静默退化为无加密。
    """
    key = os.environ.get("MASTER_ENCRYPTION_KEY")
    if not key:
        raise EnvironmentError(
            "MASTER_ENCRYPTION_KEY not set; check ~/.spider_xhs/.env"
        )
    return key
