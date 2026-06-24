"""Insight Evidence Pool extraction tool.

抽取高 CES 笔记中的可复用样本: angle / funnel_stage / hook / key_insight。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from agent_tools import registry
from agent_tools.registry import ToolContext, ToolInputError


ANGLE_ENUM = {"反直觉型", "数字清单型", "本地汇总型", "工具型", "焦虑共鸣型"}
FUNNEL_ENUM = {"traffic", "trust", "conversion"}
EPOCH = datetime(2000, 1, 1)

_NOTE_ID_KEYS = ("source_note_id", "note_id", "笔记ID", "noteId", "id")
_TITLE_KEYS = ("title", "标题", "note_title")
_CONTENT_KEYS = ("content", "正文", "内容", "desc", "description")
_KEYWORD_KEYS = ("keyword", "关键词", "搜索关键词")
_FUNNEL_KEYS = ("funnel_stage", "漏斗层级")
_CES_KEYS = ("ces_score", "CES", "ces", "互动", "互动量")


def _first_text(row: dict[str, Any], keys: tuple[str, ...], default: str = "") -> str:
    for key in keys:
        val = row.get(key)
        if val is None:
            continue
        text = str(val).strip()
        if text and text.lower() != "nan":
            return text
    return default


def _num(val: Any, default: float = 0.0) -> float:
    try:
        if val is None:
            return default
        text = str(val).replace(",", "").strip()
        if not text or text.lower() == "nan":
            return default
        return float(text)
    except Exception:
        return default


def _ces_score(row: dict[str, Any]) -> float:
    for key in _CES_KEYS:
        if key in row:
            return _num(row.get(key))

    likes = _num(row.get("likes", row.get("点赞数", row.get("点赞", 0))))
    collects = _num(row.get("collects", row.get("收藏数", row.get("收藏", 0))))
    comments = _num(row.get("comments_count", row.get("评论数", row.get("评论", 0))))
    shares = _num(row.get("shares", row.get("分享数", row.get("分享", 0))))
    follows = _num(row.get("follows", row.get("关注数", row.get("关注", 0))))
    return likes + collects + comments * 4 + shares * 4 + follows * 8


def _normalize_funnel(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if text in FUNNEL_ENUM else None


def _infer_funnel_stage(row: dict[str, Any]) -> str:
    for key in _FUNNEL_KEYS:
        funnel = _normalize_funnel(row.get(key))
        if funnel:
            return funnel

    text = " ".join([
        _first_text(row, _TITLE_KEYS),
        _first_text(row, _CONTENT_KEYS),
        _first_text(row, _KEYWORD_KEYS),
    ])
    if any(word in text for word in ("招商", "合作", "物业", "点位", "转化")):
        return "conversion"
    if any(word in text for word in ("技巧", "避坑", "评分", "清单", "复盘", "干货")):
        return "trust"
    return "traffic"


def _normalize_note(row: dict[str, Any]) -> dict[str, Any]:
    note_id = _first_text(row, _NOTE_ID_KEYS)
    return {
        "note_id": note_id,
        "title": _first_text(row, _TITLE_KEYS),
        "content": _first_text(row, _CONTENT_KEYS),
        "keyword": _first_text(row, _KEYWORD_KEYS),
        "funnel_stage": _infer_funnel_stage(row),
        "ces_score": _ces_score(row),
        "raw": dict(row),
    }


def _truncate(text: Any, limit: int) -> str:
    value = str(text or "").strip()
    return value[:limit]


def _strip_code_fence(raw: str) -> str:
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def _parse_json_array(raw: str) -> list[dict[str, Any]]:
    text = _strip_code_fence(raw)
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        candidate = text[start:end + 1]
    else:
        start_obj = text.find("{")
        end_obj = text.rfind("}")
        candidate = text[start_obj:end_obj + 1] if start_obj >= 0 and end_obj > start_obj else text

    transforms = [
        lambda s: s,
        lambda s: re.sub(r",\s*([}\]])", r"\1", s),
        lambda s: re.sub(r",\s*([}\]])", r"\1", s).replace("'", '"'),
    ]
    for transform in transforms:
        try:
            parsed = json.loads(transform(candidate))
            if isinstance(parsed, dict):
                return [parsed]
            if isinstance(parsed, list) and all(isinstance(x, dict) for x in parsed):
                return parsed
        except Exception:
            continue
    raise ValueError("Kimi response is not a JSON object/array")


def _prompt_for_notes(notes: list[dict[str, Any]]) -> str:
    payload = [
        {
            "source_note_id": note["note_id"],
            "title": note["title"],
            "content": note["content"][:900],
            "keyword": note["keyword"],
            "ces_score": note["ces_score"],
            "funnel_stage_hint": note["funnel_stage"],
        }
        for note in notes
    ]
    return (
        "你是小红书内容分析员。请从以下高 CES 笔记中提取可复用的爆款证据。\n"
        "必须只输出合法 JSON 数组，禁止 markdown 和解释文字。\n"
        "每个元素字段必须为：source_note_id, angle, funnel_stage, hook, key_insight。\n"
        "angle 只能从：反直觉型、数字清单型、本地汇总型、工具型、焦虑共鸣型 中选择。\n"
        "funnel_stage 只能从：traffic、trust、conversion 中选择。\n"
        "hook 不超过 100 字，key_insight 不超过 300 字。\n\n"
        f"待分析笔记：\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _coerce_evidence(item: dict[str, Any], note: dict[str, Any], tenant_id: str) -> dict:
    note_id = str(item.get("source_note_id") or note["note_id"]).strip()
    if not note_id:
        raise ValueError("source_note_id is required")

    angle = str(item.get("angle", "")).strip()
    if angle not in ANGLE_ENUM:
        raise ValueError(f"invalid angle for {note_id}: {angle!r}")

    funnel_stage = _normalize_funnel(item.get("funnel_stage")) or note["funnel_stage"]
    if funnel_stage not in FUNNEL_ENUM:
        raise ValueError(f"invalid funnel_stage for {note_id}: {funnel_stage!r}")

    hook = _truncate(item.get("hook"), 100)
    key_insight = _truncate(item.get("key_insight"), 300)
    if not hook or not key_insight:
        raise ValueError(f"hook/key_insight required for {note_id}")

    evidence = {
        "source_note_id": note_id,
        "angle": angle,
        "funnel_stage": funnel_stage,
        "hook": hook,
        "key_insight": key_insight,
        "ces_score": note["ces_score"],
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "raw": {
            "note_id": note_id,
            "title": note["title"],
            "content": note["content"],
            "keyword": note["keyword"],
            "funnel_stage": note["funnel_stage"],
            "ces_score": note["ces_score"],
        },
    }
    if tenant_id:
        evidence["evidence_id"] = f"{tenant_id}:{note_id}"
    return evidence


def extract_evidence(note_id: str, raw_note: dict[str, Any]) -> dict:
    """Extract one evidence row with a single Kimi call."""
    from agent_tools.kimi import call_kimi

    note = _normalize_note({**raw_note, "note_id": note_id})
    raw, err = call_kimi(_prompt_for_notes([note]), max_tokens=1200, temperature=0.2)
    if err:
        raise ValueError(err)
    item = _parse_json_array(raw)[0]
    return _coerce_evidence(item, note, tenant_id="")


def _call_batch(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from agent_tools.kimi import call_kimi

    raw, err = call_kimi(_prompt_for_notes(notes), max_tokens=3000, temperature=0.2)
    if err:
        raise ValueError(err)
    return _parse_json_array(raw)


def _store_items(
    *,
    tenant_id: str,
    storage: Any,
    notes: list[dict[str, Any]],
    items: list[dict[str, Any]],
) -> tuple[int, list[dict[str, str]]]:
    by_id = {note["note_id"]: note for note in notes}
    errors: list[dict[str, str]] = []
    count = 0
    for idx, item in enumerate(items):
        note_id = str(item.get("source_note_id") or "").strip()
        note = by_id.get(note_id) if note_id else (notes[idx] if idx < len(notes) else None)
        if note is None:
            errors.append({"note_id": note_id or "unknown", "error": "cannot match evidence to source note"})
            continue
        try:
            evidence = _coerce_evidence(item, note, tenant_id)
            storage.upsert_evidence(tenant_id, evidence)
            count += 1
        except Exception as exc:
            errors.append({"note_id": note.get("note_id", ""), "error": str(exc)})
    return count, errors


def extract_evidence_for_notes(
    *,
    tenant_id: str,
    storage: Any,
    notes: list[dict[str, Any]],
    batch_size: int = 10,
) -> dict[str, Any]:
    normalized = [
        note for note in (_normalize_note(n) for n in notes)
        if note["note_id"]
    ]
    batch_size = max(1, min(int(batch_size or 10), 10))

    extracted = 0
    errors: list[dict[str, str]] = []
    fallback_batches = 0

    for start in range(0, len(normalized), batch_size):
        batch = normalized[start:start + batch_size]
        try:
            items = _call_batch(batch)
            n, batch_errors = _store_items(
                tenant_id=tenant_id, storage=storage, notes=batch, items=items,
            )
            extracted += n
            errors.extend(batch_errors)
        except Exception as exc:
            fallback_batches += 1
            for note in batch:
                try:
                    items = _call_batch([note])
                    n, item_errors = _store_items(
                        tenant_id=tenant_id, storage=storage, notes=[note], items=items,
                    )
                    extracted += n
                    errors.extend(item_errors)
                except Exception as single_exc:
                    errors.append({"note_id": note["note_id"], "error": str(single_exc)})

    status = "partial_failure" if errors else "ok"
    return {
        "status": status,
        "extracted_count": extracted,
        "skipped_count": len(notes) - len(normalized),
        "errors": errors,
        "fallback_batches": fallback_batches,
    }


def _existing_note_ids(storage: Any, tenant_id: str) -> set[str]:
    try:
        return {
            str(item.get("source_note_id"))
            for item in storage.list_evidence(tenant_id, limit=100000)
            if item.get("source_note_id")
        }
    except Exception:
        return set()


def candidate_notes_from_storage(
    *,
    storage: Any,
    tenant_id: str,
    ces_threshold: float,
) -> tuple[list[dict[str, Any]], int]:
    df = storage.list_collected_data(tenant_id, since=EPOCH)
    if df is None or getattr(df, "empty", True):
        return [], 0

    existing = _existing_note_ids(storage, tenant_id)
    candidates: list[dict[str, Any]] = []
    skipped = 0
    for row in df.to_dict("records"):
        note = _normalize_note(row)
        if not note["note_id"] or note["ces_score"] <= ces_threshold or note["note_id"] in existing:
            skipped += 1
            continue
        candidates.append(note["raw"])
    return candidates, skipped


def extract_evidence_from_storage(
    *,
    tenant_id: str,
    storage: Any,
    ces_threshold: float,
    batch_size: int = 10,
) -> dict[str, Any]:
    notes, skipped = candidate_notes_from_storage(
        storage=storage,
        tenant_id=tenant_id,
        ces_threshold=ces_threshold,
    )
    result = extract_evidence_for_notes(
        tenant_id=tenant_id,
        storage=storage,
        notes=notes,
        batch_size=batch_size,
    )
    result["skipped_count"] += skipped
    return result


def _handler(args: dict, ctx: ToolContext) -> dict:
    if ctx.storage is None:
        raise ToolInputError("ctx.storage is required")

    batch_size = int(args.get("batch_size") or 10)
    notes = args.get("notes") or []
    if notes:
        data = extract_evidence_for_notes(
            tenant_id=ctx.tenant_id,
            storage=ctx.storage,
            notes=notes,
            batch_size=batch_size,
        )
    else:
        threshold = float(args.get("ces_threshold") or 250)
        data = extract_evidence_from_storage(
            tenant_id=ctx.tenant_id,
            storage=ctx.storage,
            ces_threshold=threshold,
            batch_size=batch_size,
        )

    return {"ok": True, "data": data}


registry.register(
    name="intel.extract_evidence",
    schema={
        "description": "Extract structured content evidence from high-CES XHS notes.",
        "parameters": {
            "type": "object",
            "properties": {
                "notes": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Optional explicit notes to extract. If omitted, storage collected notes are scanned.",
                },
                "ces_threshold": {"type": "number", "default": 250},
                "batch_size": {"type": "integer", "minimum": 1, "maximum": 10, "default": 10},
            },
        },
    },
    handler=_handler,
    cost_estimate=3000.0,
    description="Extract angle/hook/key_insight evidence and upsert into storage",
)
