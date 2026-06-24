"""
Spider_XHS · FastAPI 后端入口（Strangler Fig 第一步）。

启动：
    python -m uvicorn server.main:app --reload --port 8000

进程拓扑：
    Streamlit (:8501) ─┐
                        ├── 共享 config/ + xhs_data/ + memory/ + cookies.db
    FastAPI   (:8000) ─┘    （cookie_manager 用 SQLite WAL 多进程并发安全）

═══════════════════════════════════════════════════════════════════════════
  ⚠️ 红线（来自 openspec/specs/web-api/spec.md，违反必须 reject PR）
─────────────────────────────────────────────────────────────────────────
  1. 任何阻塞调用 MUST 用 fastapi.concurrency.run_in_threadpool 包装
     裸调以下 API 在 async handler 中是严重错误：
       - subprocess.Popen / subprocess.run
       - requests.get / requests.post
       - sqlite3.connect（含 cookie_manager.* 等）
       - pandas.read_excel / DataFrame.to_excel
       - playwright.sync_api.sync_playwright
       - PyExecJS

  2. CORS 白名单严格化，禁止 allow_origins=["*"]
     即使带 allow_credentials=False，也不允许通配符

  3. 所有业务端点必须挂 verify_token 依赖（tenant_id 来源唯一）。
     仅 /api/v1/health 为公开端点（监控用）
═══════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from dataclasses import asdict

from server.auth import AuthContext, verify_token
from server.stream_utils import sync_collect_worker
from server.routers import goals as goals_router
from server.routers import personas as personas_router
from server.routers import notes as notes_router
from server.routers import settings as settings_router
from server.routers import topics as topics_router
from server.routers import content as content_router
from server.routers import drafts as drafts_router
from server.routers import playbook as playbook_router
from server.routers import skills as skills_router
from server.routers import topics_v2 as topics_v2_router
from server.routers import calendar as calendar_router
from server.routers import strategies as strategies_router
from server.routers import packaging as packaging_router
from server.routers import intel as intel_router
from server.routers import analytics as analytics_router
from server.routers import orchestrator as orchestrator_router
from server.routers import leads as leads_router


# ── 开机自动加载 ~/.spider_xhs/.env ─────────────────────────────────────

_ENV_PATH = Path.home() / ".spider_xhs" / ".env"
if _ENV_PATH.exists():
    with open(_ENV_PATH, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _val = _line.split("=", 1)
                os.environ.setdefault(_key.strip(), _val.strip())


# ── 版本号（与 OpenAPI title/version 对齐） ───────────────────────────────

API_VERSION = "1.0.0"


# ── CORS 白名单（开发期常见 SPA 端口；生产由部署层进一步收紧） ────────────

_CORS_DEFAULT_ORIGINS = [
    "http://localhost:3000",   # Next.js / CRA 默认
    "http://127.0.0.1:3000",
    "http://localhost:5173",   # Vite 默认
    "http://127.0.0.1:5173",
    "http://localhost:4321",   # Astro 默认
    "http://127.0.0.1:4321",
]


def _cors_allowed_origins() -> list[str]:
    """Return explicit CORS origins for local, LAN, or Tailscale frontends."""
    extra = [
        origin.strip().rstrip("/")
        for origin in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",")
        if origin.strip()
    ]
    return list(dict.fromkeys(_CORS_DEFAULT_ORIGINS + extra))


CORS_ALLOWED_ORIGINS = _cors_allowed_origins()


# ── 调度器实例（lifespan 内初始化） ────────────────────────────────────

SCHEDULER_INSTANCE = None  # set by lifespan_startup; used by tests


# ── Lifespan（启动/停机钩子） ───────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan: start scheduler on boot, shut down on exit.

    Scheduler failure MUST NOT block the app from starting (红线 #4).
    """
    settings_path = Path("config/settings.json")
    try:
        cfg = json.loads(settings_path.read_text(encoding="utf-8"))
        enabled = cfg.get("scheduler", {}).get("enabled", False)
    except Exception:
        enabled = False

    if enabled:
        from agents.scheduler import SpiderScheduler  # noqa: PLC0415

        try:
            sched = SpiderScheduler()
            sched.register_default_jobs()
            sched.start()
            global SCHEDULER_INSTANCE
            SCHEDULER_INSTANCE = sched
            logging.getLogger("uvicorn.access").info(
                "Scheduler started (lock acquired)."
            )
        except Exception as exc:
            logging.getLogger("uvicorn.access").warning(
                "Scheduler failed to start (non-blocking): %s", exc
            )
    else:
        logging.getLogger("uvicorn.access").info("Scheduler disabled.")

    yield

    # Shutdown
    if SCHEDULER_INSTANCE is not None:
        try:
            SCHEDULER_INSTANCE.stop()
        except Exception:
            pass


# ── 应用实例 ─────────────────────────────────────────────────────────────

app = FastAPI(
    title="Spider_XHS Web API",
    version=API_VERSION,
    description=(
        "Spider_XHS 的 Web/HTTP 接入层（Strangler Fig 第一步）。\n\n"
        "与 Streamlit Dashboard 共存，逐步替代其前端职责。"
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],
)

