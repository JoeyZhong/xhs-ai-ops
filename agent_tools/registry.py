"""
工具注册中心。

模式（参考 Hermes Agent tools/registry.py）：
- 每个 Tool 模块在导入时调用 register(...) 自注册
- registry.invoke(name, args, ctx) 是统一调用入口
- 输入经过 JSON Schema 校验（参考 OpenClaw ToolInputError）
- 输出统一格式：{ok, data, error, meta}
"""

from __future__ import annotations

import os
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from jsonschema import Draft202012Validator, ValidationError

from agent_tools.idempotency import (
    IdempotencyCache, compute_key, is_idempotency_applicable,
)


# ── 异常体系（仿 OpenClaw） ─────────────────────────────────────────────────

class ToolError(Exception):
    """所有 Tool 相关异常的基类"""


class ToolAlreadyRegistered(ToolError):
    """同名 Tool 重复注册"""


class ToolNotFound(ToolError):
    """调用时找不到 Tool"""


class ToolInputError(ToolError):
    """参数校验失败"""


class ToolEnvironmentError(ToolError):
    """所需环境变量未设置"""


class ToolExecutionError(ToolError):
    """Tool handler 内部异常"""


class ToolPolicyViolation(ToolError):
    """policy 拒绝调用（由 Agent 层使用）"""

    def __init__(self, tool_name: str, agent_name: str = ""):
        self.tool_name = tool_name
        self.agent_name = agent_name
        super().__init__(f"policy denied: {agent_name} -> {tool_name}")


# ── 数据结构 ───────────────────────────────────────────────────────────────

@dataclass
class ToolContext:
    """传递给每个 handler 的运行上下文。"""
    tenant_id: str = "default"
    task_id: str = ""
    storage: Any = None        # StorageBackend，可选
    audit: Any = None          # AuditLogger，可选
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolDef:
    name: str
    schema: dict                            # JSON Schema（OpenAI tool calling 兼容）
    handler: Callable[[dict, ToolContext], dict]
    requires_env: List[str] = field(default_factory=list)
    cost_estimate: float = 0.0              # tokens 或秒，用于 budget
    description: str = ""


# ── 注册表 ─────────────────────────────────────────────────────────────────

_REGISTRY: Dict[str, ToolDef] = {}

# Idempotency cache 按 tenant 隔离（P1.2）
_IDEMPOT_CACHES: Dict[str, IdempotencyCache] = {}


def _get_idempot_cache(tenant_id: str) -> IdempotencyCache:
    """获取或创建 tenant 的 idempotency cache。"""
    cache = _IDEMPOT_CACHES.get(tenant_id)
    if cache is None:
        cache = IdempotencyCache(tenant_id)
        _IDEMPOT_CACHES[tenant_id] = cache
    return cache


def _fill_defaults(args: dict, schema: dict) -> dict:
    """按 JSON Schema 规范化 args：填 default + 零值填充可选字段。

    解决 LLM 工具调用时传参不稳定（有时传默认值、有时不传）导致
    compute_key 不一致、idempotency 缓存永不命中的问题。

    策略：
    1. 有 default 的字段 → 用 default 填充
    2. 无 default 且非 required 的字段 → 用该类型零值填充
       （string→"", integer→0, number→0.0, boolean→False 等）
       这样"没传"和"传零值"等价，compute_key 看到的 args 稳定。
    """
    filled = dict(args)
    params = schema.get("parameters", {})
    props = params.get("properties", {})
    required = set(params.get("required", []))

    for key, prop_schema in props.items():
        if key in filled:
            continue
        if "default" in prop_schema:
            filled[key] = prop_schema["default"]
        elif key not in required:
            # 零值填充，避免 LLM 不传可选字段导致 key 不一致
            ptype = prop_schema.get("type")
            if ptype == "string":
                filled[key] = ""
            elif ptype == "integer":
                filled[key] = 0
            elif ptype == "number":
                filled[key] = 0.0
            elif ptype == "boolean":
                filled[key] = False
            elif ptype == "array":
                filled[key] = []
            elif ptype == "object":
                filled[key] = {}
    return filled


def register(
    name: str,
    schema: dict,
    handler: Callable[[dict, ToolContext], dict],
    *,
    requires_env: Optional[List[str]] = None,
    cost_estimate: float = 0.0,
    description: str = "",
) -> Callable:
    """注册一个 Tool。模块顶部直接调用，不要包在函数里。"""
    if name in _REGISTRY:
        raise ToolAlreadyRegistered(f"tool '{name}' already registered")
    _REGISTRY[name] = ToolDef(
        name=name,
        schema=schema,
        handler=handler,
        requires_env=requires_env or [],
        cost_estimate=cost_estimate,
        description=description,
    )
    return handler


def _llm_safe_name(name: str) -> str:
    """Kimi/OpenAI 的 function name 不允许带点号，转为双下划线。"""
    return name.replace(".", "__")


def _from_llm_safe(name: str) -> str:
    """LLM 返回的 tool_call.name 反向映射回内部点号命名。"""
    return name.replace("__", ".")


