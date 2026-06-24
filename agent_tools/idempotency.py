"""
Idempotency 中间件（P1.2）。

解决 Streamlit server-side rerun 导致的重复 tool 调用问题：
- 用户点按钮 → Kimi 还在跑 → 用户切侧边栏 → Streamlit rerun → button 状态重置
- 副作用工具（如 generate_batch、write_playbook_entry）可能被重复执行

策略：
- 用 SHA256 计算 idempotency_key(tool_name, args, agent_role, task_id)
- 内存 dict + 持久化双层缓存（xhs_data/idempot/<tenant>.jsonl）
- 24h TTL，只缓存成功结果
- 副作用工具白名单控制哪些 tool 走幂等检查
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Optional


# ── 配置 ───────────────────────────────────────────────────────────────────

_IDEMPOT_DIR = Path(__file__).parent.parent / "xhs_data" / "idempot"
_TTL_SECONDS = 24 * 3600  # 24h

# 副作用工具白名单：只有这些 tool 走幂等检查
# （读操作不需要；写操作需要）
_IDEMPOT_TOOLS = frozenset({
    "content_gen.generate_batch",
    "memory.write_playbook_entry",
    "kimi.complete",
})


def is_idempotency_applicable(tool_name: str) -> bool:
    """判断该 tool 是否需要走幂等检查。"""
    return tool_name in _IDEMPOT_TOOLS


# ── compute_key ────────────────────────────────────────────────────────────

def compute_key(tool_name: str, args: dict,
                agent_role: str = "", task_id: str = "") -> str:
    """
    计算 idempotency key。
    输入：tool_name + 排序后的 args JSON + agent_role
    输出：SHA256 前 32 字符 hex（足够唯一且紧凑）

    设计说明：
    - **task_id 不入 hash**——保留形参仅为向后兼容旧调用点签名。
    - 早期设计把 task_id 入 key 是想"同 task 内防重"，但这让缓存退化成
      "只解决 Streamlit rerun"，**跨 task 相同 args 永远不命中**——
      丧失 idempotency 节约 API 成本的核心价值。改为按 (tool, args, role)
      计算，跨 task 同输入命中复用。
    - 副作用控制由白名单（_IDEMPOT_TOOLS）+ "失败结果不入缓存" 保证。
    """
    del task_id  # 显式忽略：保留参数仅为兼容签名
    payload = json.dumps({
        "tool": tool_name,
        "args": args,
        "role": agent_role,
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


# ── Cache Entry ────────────────────────────────────────────────────────────

class _CacheEntry:
    __slots__ = ("result", "ts", "tool")

    def __init__(self, result: dict, ts: float, tool: str):
        self.result = result
        self.ts = ts
        self.tool = tool


# ── IdempotencyCache ───────────────────────────────────────────────────────

class IdempotencyCache:
    """
    双层缓存：内存 dict（热）+ 本地 JSONL（持久化）。
    启动时从 JSONL 加载，写入时追加 JSONL。
    """

    def __init__(self, tenant_id: str = "default"):
        self._tenant_id = tenant_id
        self._mem: dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()
        self._file = _IDEMPOT_DIR / f"{tenant_id}.jsonl"
        self._load()

    # ── 内部 ──────────────────────────────────────────────────────

    def _load(self) -> None:
        """启动时从 JSONL 加载未过期的 entry。"""
        if not self._file.exists():
            return
        now = time.time()
        cutoff = now - _TTL_SECONDS
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        ts = record.get("ts", 0)
                        if ts >= cutoff:
                            key = record["key"]
                            self._mem[key] = _CacheEntry(
                                result=record["result"],
                                ts=ts,
                                tool=record.get("tool", ""),
                            )
                    except Exception:
                        continue
        except Exception:
            pass

    def _append_persistent(self, key: str, result: dict, ts: float,
                            tool: str) -> None:
        """追加一条记录到 JSONL。"""
        try:
            _IDEMPOT_DIR.mkdir(parents=True, exist_ok=True)
            record = {
                "key": key,
                "ts": ts,
                "tool": tool,
                "result": result,
            }
            with open(self._file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass  # 持久化失败不影响内存缓存继续工作

    def _cleanup_expired(self) -> None:
        """清理内存中过期的 entry（惰性清理，不主动跑）。"""
        cutoff = time.time() - _TTL_SECONDS
        expired = [k for k, v in self._mem.items() if v.ts < cutoff]
        for k in expired:
            self._mem.pop(k, None)

    # ── 公开接口 ──────────────────────────────────────────────────

    def get(self, key: str) -> Optional[dict]:
        """查询缓存。命中返回上次结果；未命中或过期返回 None。"""
        with self._lock:
            entry = self._mem.get(key)
            if entry is None:
                return None
            if time.time() - entry.ts > _TTL_SECONDS:
                self._mem.pop(key, None)
                return None
            # 深拷贝返回，防止调用方修改缓存内对象
            return json.loads(json.dumps(entry.result))

    def set(self, key: str, result: dict, tool: str) -> None:
        """写入缓存（仅成功结果）。"""
        if not result.get("ok"):
            return  # 失败结果不入 cache

        now = time.time()
        with self._lock:
            self._mem[key] = _CacheEntry(
                result=result,
                ts=now,
                tool=tool,
            )
        self._append_persistent(key, result, now, tool)

    def clear(self) -> None:
        """清空内存和持久化缓存。"""
        with self._lock:
            self._mem.clear()
        try:
            if self._file.exists():
                self._file.unlink()
        except Exception:
            pass
