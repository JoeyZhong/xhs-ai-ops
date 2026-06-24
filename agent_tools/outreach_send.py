"""
小红书半自动一键发送 Tool（lead-intent-radar V2 · 写端）。

契约：outreach.send(lead_id, account_id?) -> {status, sent, engine, ...}

红线（与 proposal §3 一致）：
  · 逐条人工确认才发一条 —— 本工具由 /admin/leads 的「一键发送」按钮逐条触发，
    非批量、非定时、非无人值守。
  · 默认 dryrun —— XHS_WRITE_ENGINE 缺省 = dryrun（只校验+预览，绝不真实发出）。
  · 仅小红书 —— 知乎/猪八戒只读，本工具对其拒发。
  · 商用引擎闸门 —— 真发只走 ReaJason/xhs(MIT)；免费版 MediaCrawler 无写能力、不在此列。
    切真发需显式配置 XHS_WRITE_ENGINE=reajason + 有效凭证（= 上云前置门，proposal §8-A）。

发送前置校验（任一不过则拒发）：
  · source == xhs
  · sendable = check_lure_pass and check_dup_pass（复用 V1 校验结果）
  · 速率限制：单账号 ≤ N 条/天（默认 5）、两次真发间隔 ≥ 随机 5–15min 抖动

返回 status ∈ {sent, dryrun, blocked_checks, engine_not_ready, rate_limited, source_unsupported}。
"""

from __future__ import annotations

import os
import json
import random
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

from agent_tools import registry
from agent_tools.registry import ToolContext
from storage.base import RevMismatch


CONFIG_DIR = Path(__file__).parent.parent / "config"

# 速率参数（proposal §8-B：≤5/天、5–15min 随机抖动）
DAILY_LIMIT = int(os.environ.get("XHS_SEND_DAILY_LIMIT", "5"))
INTERVAL_MIN_MINUTES = float(os.environ.get("XHS_SEND_INTERVAL_MIN", "5"))
INTERVAL_MAX_MINUTES = float(os.environ.get("XHS_SEND_INTERVAL_MAX", "15"))

_QUOTA_LOCK = threading.RLock()


# ── 写引擎抽象 ──────────────────────────────────────────────────────────────

def _engine_name() -> str:
    return (os.environ.get("XHS_WRITE_ENGINE", "dryrun") or "dryrun").strip().lower()


def _reajason_send(lead: dict) -> tuple[Optional[str], Optional[str]]:
    """真发：经 ReaJason/xhs 的 comment_note 发出首触。

    返回 (platform_id, error)。引擎/凭证未就绪 → (None, "engine_not_ready")。
    ⚠️ 上线前置门：此路径需显式配置 + 有效凭证才会真正发出（proposal §8-A）。
    内部验证期若未配置，安全返回 engine_not_ready，绝不误发。
    """
    # 凭证就绪判定（cookie 走现有 cookie_manager / 或显式 env）。
    has_cred = bool(os.environ.get("XHS_WRITE_COOKIE"))
    if not has_cred:
        return None, "engine_not_ready"
    try:
        # ReaJason/xhs 适配位：真实接线在切引擎时补。未安装则降级未就绪，不误发。
        from xhs import XhsClient  # type: ignore  # noqa: F401
    except Exception:
        return None, "engine_not_ready"
    try:
        note_id = (lead.get("source_url") or "").rstrip("/").split("/")[-1]
        if not note_id:
            return None, "missing_note_id"
        # NOTE: 实际 comment_note 调用在引擎切换落地时接入；当前保持未就绪以防误发。
        return None, "engine_not_ready"
    except Exception as e:  # pragma: no cover - 真实网络路径
        return None, f"send_failed: {e}"


# ── 速率限制（quota sidecar，按 account/day 计数，带锁）──────────────────────

def _quota_path(tenant_id: str) -> Path:
    if tenant_id == "default":
        return CONFIG_DIR / "lifecycle_send_quota.json"
    sub = CONFIG_DIR / tenant_id
    sub.mkdir(parents=True, exist_ok=True)
    return sub / "lifecycle_send_quota.json"


