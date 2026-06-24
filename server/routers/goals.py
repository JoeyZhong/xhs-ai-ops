from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from server.auth import AuthContext, verify_token
import storage.factory

CONFIG_DIR = Path("config")

router = APIRouter(prefix="/api/v1/goals", tags=["goals"])


class GoalCreate(BaseModel):
    name: str
    objective: str = ""
    description: str = ""


# ── CRUD ─────────────────────────────────────────────────────────────────

@router.get("")
async def list_goals(auth: AuthContext = Depends(verify_token)) -> dict:
    def _run():
        return storage.factory.get_backend().load_goals(auth.tenant_id)
    return await run_in_threadpool(_run)


@router.get("/{goal_id}")
async def get_goal(goal_id: str, auth: AuthContext = Depends(verify_token)) -> dict:
    data = await run_in_threadpool(storage.factory.get_backend().load_goals, auth.tenant_id)
    for g in data["goals"]:
        if g["id"] == goal_id:
            return g
    raise HTTPException(status_code=404, detail=f"goal '{goal_id}' not found")


@router.post("", status_code=201)
async def create_goal(body: GoalCreate, auth: AuthContext = Depends(verify_token)) -> dict:
    data = await run_in_threadpool(storage.factory.get_backend().load_goals, auth.tenant_id)
    new_goal: dict = {
        "id": f"goal_{uuid.uuid4().hex[:8]}",
        "name": body.name,
        "objective": body.objective,
        "description": body.description,
        "status": "active",
        "target_audience": {},
        "brand_position": "",
        "benchmark_accounts": [],
        "keywords": [],
        "keyword_library": [],
        "topic_library": [],
        "content_calendar": [],
        "used_angles": [],
        "campaigns": [],
        "performance": {"posts": []},
    }
    data["goals"].append(new_goal)
    await run_in_threadpool(storage.factory.get_backend().save_goals, auth.tenant_id, data)
    return new_goal


@router.put("/{goal_id}")
async def update_goal(
    goal_id: str,
    body: dict[str, Any] = Body(...),
    auth: AuthContext = Depends(verify_token),
) -> dict:
    data = await run_in_threadpool(storage.factory.get_backend().load_goals, auth.tenant_id)
    for i, g in enumerate(data["goals"]):
        if g["id"] == goal_id:
            g.update(body)
            data["goals"][i] = g
            await run_in_threadpool(storage.factory.get_backend().save_goals, auth.tenant_id, data)
            return g
    raise HTTPException(status_code=404, detail=f"goal '{goal_id}' not found")


# ── Strategy generation ───────────────────────────────────────────────────

_MOCK_STRATEGY = {
    "core_message": "用闲置场地换被动收入，设备免费放置，无风险合作",
    "content_funnel": {
        "top_30pct": "借「餐饮选址/商业选址」高流量词引入泛流量",
        "mid_40pct": "行业干货（选址/收益/谈判/日常）建立专业信任",
        "bottom_30pct": "深圳本地化招商内容直接触达点位方",
    },
}


@router.post("/{goal_id}/strategy/generate")
async def generate_strategy(
    goal_id: str,
    auth: AuthContext = Depends(verify_token),
) -> dict:
    """AI 生成整体内容策略，基于 goal 信息调用 Kimi。"""
    data = await run_in_threadpool(storage.factory.get_backend().load_goals, auth.tenant_id)
    goal = next((g for g in data["goals"] if g["id"] == goal_id), None)
    if goal is None:
        raise HTTPException(status_code=404, detail=f"goal '{goal_id}' not found")

    def _run():
        settings = json.loads((CONFIG_DIR / "settings.json").read_text(encoding="utf-8"))
        provider = settings.get("llm_provider", "kimi")

        if provider == "mock":
            return _MOCK_STRATEGY, None

        try:
            from agent_tools.kimi import call_kimi
            audience = goal.get("target_audience", {})
            kws = ", ".join(goal.get("keywords", []) + goal.get("keyword_library", []))
            prompt = (
                "你是小红书内容策略专家。请基于以下运营信息，生成一份整体内容策略：\n\n"
                f"品牌定位：{goal.get('brand_position', '')}\n"
                f"运营目标：{goal.get('objective', '')}，{goal.get('description', '')}\n"
                f"目标受众：{audience.get('who', '')}，痛点：{audience.get('pain_points', '')}\n"
                f"核心关键词：{kws}\n\n"
                "请输出 JSON 格式，包含以下字段：\n"
                "- core_message: 一句话核心传播信息\n"
                "- content_funnel: 三层漏斗策略\n"
                "  - top_30pct: 纯文本字符串，引流层策略说明\n"
                "  - mid_40pct: 纯文本字符串，信任层策略说明\n"
                "  - bottom_30pct: 纯文本字符串，转化层策略说明\n"
                "注意：top_30pct/mid_40pct/bottom_30pct 必须是纯文本字符串，不要嵌套对象。"
            )
            raw, err = call_kimi(prompt, max_tokens=1500)
            if err:
                return _MOCK_STRATEGY, f"AI 调用失败（{err}），已降级为示例策略"
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                json_str = raw[start:end]
                try:
                    strategy = json.loads(json_str)
                except json.JSONDecodeError:
                    import ast  # noqa: PLC0415
                    try:
                        strategy = ast.literal_eval(json_str)
                    except (ValueError, SyntaxError):
                        strategy = None
                if strategy and isinstance(strategy, dict):
                    funnel = strategy.get("content_funnel")
                    if isinstance(funnel, dict):
                        for key in ("top_30pct", "mid_40pct", "bottom_30pct"):
                            val = funnel.get(key)
                            if isinstance(val, dict):
                                desc = val.get("description", "")
                                strs = val.get("strategy")
                                if isinstance(strs, list):
                                    funnel[key] = f"{desc}：{'；'.join(str(s) for s in strs if isinstance(s, str))}"
                                else:
                                    funnel[key] = desc if isinstance(desc, str) else ""
                            elif not isinstance(val, str):
                                funnel[key] = ""
                    return strategy, None
            return _MOCK_STRATEGY, "AI 返回格式异常，已降级为示例策略"
        except Exception as e:
            return _MOCK_STRATEGY, f"AI 调用失败（{type(e).__name__}: {e}），已降级为示例策略"

    result = await run_in_threadpool(_run)
    strategy, error = result
    resp: dict = {"strategy": strategy}
    if error:
        resp["error"] = error
    return resp