def get(name: str) -> ToolDef:
    if name not in _REGISTRY:
        # 兼容 LLM 回传的下划线版本
        alt = _from_llm_safe(name)
        if alt in _REGISTRY:
            name = alt
    if name not in _REGISTRY:
        raise ToolNotFound(f"tool '{name}' not registered")
    return _REGISTRY[name]


def list_tools() -> List[str]:
    return sorted(_REGISTRY.keys())


def get_schemas() -> List[dict]:
    """返回所有 Tool 的 OpenAI tool calling 格式 schema（name 已转为 LLM-safe 形式）。"""
    return [
        {"type": "function", "function": {**t.schema, "name": _llm_safe_name(t.name)}}
        for t in _REGISTRY.values()
    ]


def filter_schemas(allowed_names: List[str]) -> List[dict]:
    """按白名单过滤 schema，给特定 Sub Agent 用。"""
    return [
        {"type": "function", "function": {**t.schema, "name": _llm_safe_name(t.name)}}
        for t in _REGISTRY.values()
        if t.name in allowed_names
    ]


# ── 调用入口 ───────────────────────────────────────────────────────────────

def invoke(name: str, args: dict, ctx: Optional[ToolContext] = None) -> dict:
    """
    调用 Tool 的统一入口。

    返回标准格式：
        {ok: bool, data: any, error: str|None, meta: {duration_ms, tool, ...}}
    """
    ctx = ctx or ToolContext()
    tool = get(name)  # 内部已处理 LLM-safe 名字反向映射
    name = tool.name  # 统一用内部点号名字记录
    started = time.perf_counter()
    base_meta = {"tool": name, "tenant_id": ctx.tenant_id, "task_id": ctx.task_id}

    # 1. 环境变量检查
    missing = [v for v in tool.requires_env if not os.environ.get(v)]
    if missing:
        return _fail(ToolEnvironmentError(f"missing env: {missing}"),
                      base_meta, started)

    # 2. 参数 schema 校验
    params_schema = tool.schema.get("parameters")
    if params_schema:
        try:
            Draft202012Validator(params_schema).validate(args)
        except ValidationError as e:
            return _fail(ToolInputError(f"{e.message} (path: {list(e.absolute_path)})"),
                          base_meta, started)

    # 3. Idempotency 检查（P1.2）
    idempot_key = None
    if is_idempotency_applicable(name):
        agent_role = (ctx.extra or {}).get("agent_role", "")
        # task_id 不入 key（早期设计 bug 已修）：跨 task 同 args 应复用缓存
        # 先按 schema default 填充缺失字段，避免 LLM 传参不稳定导致 key 不一致
        normalized_args = _fill_defaults(args, tool.schema)
        idempot_key = compute_key(name, normalized_args, agent_role)
        cached = _get_idempot_cache(ctx.tenant_id).get(idempot_key)
        if cached is not None:
            cached.setdefault("meta", {}).update({
                **base_meta,
                "idempotency_hit": True,
                "duration_ms": 0,
            })
            return cached

    # 4. handler 调用
    try:
        result = tool.handler(args, ctx)
    except ToolError as e:
        return _fail(e, base_meta, started)
    except Exception as e:
        return _fail(ToolExecutionError(str(e)), base_meta, started,
                      trace=traceback.format_exc())

    # 5. 规范化返回值
    if not isinstance(result, dict):
        return _fail(ToolExecutionError(f"invalid handler return: {type(result).__name__}"),
                      base_meta, started)
    result.setdefault("ok", True)
    result.setdefault("data", None)
    result.setdefault("error", None)
    meta = result.setdefault("meta", {})
    meta.update(base_meta)
    meta["duration_ms"] = int((time.perf_counter() - started) * 1000)

    # 6. 写 Idempotency cache（仅成功结果，P1.2.5）
    if idempot_key is not None:
        _get_idempot_cache(ctx.tenant_id).set(idempot_key, result, name)

    # 7. 写审计（如果 ctx 有 audit）
    if ctx.audit:
        try:
            ctx.audit.write({
                "kind": "tool_call",
                **base_meta,
                "ok": result["ok"],
                "duration_ms": meta["duration_ms"],
            })
        except Exception:
            pass  # 审计失败不影响业务

    return result


def _fail(err: Exception, base_meta: dict, started: float, trace: str = "") -> dict:
    duration = int((time.perf_counter() - started) * 1000)
    return {
        "ok": False,
        "data": None,
        "error": str(err),
        "meta": {**base_meta, "duration_ms": duration,
                 "error_type": type(err).__name__,
                 **({"trace": trace} if trace else {})},
    }


# ── 测试用：清空注册表（不要在生产代码用） ─────────────────────────────────

def _reset_for_tests():
    _REGISTRY.clear()
    # 清除 idempotency caches（避免测试间状态泄漏）
    for cache in _IDEMPOT_CACHES.values():
        cache.clear()
    _IDEMPOT_CACHES.clear()
