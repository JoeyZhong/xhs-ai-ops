"""
审计日志（JSONL 追加 + SHA256 去重 + 跨进程安全追加）。

设计要点：
- JSONL 格式（一行一条 JSON），便于流式分析
- 写入失败不阻断业务（包到 try/except 里）
- 内置 SHA256 去重缓存（同一进程内连续写相同事件只写一次）
- 通过 StorageBackend 持久化（本地 backend → 文件，未来 Supabase → 表）
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime
from typing import Any, Optional


class AuditLogger:
    """每个任务一个 logger 实例，task_id 自动注入每条日志。"""

    def __init__(self, storage, tenant_id: str, task_id: str = "",
                 dedup_window: int = 64):
        self.storage = storage
        self.tenant_id = tenant_id
        self.task_id = task_id
        self._recent_hashes: list[str] = []
        self._max_dedup = dedup_window
        self._lock = threading.Lock()

    def write(self, entry: dict) -> bool:
        """写入一条审计日志。返回 True 表示真的写了，False 表示去重跳过。"""
        # 标准化字段
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "tenant_id": self.tenant_id,
            "task_id": self.task_id,
            "pid": os.getpid(),
            **entry,
        }
        # 去重 key（基于 entry 内容 + tenant + task）
        digest = self._digest(record)
        with self._lock:
            if digest in self._recent_hashes:
                return False
            self._recent_hashes.append(digest)
            if len(self._recent_hashes) > self._max_dedup:
                self._recent_hashes.pop(0)
        record["_digest"] = digest

        try:
            if self.storage:
                self.storage.save_audit_log(self.tenant_id, record)
        except Exception:
            # 审计失败不向上抛，避免污染业务路径
            return False
        return True

    @staticmethod
    def _digest(record: dict) -> str:
        # 不让 ts/pid 影响去重（去重看的是事件内容）
        body = {k: v for k, v in record.items() if k not in ("ts", "pid", "_digest")}
        s = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def make_logger(storage, tenant_id: str = "default", task_id: str = "") -> AuditLogger:
    """简洁工厂。"""
    return AuditLogger(storage=storage, tenant_id=tenant_id, task_id=task_id)
