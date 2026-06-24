from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from typing import Optional

from server.auth import AuthContext, verify_token
import storage.factory

CONFIG_DIR = Path("config")

router = APIRouter(prefix="/api/v1/topics", tags=["topics"])

_MOCK_TOPICS = [
    {"title": "深圳工厂区自助机点位地图（南山/宝安/龙岗）", "angle": "本地汇总型", "formula": "本地汇总型", "keywords": ["自助机点位", "深圳工厂"]},
    {"title": "谈了3个月的工厂点位，被这一句话废了", "angle": "焦虑共鸣型", "formula": "焦虑共鸣型", "keywords": ["点位谈判", "自助机"]},
    {"title": "7个黄金点位判断标准，90%的人看不懂第3条", "angle": "数字清单型", "formula": "数字清单型", "keywords": ["点位评估", "自助售卖机"]},
    {"title": "深圳学校自助机点位，他们不要钱还倒贴", "angle": "反直觉型", "formula": "反直觉型", "keywords": ["学校点位", "免费合作"]},
    {"title": "点位评分表：20分钟判断一个点位值不值", "angle": "工具型", "formula": "工具型", "keywords": ["点位选址", "评估工具"]},
]


class TopicsRequest(BaseModel):
    goal_id: str
    count: int = 5


class TopicsResponse(BaseModel):
    topics: list[dict]
    goal_id: str
    error: Optional[str] = None


def _mock_topics(count: int) -> list[dict]:
    base = _MOCK_TOPICS
    result = []
    for i in range(count):
        item = dict(base[i % len(base)])
        if i >= len(base):
            item["title"] = f"{item['title']}（{i + 1}）"
        result.append(item)
    return result


def _generate(goal_id: str, count: int, tenant_id: str) -> tuple[list[dict], str | None]:
    backend = storage.factory.get_backend()
    data = backend.load_goals(tenant_id)
    goal = next((g for g in data["goals"] if g["id"] == goal_id), None)
    if goal is None:
        return [], None

    settings = json.loads((CONFIG_DIR / "settings.json").read_text(encoding="utf-8"))
    provider = settings.get("llm_provider", "kimi")

    if provider == "mock":
        return _mock_topics(count), None

    try:
        from agent_tools.kimi import call_kimi
        kws = ", ".join(goal.get("keywords", []) + goal.get("keyword_library", []))
        audience = goal.get("target_audience", {})
        prompt = (
            f"你是小红书内容策划专家。\n"
            f"运营目标：{goal.get('objective', '')}，{goal.get('description', '')}\n"
            f"目标受众：{audience.get('who', '')}，痛点：{audience.get('pain_points', '')}\n"
            f"核心关键词：{kws}\n"
            f"请生成 {count} 个差异化小红书选题，每个包含：标题（≤25字）、内容角度（反直觉/数字清单/本地汇总/工具型/焦虑共鸣之一）、3个相关关键词。\n"
            f"以 JSON 数组格式输出，字段：title, angle, keywords。"
        )
        raw, err = call_kimi(prompt, max_tokens=1000)
        if err:
            return _mock_topics(count), f"AI 调用失败（{err}），已降级为示例选题"
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start >= 0 and end > start:
            topics = json.loads(raw[start:end])
            if topics:
                return topics[:count], None
        return _mock_topics(count), "AI 返回格式异常，已降级为示例选题"
    except Exception as e:
        return _mock_topics(count), f"AI 调用失败（{type(e).__name__}: {e}），已降级为示例选题"


@router.post("/generate", response_model=TopicsResponse)
async def generate_topics(body: TopicsRequest, auth: AuthContext = Depends(verify_token)) -> TopicsResponse:
    def _run():
        data = storage.factory.get_backend().load_goals(auth.tenant_id)
        if not any(g["id"] == body.goal_id for g in data["goals"]):
            return None
        return _generate(body.goal_id, body.count, auth.tenant_id)

    result = await run_in_threadpool(_run)
    if result is None:
        raise HTTPException(status_code=404, detail=f"goal '{body.goal_id}' not found")
    topics, error = result
    return TopicsResponse(topics=topics, goal_id=body.goal_id, error=error)