app.include_router(goals_router.router)
app.include_router(personas_router.router)
app.include_router(notes_router.router)
app.include_router(settings_router.router)
app.include_router(topics_router.router)
app.include_router(content_router.router)
app.include_router(drafts_router.router)
app.include_router(playbook_router.router)
app.include_router(skills_router.router)
app.include_router(topics_v2_router.router)
app.include_router(calendar_router.router)
app.include_router(strategies_router.router)
app.include_router(packaging_router.router)
app.include_router(intel_router.router)
app.include_router(analytics_router.router)
app.include_router(orchestrator_router.router)
app.include_router(leads_router.router)


# ── 全局异常处理器（确保 500 也有 CORS 头，浏览器不报 Failed to fetch） ──

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger = logging.getLogger("uvicorn.error")
    logger.error("Unhandled exception: %s", exc, exc_info=exc)
    origin = request.headers.get("origin", "")
    allowed = CORS_ALLOWED_ORIGINS
    cors_origin = origin if origin in allowed else ""
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
        headers={"Access-Control-Allow-Origin": cors_origin} if cors_origin else None,
    )


# ── 请求模型 ─────────────────────────────────────────────────────────────

class CollectRequest(BaseModel):
    keywords: list[str]
    account_id: str = "default"
    skip_api: bool = False
    goal_id: str = "default"


# ── 端点定义 ─────────────────────────────────────────────────────────────

@app.get(
    "/api/v1/health",
    tags=["meta"],
    summary="服务健康检查",
    response_description="服务状态与版本号",
)
async def health() -> dict:
    """
    无副作用的健康检查端点。

    用于：
    - 部署后探活（k8s liveness / readiness probe）
    - CI/CD pipeline 的 smoke check
    - 监控系统轮询

    本端点 SHALL 不读 DB、不调外部 API、不耗 CPU。
    """
    return {"status": "ok", "version": API_VERSION}


@app.get(
    "/api/v1/scheduler/status",
    tags=["scheduler"],
    summary="调度器状态与已注册 cron",
)
async def scheduler_status(
    auth: AuthContext = Depends(verify_token),
) -> dict:
    """返回调度器运行状态和已注册的 cron job 信息（P3.4）。"""
    def _run() -> dict:
        sched = SCHEDULER_INSTANCE
        if sched is None:
            return {"running": False, "jobs": []}
        return {
            "running": sched.started,
            "started": sched.started,
            "jobs": sched.get_jobs_info(),
        }
    return await run_in_threadpool(_run)


# ── DAG 数据模型 ──────────────────────────────────────────────────────────

class DagNodeInput(BaseModel):
    id: str
    type: str
    prompt: str
    blocked_by: list[str] = []


class DagSubmitRequest(BaseModel):
    plan: list[DagNodeInput]
    dag_id: Optional[str] = None
    tenant_id: str = "default"


class DagSubmitResponse(BaseModel):
    dag_id: str
    task_ids: list[str]
    status: str = "submitted"


class DagTaskStatus(BaseModel):
    id: str
    type: str
    status: str
    result: Optional[dict] = None


class DagStatusResponse(BaseModel):
    dag_id: str
    tasks: list[DagTaskStatus]
    summary: dict


# ── DAG 端点 ──────────────────────────────────────────────────────────────

@app.post(
    "/api/v1/dag",
    tags=["dag"],
    summary="提交 DAG 任务计划（异步启动）",
    response_model=DagSubmitResponse,
)
async def dag_submit(req: DagSubmitRequest,
                     auth: AuthContext = Depends(verify_token)) -> DagSubmitResponse:
    """
    接收 plan（list[TaskNode]），校验后在后台线程执行，立即返回 dag_id。
    客户端轮询 GET /api/v1/dag/{dag_id} 获取实时进度。

    所有阻塞调用（LLM / 文件 IO）均在线程池内执行，不阻塞事件循环。
    """
    from agents.task_ledger import TaskNode
    from agents.master import HermesMaster

    tid = req.tenant_id if req.tenant_id != "default" else auth.tenant_id
    dag_id = req.dag_id or f"dag-{uuid.uuid4().hex[:8]}"
    nodes = [
        TaskNode(id=n.id, type=n.type, prompt=n.prompt, blocked_by=list(n.blocked_by))
        for n in req.plan
    ]
    task_ids = [n.id for n in nodes]

    async def _run_dag():
        master = HermesMaster(tenant_id=tid)
        await run_in_threadpool(master.submit_dag, nodes, dag_id, tenant_id=tid)

    asyncio.create_task(_run_dag())
    return DagSubmitResponse(dag_id=dag_id, task_ids=task_ids, status="submitted")


