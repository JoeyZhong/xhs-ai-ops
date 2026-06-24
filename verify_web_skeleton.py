#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FastAPI 骨架验收测试（V1.1+ 修订版）

旧版（strangler-fastapi-skeleton 时期）的 "仅 1 个 /api/v1/health" 断言
已被 V1.1 内容生命周期闭环（topics / calendar / strategies / drafts /
content / playbook / scheduler / goals / personas 等多 router）打破。
本脚本保留仍然有效的不变量：

  S1 · server.main 可 import & FastAPI 实例 & 关键导出
  S2 · /api/v1/health 端点行为
  S3 · CORS 白名单（永远不允许 '*'，回显白名单 Origin）
  S4 · V1.1 lifecycle routers 全部注册（topics / calendar / strategies / drafts）
  S5 · server/main.py 红线（不 import streamlit / dashboard / playwright / subprocess /
        pandas / execjs / requests / sqlite3；不写通配 CORS）
  S6 · IdempotencyRoute 已挂在所有 lifecycle 写入端点上

运行：python -X utf8 verify_web_skeleton.py
"""

import re
import sys
from pathlib import Path

_results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    mark = "[+]" if cond else "[X]"
    s = "PASS" if cond else "FAIL"
    line = f"  {mark} {s}  {name}"
    if detail:
        line += f"  <- {detail}"
    print(line)
    _results.append((name, cond, detail))


def section(title):
    print(f"\n{'='*60}\n  {title}\n{'-'*60}")


def summary():
    total = len(_results)
    ok = sum(1 for _, c, _ in _results if c)
    print(f"\n{'='*60}\n  结果：{ok}/{total} 通过")
    if ok != total:
        print("  失败清单：")
        for n, c, d in _results:
            if not c:
                print(f"    [X] {n}" + (f": {d}" if d else ""))
    else:
        print("  全部通过")
    print('='*60)
    return ok == total


# ─────────────────────────────────────────────────────────────────────
# S1 · 模块 import & app 实例
# ─────────────────────────────────────────────────────────────────────

section("S1 · 模块导入 & FastAPI 实例")

try:
    from server import main as srv_main
    check("server.main 可 import", True)
except Exception as e:
    check("server.main 可 import", False, f"{type(e).__name__}: {e}")
    sys.exit(1)

from fastapi import FastAPI
check("app 是 FastAPI 实例", isinstance(srv_main.app, FastAPI))
check("API_VERSION 已定义",
      hasattr(srv_main, "API_VERSION")
      and isinstance(srv_main.API_VERSION, str))
check("CORS_ALLOWED_ORIGINS 已定义",
      hasattr(srv_main, "CORS_ALLOWED_ORIGINS")
      and isinstance(srv_main.CORS_ALLOWED_ORIGINS, list))


# ─────────────────────────────────────────────────────────────────────
# S2 · /health 端点行为
# ─────────────────────────────────────────────────────────────────────

section("S2 · /api/v1/health 端点")

from fastapi.testclient import TestClient
client = TestClient(srv_main.app)

resp = client.get("/api/v1/health")
check("HTTP 200", resp.status_code == 200, f"got={resp.status_code}")

try:
    body = resp.json()
    check("响应是 JSON", True)
except Exception as e:
    check("响应是 JSON", False, str(e))
    body = {}

check("body['status'] == 'ok'",
      body.get("status") == "ok", f"got={body.get('status')}")
check("body['version'] 是字符串",
      isinstance(body.get("version"), str)
      and len(body["version"]) > 0,
      f"got={body.get('version')}")
check("response 含 application/json content-type",
      "application/json" in resp.headers.get("content-type", ""))


# ─────────────────────────────────────────────────────────────────────
# S3 · CORS 中间件配置
# ─────────────────────────────────────────────────────────────────────

section("S3 · CORS 白名单")

# 3.1 白名单内：localhost:5173（Vite）应被允许
preflight = client.options(
    "/api/v1/health",
    headers={
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "content-type",
    },
)
check("白名单 localhost:5173 预检通过",
      preflight.status_code in (200, 204))
check("白名单回显 Origin",
      preflight.headers.get("access-control-allow-origin")
      == "http://localhost:5173",
      f"got={preflight.headers.get('access-control-allow-origin')}")

# 3.2 白名单内：localhost:3000（Next.js）应被允许
preflight2 = client.options(
    "/api/v1/health",
    headers={
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "GET",
    },
)
check("白名单 localhost:3000 预检通过",
      preflight2.headers.get("access-control-allow-origin")
      == "http://localhost:3000")

# 3.3 非白名单：evil.example.com 应被拒绝（不回显 Origin）
preflight_evil = client.options(
    "/api/v1/health",
    headers={
        "Origin": "https://evil.example.com",
        "Access-Control-Request-Method": "GET",
    },
)
ace = preflight_evil.headers.get("access-control-allow-origin", "")
check("非白名单 evil.example.com 不被允许",
      ace != "https://evil.example.com",
      f"got={ace!r}")

# 3.4 配置中不能含 "*"
check("CORS_ALLOWED_ORIGINS 不含通配符",
      "*" not in srv_main.CORS_ALLOWED_ORIGINS,
      str(srv_main.CORS_ALLOWED_ORIGINS))


# ─────────────────────────────────────────────────────────────────────
# S4 · V1.1 lifecycle routers 全部注册
# ─────────────────────────────────────────────────────────────────────

section("S4 · V1.1 lifecycle routers")

all_paths = {getattr(r, "path", "") for r in srv_main.app.routes}

# 每个 prefix 至少出现一次 (list 端点 "" 会暴露为 prefix 本体)
lifecycle_prefixes = {
    "/api/v1/topics":     ["/api/v1/topics", "/api/v1/topics/{topic_id}"],
    "/api/v1/calendar":   ["/api/v1/calendar", "/api/v1/calendar/{calendar_item_id}"],
    "/api/v1/strategies": ["/api/v1/strategies", "/api/v1/strategies/{strategy_id}"],
    "/api/v1/drafts":     ["/api/v1/drafts", "/api/v1/drafts/{content_id}"],
}
for prefix, expected_routes in lifecycle_prefixes.items():
    missing = [r for r in expected_routes if r not in all_paths]
    check(f"router {prefix} 已注册（{len(expected_routes)} 个核心路径）",
          not missing,
          f"missing={missing}" if missing else "")

# 关键 sub-routes
sub_routes = [
    "/api/v1/drafts/{content_id}/duplicate",
    "/api/v1/drafts/{content_id}/schedule",
    "/api/v1/drafts/{content_id}/reject",
    "/api/v1/content/generate",
    "/api/v1/intel/evidence/extract",
    "/api/v1/intel/evidence",
    "/api/v1/health",
]
for sub in sub_routes:
    check(f"sub-route {sub} 存在", sub in all_paths)

# 总路由数应明显 > P0 时代的 1 条（V1.1 后 ≥ 40 个独立路径）
api_routes = [p for p in all_paths if p.startswith("/api/")]
check("API 路由数量 ≥ 42（lifecycle + evidence + legacy 全注册）",
      len(api_routes) >= 42,
      f"count={len(api_routes)}")


# ─────────────────────────────────────────────────────────────────────
# S5 · 红线静态检查（grep server/main.py）
# ─────────────────────────────────────────────────────────────────────

section("S5 · 红线静态检查")

server_main_path = Path(__file__).parent / "server" / "main.py"
src_full = server_main_path.read_text(encoding="utf-8")

import ast as _ast


def _strip_docstring_and_comments(s: str) -> str:
    """删除模块 docstring 和井号注释，留下纯代码。"""
    tree = _ast.parse(s)
    if (tree.body and isinstance(tree.body[0], _ast.Expr)
            and isinstance(tree.body[0].value, _ast.Constant)
            and isinstance(tree.body[0].value.value, str)):
        first_real = tree.body[1] if len(tree.body) > 1 else None
        if first_real:
            lines = s.splitlines()
            code_only = "\n".join(lines[first_real.lineno - 1:])
        else:
            code_only = ""
    else:
        code_only = s
    cleaned_lines = []
    for line in code_only.splitlines():
        idx = line.find("#")
        if idx >= 0:
            line = line[:idx]
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


src = _strip_docstring_and_comments(src_full)

# 5.1 不在 main.py 里直接 import 阻塞/重型库（业务应在 router + run_in_threadpool）
blocking_imports = [
    "import subprocess", "from subprocess",
    "import requests", "from requests",
    "import sqlite3", "from sqlite3",
    "from playwright", "import playwright",
    "import pandas", "from pandas",
    "import execjs", "from execjs",
    "import streamlit", "from streamlit",
]
for bi in blocking_imports:
    pattern = re.compile(rf"^\s*{re.escape(bi)}", re.M)
    check(f"server/main.py 不 import 阻塞/重型库 '{bi}'",
          not pattern.search(src),
          f"found '{bi}' on bare import line" if pattern.search(src) else "")

# 5.2 不含 allow_origins=["*"]
check("CORS 不写通配符 allow_origins=['*']",
      not re.search(r'allow_origins\s*=\s*\[\s*"\*"\s*\]', src))

# 5.3 main.py 不直接 import dashboard（仅允许字符串/注释中提及）
check("server/main.py 不 import dashboard",
      not re.search(r'^\s*(import\s+dashboard|from\s+dashboard)', src, re.M))


# ─────────────────────────────────────────────────────────────────────
# S6 · IdempotencyRoute 已挂在 lifecycle 写入端点上
# ─────────────────────────────────────────────────────────────────────

section("S6 · Idempotency 中间件覆盖")

from server.middleware.idempotency import IdempotencyRoute

# V1.1 lifecycle 写入端点必须全部走 IdempotencyRoute
write_methods = {"POST", "PUT", "DELETE"}
covered_prefixes = {"/api/v1/topics", "/api/v1/calendar",
                    "/api/v1/strategies", "/api/v1/drafts",
                    "/api/v1/content", "/api/v1/packaging",
                    "/api/v1/intel", "/api/v1/orchestrator"}
# 已知 legacy 例外（pre-V1.1 路由，跟进项 F16 处理）
LEGACY_NO_IDEM_WHITELIST = {
    "/api/v1/topics/generate",  # 旧 topics.py POST /generate，AI 选题生成
}

for prefix in sorted(covered_prefixes):
    matching_writes = [
        r for r in srv_main.app.routes
        if getattr(r, "path", "").startswith(prefix)
        and write_methods.intersection(getattr(r, "methods", set()) or set())
    ]
    if not matching_writes:
        check(f"{prefix} 至少 1 个写入 route", False, "no writes found")
        continue
    non_idem = [
        r for r in matching_writes
        if not isinstance(r, IdempotencyRoute)
        and getattr(r, "path", "") not in LEGACY_NO_IDEM_WHITELIST
    ]
    check(f"{prefix} 所有写入端点用 IdempotencyRoute（{len(matching_writes)} 个写入）",
          not non_idem,
          f"non-idem: {[r.path for r in non_idem]}" if non_idem else "")

# server/__init__.py 应存在
init_path = Path(__file__).parent / "server" / "__init__.py"
check("server/__init__.py 存在", init_path.exists())


# ─────────────────────────────────────────────────────────────────────
# S7 · 横切数据维度守护
# ─────────────────────────────────────────────────────────────────────

section("S7 · 横切数据维度守护")

import re as _s7_re
import inspect as _s7_inspect

spec_path = Path(__file__).parent / "openspec/specs/data-dimensions/spec.md"
if spec_path.exists():
    spec_text = spec_path.read_text("utf-8")
    # 解析 ADDED Requirement 行，提取维度名（如 goal_id、persona_id）
    dims_raw = _s7_re.findall(
        r"^## (?:ADDED )?Requirement: (\w+)\s+", spec_text, _s7_re.M)
    check("S7 spec 可读取，维度清单已解析",
          len(dims_raw) > 0,
          f"dimensions={dims_raw}")
else:
    check("S7 spec 文件存在", False, str(spec_path))
    dims_raw = []

# S7.1 backend 方法签名守护
# 仅对已经在 spec 中立了且当前有方法映射的维度做检查
# SPEC_METHOD_MAP: dimension → [method_name, ...]
_S7_METHOD_MAP = {
    "goal_id": ["list_collected_data"],
}

from storage.base import StorageBackend as _S7_Proto

conformance_dims = [d for d in dims_raw if d in _S7_METHOD_MAP]
if conformance_dims:
    for dim in conformance_dims:
        for mname in _S7_METHOD_MAP[dim]:
            proto_method = getattr(_S7_Proto, mname, None)
            if proto_method is None:
                check(f"S7.1 {dim}: {mname} 在 Protocol 中定义", False)
                continue
            sig = _s7_inspect.signature(proto_method)
            has_param = dim in sig.parameters
            check(f"S7.1 {dim}: {mname} 签名包含 {dim} 参数",
                  has_param,
                  f"params={list(sig.parameters.keys())}" if not has_param else "")
else:
    check("S7.1 无 conformance 维度待检查", True, "none found")

# S7.2 API route 参数守护
def _is_depends_param(param):
    """Check if a parameter's default is a FastAPI Depends (function, not class)."""
    return type(param.default).__name__ == "Depends"

# 从 spec 提取需要暴露 goal_id 的 endpoint
_S7_ENDPOINT_DIM_MAP = {
    "/api/v1/notes": ["goal_id"],
}
for endpoint, expected_dims in _S7_ENDPOINT_DIM_MAP.items():
    route = None
    for r in srv_main.app.routes:
        if getattr(r, "path", "") == endpoint:
            route = r
            break
    if route is None:
        check(f"S7.2 {endpoint} 路由已注册", False)
        continue
    # 从 endpoint 函数签名检查 query 参数
    qparams = set()
    if hasattr(route, "endpoint"):
        try:
            ep_sig = _s7_inspect.signature(route.endpoint)
            for pname, p in ep_sig.parameters.items():
                # 排除 Depends 参数（如 auth），保留普通参数 = query 参数
                if not _is_depends_param(p):
                    qparams.add(pname)
        except Exception:
            pass
    for dim in expected_dims:
        has_qp = dim in qparams
        check(f"S7.2 {endpoint} 暴露 {dim} query 参数",
              has_qp,
              f"found qparams={qparams}" if not has_qp else "")


# ─────────────────────────────────────────────────────────────────────
ok = summary()
sys.exit(0 if ok else 1)
