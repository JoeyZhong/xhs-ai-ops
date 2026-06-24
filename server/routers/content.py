from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Union

import pandas as pd
from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from typing import Optional

from server.auth import AuthContext, verify_token
from server.errors import ErrorCode, error_response
from server.middleware.idempotency import IdempotencyRoute
import storage.factory

CONFIG_DIR = Path("config")
EPOCH = datetime(2000, 1, 1)

router = APIRouter(prefix="/api/v1/content", tags=["content"], route_class=IdempotencyRoute)

_MOCK_ITEM = {
    "content_id": "mock_001",
    "title": "mock title",
    "body": "mock content",
    "hashtags": ["tag1", "tag2"],
    "publish_at": "12:00",
    "angle": "mock_angle",
    "status": "draft",
}

_MOCK_STRATEGY = {
    "angle": "反直觉型",
    "hook": "做了4年自助售货机，才发现90%的人都搞错了选址重点",
    "key_points": [
        "人流量≠有效流量，学校 vs 工厂差异大",
        "谈判技巧：如何让物业主动找你",
        "合同陷阱：签3年还是5年更划算",
    ],
    "cta": "你在找点位还是运营中遇到了什么问题？评论区聊聊",
}

# ── Pydantic models ───────────────────────────────────────────────────────


class ContentGenerateRequest(BaseModel):
    goal_id: str
    topic: str
    strategy: Union[str, dict[str, Any]] = ""
    count: int = 3
    persist: bool = True
    topic_id: str | None = None
    strategy_id: str | None = None
    calendar_item_id: str | None = None
    knowledge_refs: list[dict] = []
    memory_refs: list[dict] = []


class ContentItem(BaseModel):
    content_id: str
    goal_id: str
    title: str
    alt_titles: list[str] = []
    body: str
    hashtags: list[str] = []
    publish_at: str = ""
    publish_reason: str = ""
    angle: str = ""
    status: Literal["draft", "edited", "scheduled", "rejected", "published"] = "draft"
    source: Literal["ai_generate", "legacy_xlsx", "manual"] = "ai_generate"
    created_at: str
    updated_at: str
    edit_count: int = 0
    topic_id: str | None = None
    strategy_id: str | None = None
    calendar_item_id: str | None = None
    knowledge_refs: list[dict] = []
    memory_refs: list[dict] = []


class ContentUpdateRequest(BaseModel):
    title: Optional[str] = None
    alt_titles: Optional[list[str]] = None
    body: Optional[str] = None
    hashtags: Optional[list[str]] = None
    publish_at: Optional[str] = None
    publish_reason: Optional[str] = None
    angle: Optional[str] = None
    status: Optional[Literal["draft", "edited", "scheduled", "rejected", "published"]] = None
    rev: int = 0


class StrategyRequest(BaseModel):
    goal_id: str
    keywords: list[str] = []
    user_intent: str = ""
    topic_id: str | None = None


# ── Helpers ───────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_str_list(val: Any) -> list[str]:
    if isinstance(val, list):
        return [str(v) for v in val]
    if isinstance(val, str):
        return [v.strip() for v in val.replace("，", ",").split(",") if v.strip()]
    return []


def _init_content_item(raw: dict, goal_id: str) -> dict[str, Any]:
    return {
        "content_id": raw.get("content_id", str(uuid.uuid4())[:8]),
        "goal_id": goal_id,
        "title": raw.get("title", ""),
        "alt_titles": _ensure_str_list(raw.get("alt_titles")),
        "body": raw.get("body", ""),
        "hashtags": _ensure_str_list(raw.get("hashtags")),
        "publish_at": raw.get("publish_at", ""),
        "publish_reason": raw.get("publish_reason", ""),
        "angle": raw.get("angle", ""),
        "status": raw.get("status", "draft"),
        "source": "ai_generate",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "edit_count": 0,
        "topic_id": raw.get("topic_id"),
        "strategy_id": raw.get("strategy_id"),
        "calendar_item_id": raw.get("calendar_item_id"),
        "knowledge_refs": raw.get("knowledge_refs", []),
        "memory_refs": raw.get("memory_refs", []),
    }


def _format_evidence_block(evidence_refs: list[dict] | None) -> str:
    if not evidence_refs:
        return ""
    lines = []
    for ev in evidence_refs[:3]:
        angle = str(ev.get("angle", "")).strip()
        hook = str(ev.get("hook", "")).strip()
        insight = str(ev.get("key_insight", "")).strip()
        if not (angle and hook and insight):
            continue
        lines.append(f'- 角度={angle}, hook="{hook}", 洞察="{insight}"')
    if not lines:
        return ""
    return (
        "\n── 同 funnel/同 angle 爆款样本 ──\n"
        + "\n".join(lines)
        + "\n──────────\n"
    )