@app.get(
    "/api/v1/dag/{dag_id}",
    tags=["dag"],
    summary="查询 DAG 当前状态",
    response_model=DagStatusResponse,
)
async def dag_status(dag_id: str,
                     auth: AuthContext = Depends(verify_token)) -> DagStatusResponse:
    """
    从 ledger JSONL 加载该 dag 所有节点的最新状态。
    返回每个 task 的 status + summary 计数。
    """
    from agents.task_ledger import TaskLedger
    from pathlib import Path

    def _load():
        ledger = TaskLedger(Path(f"xhs_data/tasks/ledger_{auth.tenant_id}.jsonl"))
        return ledger.load_dag(dag_id)

    nodes = await run_in_threadpool(_load)
    if not nodes:
        raise HTTPException(status_code=404, detail=f"dag '{dag_id}' not found")

    tasks = [DagTaskStatus(id=n.id, type=n.type, status=n.status, result=n.result)
             for n in nodes]
    counts: dict[str, int] = {}
    for n in nodes:
        counts[n.status] = counts.get(n.status, 0) + 1
    summary = {
        s: counts.get(s, 0)
        for s in ("pending", "in_progress", "completed", "failed", "cancelled")
    }
    return DagStatusResponse(dag_id=dag_id, tasks=tasks, summary=summary)


@app.post(
    "/api/v1/dag/{dag_id}/retry/{node_id}",
    tags=["dag"],
    summary="重试 DAG 中单个失败节点",
)
async def dag_retry(dag_id: str, node_id: str,
                    auth: AuthContext = Depends(verify_token)) -> dict:
    """重试指定 DAG 节点（及其下游）。节点必须为 failed/cancelled/completed 状态。"""
    from agents.master import HermesMaster

    try:
        master = HermesMaster(tenant_id=auth.tenant_id)
        master.retry_task(dag_id, node_id, tenant_id=auth.tenant_id)
        return {"ok": True, "dag_id": dag_id, "node_id": node_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── 单 Agent 端点 ─────────────────────────────────────────────────────────

_agent_results: dict[str, dict[str, dict]] = {}

class AgentSubmitRequest(BaseModel):
    agent_type: str = "intel"
    prompt: str
    goal_id: str = ""
    budget_tokens: int = 50_000


@app.post(
    "/api/v1/agent/submit",
    tags=["agent"],
    summary="提交单 Agent 任务（异步）",
)
async def agent_submit(req: AgentSubmitRequest,
                      auth: AuthContext = Depends(verify_token)) -> dict:
    """后台运行单个 Agent，返回 task_id。客户端轮询 GET /api/v1/agent/{task_id}。"""
    from agents.master import HermesMaster
    from agents.base import AgentTask

    task_id = f"agent-{uuid.uuid4().hex[:8]}"
    _agent_results.setdefault(auth.tenant_id, {})[task_id] = {"status": "running", "result": None}

    async def _run():
        try:
            master = HermesMaster(tenant_id=auth.tenant_id)
            task = AgentTask(
                type=req.agent_type, prompt=req.prompt,
                goal_id=req.goal_id, budget_tokens=req.budget_tokens,
                tenant_id=auth.tenant_id,
            )
            result = await run_in_threadpool(master.submit, task)
            _agent_results[auth.tenant_id][task_id] = {"status": "completed", "result": asdict(result)}
        except Exception as e:
            _agent_results[auth.tenant_id][task_id] = {"status": "failed", "error": str(e)}

    asyncio.create_task(_run())
    return {"task_id": task_id, "status": "submitted"}


@app.get(
    "/api/v1/agent/{task_id}",
    tags=["agent"],
    summary="查询单 Agent 任务状态",
)
async def agent_status(task_id: str,
                       auth: AuthContext = Depends(verify_token)) -> dict:
    entry = _agent_results.get(auth.tenant_id, {}).get(task_id)
    if not entry:
        raise HTTPException(404, f"task '{task_id}' not found")
    return entry


@app.post(
    "/api/v1/collect/stream",
    tags=["collect"],
    summary="关键词采集（SSE 实时流）",
)
async def collect_stream(req: CollectRequest,
                        auth: AuthContext = Depends(verify_token)) -> EventSourceResponse:
    """
    在独立线程中运行同步爬虫 Worker，通过 SSE 实时推送每条笔记的抓取进度。

    所有阻塞调用（requests / PyExecJS / Playwright / pandas）均在 Worker
    线程内执行，不阻塞 FastAPI 事件循环。
    客户端断连时 stop_event 被 set，Worker 在下一个关键词间隔处退出，
    并将已抓取的半成品数据落盘。
    """
    queue: asyncio.Queue = asyncio.Queue()
    stop_event = threading.Event()
    loop = asyncio.get_running_loop()

    loop.run_in_executor(
        None,
        lambda: sync_collect_worker(
            req.keywords,
            queue,
            loop,
            account_id=req.account_id,
            stop_event=stop_event,
            skip_api=req.skip_api,
            goal_id=req.goal_id,
            tenant_id=auth.tenant_id,
        ),
    )

    async def generator():
        try:
            while True:
                msg = await queue.get()
                yield {"data": json.dumps(msg, ensure_ascii=False)}
                if msg.get("type") == "done":
                    break
        finally:
            stop_event.set()

    return EventSourceResponse(generator())