def _load_quota(tenant_id: str) -> dict:
    path = _quota_path(tenant_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_quota(tenant_id: str, data: dict) -> None:
    path = _quota_path(tenant_id)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _account_record(data: dict, account_id: str) -> dict:
    rec = data.get(account_id) or {}
    # 跨天重置
    if rec.get("date") != _today():
        rec = {"date": _today(), "count": 0, "last_send_at": None, "next_allowed_at": None}
    return rec


def check_rate(tenant_id: str, account_id: str) -> dict:
    """只读判定当前账号是否可发。返回 {allowed, count, limit, reason, next_minutes}。"""
    with _QUOTA_LOCK:
        data = _load_quota(tenant_id)
        rec = _account_record(data, account_id)
    count = int(rec.get("count", 0) or 0)
    now = datetime.now(timezone.utc)

    if count >= DAILY_LIMIT:
        # 当日已达上限 → 次日 0 点可发
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        mins = max(1, int((tomorrow - now).total_seconds() // 60))
        return {"allowed": False, "count": count, "limit": DAILY_LIMIT,
                "reason": f"今日 {count}/{DAILY_LIMIT} 已达上限", "next_minutes": mins}

    nxt = rec.get("next_allowed_at")
    if nxt:
        try:
            nxt_dt = datetime.fromisoformat(nxt)
            if now < nxt_dt:
                mins = max(1, int((nxt_dt - now).total_seconds() // 60))
                return {"allowed": False, "count": count, "limit": DAILY_LIMIT,
                        "reason": f"距下次可发约 {mins} 分钟", "next_minutes": mins}
        except (ValueError, TypeError):
            pass

    return {"allowed": True, "count": count, "limit": DAILY_LIMIT,
            "reason": "", "next_minutes": None}


def _record_send(tenant_id: str, account_id: str) -> int:
    """记一次真发：count+1，设 last_send_at 与随机抖动的 next_allowed_at。返回新 count。"""
    with _QUOTA_LOCK:
        data = _load_quota(tenant_id)
        rec = _account_record(data, account_id)
        now = datetime.now(timezone.utc)
        jitter = random.uniform(INTERVAL_MIN_MINUTES, INTERVAL_MAX_MINUTES)
        rec["count"] = int(rec.get("count", 0) or 0) + 1
        rec["last_send_at"] = now.isoformat()
        rec["next_allowed_at"] = (now + timedelta(minutes=jitter)).isoformat()
        rec["date"] = _today()
        data[account_id] = rec
        _save_quota(tenant_id, data)
        return rec["count"]


# ── audit ───────────────────────────────────────────────────────────────────

def _audit(ctx: ToolContext, entry: dict) -> None:
    try:
        if getattr(ctx, "storage", None) and hasattr(ctx.storage, "save_audit_log"):
            ctx.storage.save_audit_log(ctx.tenant_id, {"event": "outreach.send", **entry})
    except Exception:
        pass


# ── Tool handler ────────────────────────────────────────────────────────────

def _send_handler(args: dict, ctx: ToolContext) -> dict:
    lead_id = (args.get("lead_id") or "").strip()
    if not lead_id:
        return {"ok": False, "error": "lead_id is required"}
    storage = getattr(ctx, "storage", None)
    if storage is None:
        return {"ok": False, "error": "storage unavailable"}

    lead = storage.get_lead(ctx.tenant_id, lead_id)
    if lead is None:
        return {"ok": False, "error": f"lead '{lead_id}' not found"}

    account_id = (args.get("account_id") or lead.get("persona_id") or "default").strip()
    engine = _engine_name()

    def _result(status: str, *, sent: bool = False, platform_id=None,
                reason: str = "", rate: Optional[dict] = None,
                lead_out: Optional[dict] = None) -> dict:
        rate = rate or check_rate(ctx.tenant_id, account_id)
        return {"ok": True, "data": {
            "status": status, "sent": sent, "engine": engine,
            "platform_id": platform_id, "reason": reason,
            "count_today": rate.get("count", 0), "daily_limit": rate.get("limit", DAILY_LIMIT),
            "next_available_minutes": rate.get("next_minutes"),
            "lead": lead_out,
        }}

    # 1. 仅小红书可自动发（知乎/猪八戒只读）
    if (lead.get("source") or "xhs") != "xhs":
        return _result("source_unsupported", reason="该信源只读，仅小红书支持一键发送")

    # 2. 发送前置校验（引流词 + 雷同度，复用 V1 结果）
    if not (lead.get("check_lure_pass") and lead.get("check_dup_pass")):
        _audit(ctx, {"lead_id": lead_id, "status": "blocked_checks"})
        return _result("blocked_checks", reason="校验未通过（引流词/雷同度），禁止发送")

    # 3. 演练（默认）：只校验+预览，不真发、不计数、不改 lead
    if engine == "dryrun":
        _audit(ctx, {"lead_id": lead_id, "status": "dryrun"})
        return _result("dryrun", reason="演练态：未真实发送")

    # 4. 真发前的速率限制
    rate = check_rate(ctx.tenant_id, account_id)
    if not rate["allowed"]:
        _audit(ctx, {"lead_id": lead_id, "status": "rate_limited", "reason": rate["reason"]})
        return _result("rate_limited", reason=rate["reason"], rate=rate)

    # 5. 真发（仅 reajason；引擎/凭证未就绪 → 拒发，绝不误发）
    if engine != "reajason":
        return _result("engine_not_ready", reason=f"未知写引擎 '{engine}'，仅支持 reajason")

    platform_id, err = _reajason_send(lead)
    if err:
        _audit(ctx, {"lead_id": lead_id, "status": "engine_not_ready", "reason": err})
        return _result("engine_not_ready",
                       reason="写引擎未就绪（未配置/凭证无效），回退人工复制+打开原帖")

    # 6. 真发成功：计数 + 持久化 lead（OCC，单次重试）
    new_count = _record_send(ctx.tenant_id, account_id)
    now_iso = datetime.now(timezone.utc).isoformat()
    changes = {"lead_status": "touched", "touched_at": now_iso,
               "sent_at": now_iso, "send_platform_id": platform_id, "send_engine": engine}
    updated = None
    try:
        updated = storage.update_lead(ctx.tenant_id, lead_id,
                                      expected_rev=lead["rev"], **changes)
    except RevMismatch:
        fresh = storage.get_lead(ctx.tenant_id, lead_id)
        if fresh:
            updated = storage.update_lead(ctx.tenant_id, lead_id,
                                          expected_rev=fresh["rev"], **changes)
    _audit(ctx, {"lead_id": lead_id, "status": "sent",
                 "platform_id": platform_id, "count_today": new_count})
    rate_after = check_rate(ctx.tenant_id, account_id)
    return _result("sent", sent=True, platform_id=platform_id,
                   reason="已发送", rate=rate_after, lead_out=updated)


# ── 注册 ─────────────────────────────────────────────────────────────────

registry.register(
    name="outreach.send",
    schema={
        "description": (
            "对一条小红书 lead 半自动发出首触（逐条人工确认触发）。默认 dryrun 只校验不真发；"
            "真发走 ReaJason comment_note 且受速率限制（≤N/天 + 随机间隔）。"
            "知乎/猪八戒只读拒发。返回 {status, sent, engine, count_today, daily_limit, ...}。"
        ),
        "parameters": {
            "type": "object",
            "required": ["lead_id"],
            "properties": {
                "lead_id":    {"type": "string", "description": "要发送的 lead id"},
                "account_id": {"type": "string",
                               "description": "发送账号标识（速率计数维度），默认取 lead.persona_id"},
            },
        },
    },
    handler=_send_handler,
    cost_estimate=0.0,
    description="小红书半自动一键发送（dryrun/reajason + 速率限制）",
)