def _format_playbook_block(playbook_summary: str | None) -> str:
    """P3.4: 注入 AnalystEvaluator 沉淀的已验证爆款规律（playbook 自动区）。"""
    summary = (playbook_summary or "").strip()
    if not summary:
        return ""
    return (
        "\n── 已验证爆款规律（playbook）──\n"
        f"{summary}\n"
        "──────────\n"
    )


# ── Content generation ────────────────────────────────────────────────────


def _generate_items(
    goal_id: str, topic: str, strategy: Union[str, dict[str, Any]], count: int, tenant_id: str,
    topic_id: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    backend = storage.factory.get_backend()
    data = backend.load_goals(tenant_id)
    goal = next((g for g in data["goals"] if g["id"] == goal_id), None)
    if goal is None:
        return [], None

    if isinstance(strategy, dict):
        angle_str = strategy.get("angle", "")
    else:
        angle_str = strategy

    settings = json.loads((CONFIG_DIR / "settings.json").read_text(encoding="utf-8"))
    provider = settings.get("llm_provider", "kimi")

    if provider == "mock":
        raw_items = [
            {**_MOCK_ITEM, "content_id": f"mock_{i:03d}", "angle": angle_str or "mock_angle"}
            for i in range(count)
        ]
        return [_init_content_item(item, goal_id) for item in raw_items], None

    try:
        from agent_tools.kimi import call_kimi
        from agent_tools.prompt_context import build_strategy_prompt_context

        ctx = build_strategy_prompt_context(
            backend=backend, tenant_id=tenant_id, goal=goal, topic_id=topic_id,
        )

        audience = goal.get("target_audience", {})
        kws = ", ".join(goal.get("keywords", [])[:5])
        brand = goal.get("brand_position", "")
        who = audience.get("who", "")
        pain = audience.get("pain_points", "")

        funnel_block = (
            f"\n本次选题属于「{ctx['funnel_stage']}」层，该层策略：{ctx['funnel_strategy_text']}\n"
            if ctx["funnel_strategy_text"] else ""
        )
        core_block = f"\n品牌核心信息：{ctx['core_message']}\n" if ctx["core_message"] else ""
        evidence_block = _format_evidence_block(ctx.get("evidence_refs"))
        playbook_block = _format_playbook_block(ctx.get("playbook_summary"))

        prompt = (
            f"你是小红书爆款内容创作者。账号定位：{brand}\n"
            f"目标受众：{who}，痛点：{pain}\n"
            f"选题：{topic}\n"
            f"内容角度：{angle_str}\n"
            f"{core_block}{funnel_block}\n"
            "── 包装规则（写作时必须遵循）──\n"
            f"{ctx['packaging_rules']}\n"
            "──────────\n\n"
            f"{evidence_block}"
            f"{playbook_block}"
            f"核心关键词：{kws}\n"
            f"要求：生成 {count} 篇不同角度的小红书笔记，"
            "每篇包含主标题（≤25字）、正文（500-800字）、标签（3-8个）、最佳发布时间。\n"
            "以标准 JSON 数组输出（必须使用双引号，不能使用单引号），字段：title, body, hashtags, publish_at, angle。"
        )
        raw, err = call_kimi(prompt, max_tokens=3000)
        if err:
            fallback = [{**_MOCK_ITEM, "content_id": f"fallback_{i:03d}"} for i in range(count)]
            return (
                [_init_content_item(item, goal_id) for item in fallback],
                f"AI 调用失败（{err}），已降级为示例内容",
            )

        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1] if "\n" in raw else raw[3:]
            raw = raw.rsplit("```", 1)[0] if "```" in raw[3:] else raw
            raw = raw.strip()

        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start >= 0 and end > start:
            json_str = raw[start:end]
            try:
                items = json.loads(json_str)
            except json.JSONDecodeError:
                import ast  # noqa: PLC0415
                try:
                    items = ast.literal_eval(json_str)
                except (ValueError, SyntaxError):
                    items = None
            if items and isinstance(items, list):
                enriched = [
                    _init_content_item(
                        {**item, "content_id": str(uuid.uuid4())[:8], "status": "draft"},
                        goal_id,
                    )
                    for item in items[:count]
                ]
                return enriched, None
        fallback = [{**_MOCK_ITEM, "content_id": f"fallback_{i:03d}"} for i in range(count)]
        preview = raw[:200].replace("\n", " ")
        return (
            [_init_content_item(item, goal_id) for item in fallback],
            f"AI 返回格式异常，已降级为示例内容（预览：{preview}）",
        )
    except Exception as e:
        fallback = [{**_MOCK_ITEM, "content_id": f"fallback_{i:03d}"} for i in range(count)]
        return (
            [_init_content_item(item, goal_id) for item in fallback],
            f"AI 调用失败（{type(e).__name__}: {e}），已降级为示例内容",
        )


# ── Endpoints ──────────────────────────────────────────────────────────────


@router.post("/generate")
async def generate_content(
    body: ContentGenerateRequest, auth: AuthContext = Depends(verify_token)
) -> dict:
    def _run() -> tuple[list[dict[str, Any]], str | None] | None:
        data = storage.factory.get_backend().load_goals(auth.tenant_id)
        if not any(g["id"] == body.goal_id for g in data["goals"]):
            return None
        return _generate_items(
            body.goal_id, body.topic, body.strategy, body.count, auth.tenant_id,
            topic_id=body.topic_id,
        )

    result = await run_in_threadpool(_run)
    if result is None:
        return error_response(
            status_code=404,
            code=ErrorCode.NOT_FOUND,
            message=f"goal '{body.goal_id}' not found",
        )
    items, error = result

    # Inject lifecycle fields from request into each generated item
    lifecycle_fields = {
        "topic_id": body.topic_id,
        "strategy_id": body.strategy_id,
        "calendar_item_id": body.calendar_item_id,
        "knowledge_refs": body.knowledge_refs,
        "memory_refs": body.memory_refs,
    }
    for item in items:
        for k, v in lifecycle_fields.items():
            if v is not None:
                item[k] = v

    if body.persist and items:
        def _persist():
            backend = storage.factory.get_backend()
            df = pd.DataFrame(items)
            backend.save_generated_posts(auth.tenant_id, df, meta={"goal_id": body.goal_id})

        await run_in_threadpool(_persist)

    resp: dict = {"items": items, "total": len(items)}
    if error:
        resp["error"] = error
    return resp


@router.post("/strategy")
async def generate_strategy(
    body: StrategyRequest,
    auth: AuthContext = Depends(verify_token),
) -> dict:
    def _run() -> dict | None:
        data = storage.factory.get_backend().load_goals(auth.tenant_id)
        if not any(g["id"] == body.goal_id for g in data["goals"]):
            return None

        settings = json.loads((CONFIG_DIR / "settings.json").read_text(encoding="utf-8"))
        provider = settings.get("llm_provider", "kimi")

        if provider == "mock":
            return _MOCK_STRATEGY, None

        try:
            from agent_tools.kimi import call_kimi
            from agent_tools.prompt_context import build_strategy_prompt_context
            goal = next(g for g in data["goals"] if g["id"] == body.goal_id)
            ctx = build_strategy_prompt_context(
                backend=storage.factory.get_backend(),
                tenant_id=auth.tenant_id,
                goal=goal,
                topic_id=body.topic_id,
            )
            audience = goal.get("target_audience", {})
            kws = ", ".join(body.keywords or goal.get("keywords", []))
            funnel_block = (
                f"\n本次选题属于「{ctx['funnel_stage']}」层（漏斗"
                f"{ {'traffic':'上','trust':'中','conversion':'下'}.get(ctx['funnel_stage'],'')}），"
                f"该层策略要求：{ctx['funnel_strategy_text']}\n"
                if ctx["funnel_strategy_text"] else ""
            )
            core_block = f"\n品牌核心信息：{ctx['core_message']}\n" if ctx["core_message"] else ""
            evidence_block = _format_evidence_block(ctx.get("evidence_refs"))
            playbook_block = _format_playbook_block(ctx.get("playbook_summary"))
            prompt = (
                "你是小红书内容策略专家。请基于以下信息，生成一条内容策略：\n\n"
                f"品牌定位：{goal.get('brand_position', '')}\n"
                f"目标受众：{audience.get('who', '')}，痛点：{audience.get('pain_points', '')}\n"
                f"{core_block}{funnel_block}\n"
                "── 包装规则（输出策略时必须遵循）──\n"
                f"{ctx['packaging_rules']}\n"
                "──────────\n\n"
                f"{evidence_block}"
                f"{playbook_block}"
                f"关键词：{kws}\n"
                f"用户意图：{body.user_intent}\n"
                "输出 JSON，字段：\n"
                "- angle: 角度类型（从五大公式中选一种：反直觉型、数字清单型、本地汇总型、工具型、焦虑共鸣型）\n"
                "- hook: 吸引式开头，**必须紧密围绕关键词**，不要重复使用之前用过的开头句式\n"
                "- key_points: 3-5个要点数组\n"
                "- cta: 结尾引导话术（按 CES 钩子规则，必须含开放式提问）"
            )
            raw, err = call_kimi(prompt, max_tokens=1500, json_mode=True)
            if err:
                return _MOCK_STRATEGY, f"AI 调用失败（{err}），已降级"
            if not raw or not raw.strip():
                return _MOCK_STRATEGY, "AI 返回为空，已降级为示例内容"
            strategy = json.loads(raw)
            if isinstance(strategy, dict) and "angle" not in strategy and "strategy" in strategy:
                inner = strategy["strategy"]
                if isinstance(inner, dict) and "angle" in inner:
                    strategy = inner
            return strategy, None
        except Exception as e:
            return _MOCK_STRATEGY, f"AI 调用失败（{type(e).__name__}: {e}），已降级"

    result = await run_in_threadpool(_run)
    if result is None:
        return error_response(
            status_code=404,
            code=ErrorCode.NOT_FOUND,
            message=f"goal '{body.goal_id}' not found",
        )
    strategy, error = result
    resp: dict = {"strategy": strategy}
    if error:
        resp["error"] = error
    return resp


@router.get("")
async def list_content(
    goal_id: str = Query("default"),
    status: str = Query(None),
    topic_id: str | None = Query(None),
    strategy_id: str | None = Query(None),
    calendar_item_id: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    auth: AuthContext = Depends(verify_token),
) -> dict:
    def _load() -> dict:
        backend = storage.factory.get_backend()
        df = backend.list_generated_posts(
            auth.tenant_id, since=EPOCH,
            topic_id=topic_id, strategy_id=strategy_id,
            calendar_item_id=calendar_item_id, status=status,
        )
        if df.empty:
            return {"items": [], "total": 0, "page": page, "page_size": page_size, "has_more": False}

        if "content_id" in df.columns:
            df = df.drop_duplicates(subset=["content_id"], keep="first")

        items: list[dict[str, Any]] = df.to_dict("records")

        for item in items:
            item["hashtags"] = _ensure_str_list(item.get("hashtags"))
            item["alt_titles"] = _ensure_str_list(item.get("alt_titles"))

        if goal_id != "default":
            items = [i for i in items if str(i.get("goal_id", "")) == goal_id]

        total = len(items)
        start = (page - 1) * page_size
        end = start + page_size
        return {"items": items[start:end], "total": total, "page": page, "page_size": page_size, "has_more": end < total}

    return await run_in_threadpool(_load)


@router.put("/{content_id}")
async def update_content(
    content_id: str,
    body: ContentUpdateRequest,
    auth: AuthContext = Depends(verify_token),
) -> dict:
    def _update() -> tuple[str, Any]:
        backend = storage.factory.get_backend()
        df = backend.list_generated_posts(auth.tenant_id, since=EPOCH)
        if df.empty or "content_id" not in df.columns:
            return ("not_found", None)

        mask = df["content_id"].astype(str) == content_id
        if not mask.any():
            return ("not_found", None)

        idx = df[mask].index[0]
        item = df.loc[idx].to_dict()

        if str(item.get("source", "")) in ("legacy_xlsx", "manual"):
            return ("conflict", item.get("source"))

        update_data = body.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            item[key] = value

        if item.get("status") == "draft":
            item["status"] = "edited"

        item["updated_at"] = _now_iso()
        item["edit_count"] = int(item.get("edit_count", 0)) + 1

        # pandas 写 Excel 时 list→str，读回来需还原
        item["hashtags"] = _ensure_str_list(item.get("hashtags"))
        item["alt_titles"] = _ensure_str_list(item.get("alt_titles"))

        # 保存单条更新（list_content dedup 时 keep=last 保证新值覆盖旧值）
        update_df = pd.DataFrame([item])
        backend.save_generated_posts(auth.tenant_id, update_df, meta={})
        return ("ok", item)

    status_tag, payload = await run_in_threadpool(_update)
    if status_tag == "not_found":
        return error_response(
            status_code=404,
            code=ErrorCode.NOT_FOUND,
            message=f"Content '{content_id}' not found",
        )
    if status_tag == "conflict":
        return error_response(
            status_code=409,
            code=ErrorCode.INVALID_STATUS_TRANSITION,
            message=f"Cannot edit {payload} items via API",
        )
    return payload
