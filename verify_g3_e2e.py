"""G.3 端到端闭环验收 · content-lifecycle-v2

把三个 phase 串成一条真实闭环，全程走真实 server.main:app 的 HTTP 路由
（TestClient = 浏览器同款路由，少一层 socket），在**完全隔离的 tmp 后端**上跑，
零碰真实数据。只对两个外部/烧钱的 hop 做替身：
  - XHS 采集（限频、要 cookie）        → 直接 seed 一条 collected/evidence 代替"采集+提取"产出
  - LLM call_kimi（烧 token、不确定）  → monkeypatch，捕获拼好的 prompt + 回 canned JSON

验证链（PRD 闭环）：
  1. 改 packaging 公式（真实 PUT /api/v1/packaging/rules）
  2. evidence 入池（seed，代表 采集→提取 的产出，funnel=trust）
  3. 内容创作生成（真实 POST /content/strategy + /content/generate）
     → 断言拼好的 prompt **含** packaging 新公式 + evidence；此时 **无** playbook
  4. 录入发布数据（真实 POST /api/v1/analytics/performance）→ CES 写回 + used_angles
  5. 触发 evaluator（真实 AnalystEvaluator._update_playbook）
     → 断言 playbook.md 写入自动区 + used_angles 翻三态
  6. 闭环回灌：再跑一次 /content/strategy
     → 断言 prompt **同时含** packaging + evidence + 新 playbook（证明 evaluator 产出回到了下一轮 prompt）

用法： python -X utf8 verify_g3_e2e.py
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path

os.environ.setdefault("JWT_SECRET", "g3-e2e-secret")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ["STORAGE_BACKEND"] = "local"

import pandas as pd  # noqa: E402

# ── 哨兵串（独一无二，便于在 prompt 里精确定位）──────────────────────────
PKG_SENTINEL = "G3PKG_反爬独家标题公式_zzz9"
EV_HOOK = "G3EV_先看消费时段再谈合作"
EV_INSIGHT = "G3EV_用点位评分表降低招商感"
HIT_ANGLE = "反直觉型"
SUNK_ANGLE = "数字清单型"
TENANT = "default"
GOAL_ID = "goal_001"

_n = 0


def step(msg: str, ok: bool, evidence: str = "") -> None:
    global _n
    _n += 1
    mark = "✅" if ok else "❌"
    print(f"{mark} step {_n}: {msg}")
    if evidence:
        for line in evidence.splitlines():
            print(f"        {line}")
    if not ok:
        raise AssertionError(f"step {_n} FAILED: {msg}")


def run(tmp: Path) -> None:
    # ── 装隔离环境（patch 必须在第一个请求前生效）─────────────────────────
    from storage.local_json import LocalJsonBackend
    import storage.factory
    import agents.master as master_mod
    import agent_tools.kimi as kimi_mod
    import agent_tools.packaging_rules as pkg_mod
    from server.routers import content as content_router

    backend = LocalJsonBackend(base_dir=str(tmp))
    storage.factory.get_backend = lambda *a, **k: backend          # content/analytics 路由
    master_mod.get_backend = lambda *a, **k: backend               # HermesMaster._storage（evaluator）

    cfgdir = tmp / "cfg"
    cfgdir.mkdir(parents=True, exist_ok=True)
    (cfgdir / "settings.json").write_text('{"llm_provider":"kimi"}', encoding="utf-8")
    content_router.CONFIG_DIR = cfgdir                             # 绕开 mock 短路

    pkg_path = tmp / "packaging_rules.md"
    pkg_path.write_text("# 包装基线\n五大爆文标题公式：旧公式\nCES 权重：评论×4\n", encoding="utf-8")
    pkg_mod._RULES_PATH = pkg_path                                 # packaging PUT + loader 都指向 tmp
    pkg_mod.load_packaging_rules.cache_clear()

    captured: dict[str, str] = {}

    def fake_kimi(prompt: str, **kw):
        captured["prompt"] = prompt
        if kw.get("json_mode"):  # /strategy 要 dict
            return json.dumps({"angle": HIT_ANGLE, "hook": "h", "key_points": ["k"], "cta": "你怎么看？"},
                              ensure_ascii=False), None
        # /generate 要数组
        return json.dumps([{"title": "测试标题", "body": "正文" * 30, "hashtags": ["点位"],
                            "publish_at": "12:00", "angle": HIT_ANGLE}], ensure_ascii=False), None

    kimi_mod.call_kimi = fake_kimi

    # ── seed goal（含 funnel）+ topic（funnel=trust，让 evidence 进 prompt）──
    backend.save_goals(TENANT, {"active_goal_id": GOAL_ID, "goals": [{
        "id": GOAL_ID, "name": "B端点位招商",
        "brand_position": "深圳本土自助售卖机运营商",
        "target_audience": {"who": "工厂老板", "pain_points": "闲置场地无收益"},
        "keywords": ["自助机点位招商"],
        "used_angles": [],
        "overall_strategy": {
            "core_message": "用闲置场地换被动收入",
            "content_funnel": {"top_30pct": "借餐饮选址引流", "mid_40pct": "行业干货建信任",
                               "bottom_30pct": "本地化招商触达"},
        },
    }]})
    topic = backend.create_topic(TENANT, title="点位评分表", goal_id=GOAL_ID,
                                 angle="工具型", funnel_stage="trust", source="manual")
    topic_id = topic["topic_id"]

    from server.main import app
    from fastapi.testclient import TestClient
    from security.jwt import encode_token
    from agents.used_angles import normalize_used_angles

    client = TestClient(app)
    tok = encode_token(TENANT)

    def H():  # 每次新 Idempotency-Key
        return {"Authorization": f"Bearer {tok}", "Idempotency-Key": uuid.uuid4().hex}

    auth_only = {"Authorization": f"Bearer {tok}"}

    # ── STEP 1：改 packaging 公式（真实 PUT）─────────────────────────────
    new_rules = (f"# 包装规则\n五大爆文标题公式：{PKG_SENTINEL}（反直觉/数字清单/本地汇总/工具/焦虑）\n"
                 "CES 权重：点赞1 收藏1 评论4 分享4 关注8\n")
    r = client.put("/api/v1/packaging/rules", headers=H(), json={"rules": new_rules})
    g = client.get("/api/v1/packaging/rules", headers=auth_only)
    step("PUT packaging/rules 改公式 → GET 读回含新哨兵",
         r.status_code == 200 and PKG_SENTINEL in g.json().get("rules", ""),
         f"PUT={r.status_code}  GET.rules含哨兵={PKG_SENTINEL in g.json().get('rules','')}")

    # ── STEP 2：evidence 入池（代表 采集→提取 的产出）───────────────────
    backend.upsert_evidence(TENANT, {
        "source_note_id": "g3-n1", "angle": "工具型", "funnel_stage": "trust",
        "hook": EV_HOOK, "key_insight": EV_INSIGHT, "ces_score": 620,
        "raw": {"title": "点位评分表"}})
    evs = backend.list_evidence(TENANT, funnel_stage="trust", limit=3)
    step("seed evidence（funnel=trust）入池（替身：跳过 XHS 限频采集 + LLM 抽取，仅此 hop）",
         any(e.get("hook") == EV_HOOK for e in evs),
         f"list_evidence(trust) 命中哨兵 hook={any(e.get('hook')==EV_HOOK for e in evs)}")

    # ── STEP 3：内容创作生成 → 捕获 prompt（应含 packaging+evidence，无 playbook）──
    r = client.post("/api/v1/content/strategy", headers=H(), json={
        "goal_id": GOAL_ID, "keywords": ["自助机点位招商"],
        "user_intent": "找深圳工厂点位", "topic_id": topic_id})
    p1 = captured.get("prompt", "")
    cond = (r.status_code == 200 and PKG_SENTINEL in p1 and EV_HOOK in p1
            and "已验证爆款规律（playbook）" not in p1)
    step("POST content/strategy → 拼好的 prompt 含[新 packaging 公式]+[evidence]，且[playbook]尚空",
         cond,
         f"status={r.status_code}\npackaging哨兵∈prompt={PKG_SENTINEL in p1}\n"
         f"evidence hook∈prompt={EV_HOOK in p1}\nplaybook段缺席={'已验证爆款规律（playbook）' not in p1}")

    # 生成并落库一篇（拿 content_id 给 STEP 4 录数据）
    r = client.post("/api/v1/content/generate", headers=H(), json={
        "goal_id": GOAL_ID, "topic": "点位评分表", "strategy": {"angle": HIT_ANGLE},
        "count": 1, "persist": True, "topic_id": topic_id})
    item = r.json()["items"][0]
    content_id = item["content_id"]
    step("POST content/generate(persist) → 落库 1 篇，拿到 content_id",
         r.status_code == 200 and bool(content_id) and item.get("angle") == HIT_ANGLE,
         f"status={r.status_code}  content_id={content_id}  angle={item.get('angle')}")

    # ── STEP 4：录入发布数据（真实端点）→ CES + used_angles ───────────────
    r = client.post("/api/v1/analytics/performance", headers=H(), json={
        "content_id": content_id, "likes": 100, "collects": 50,
        "comments_count": 40, "shares": 20, "follows": 10})
    body = r.json()
    expect_ces = 100 + 50 + 40 * 4 + 20 * 4 + 10 * 8  # =470
    goals = backend.load_goals(TENANT)
    ua = {e["angle"]: e for g in goals["goals"]
          for e in normalize_used_angles(g.get("used_angles", []))}
    step("POST analytics/performance → CES 写回 + used_angles[反直觉型].last_ces 更新",
         r.status_code == 200 and body.get("ces_score") == expect_ces
         and ua.get(HIT_ANGLE, {}).get("last_ces") == expect_ces,
         f"status={r.status_code}  ces={body.get('ces_score')}(期望{expect_ces})  "
         f"used_angles[{HIT_ANGLE}].last_ces={ua.get(HIT_ANGLE,{}).get('last_ces')}")

    # ── STEP 5：seed 满足样本阈值的数据 + 注入 sunk 角度 → 跑真实 evaluator ──
    rows = []
    for i in range(3):  # 高 CES → validated_hit（avg>200, n>=3）
        rows.append({"content_id": f"g3hit{i}", "angle": HIT_ANGLE, "ces_score": 300,
                     "主标题": f"高{i}", "created_at": pd.Timestamp.utcnow().isoformat()})
    for i in range(3):  # 低 CES → sunk（avg<80, n>=3）
        rows.append({"content_id": f"g3sunk{i}", "angle": SUNK_ANGLE, "ces_score": 30,
                     "主标题": f"低{i}", "created_at": pd.Timestamp.utcnow().isoformat()})
    backend.save_generated_posts(TENANT, pd.DataFrame(rows), meta={"goal_id": GOAL_ID})
    # used_angles 需含两个角度（evaluator 只改已存在条目）；HIT 已由 STEP4 加，补 SUNK
    data = backend.load_goals(TENANT)
    for goal in data["goals"]:
        if goal["id"] == GOAL_ID:
            ua_list = normalize_used_angles(goal.get("used_angles", []))
            if not any(e["angle"] == SUNK_ANGLE for e in ua_list):
                ua_list.append({"angle": SUNK_ANGLE, "status": "unknown",
                                "evidence_count": 3, "last_ces": 30})
            goal["used_angles"] = ua_list
    backend.save_goals(TENANT, data)

    from agents.evaluators import AnalystEvaluator
    ev = AnalystEvaluator(tenant_id=TENANT)  # 构造在 patch 之后 → _master._storage = tmp backend
    res = ev._update_playbook(TENANT)

    pb = backend.load_memory(TENANT, "content", "playbook.md") or ""
    pb_bak = backend.load_memory(TENANT, "content", "playbook.md.bak")
    goals2 = backend.load_goals(TENANT)
    status = {e["angle"]: e["status"] for g in goals2["goals"]
              for e in normalize_used_angles(g.get("used_angles", []))}
    step("AnalystEvaluator._update_playbook → playbook 写自动区 + used_angles 翻三态 + 改前备份",
         res.get("updated") is True and HIT_ANGLE in pb
         and "analyst-auto" in pb and pb_bak is not None
         and status.get(HIT_ANGLE) == "validated_hit" and status.get(SUNK_ANGLE) == "sunk",
         f"updated={res.get('updated')}\nplaybook含[{HIT_ANGLE}]={HIT_ANGLE in pb} 含auto区={'analyst-auto' in pb}\n"
         f".bak已建={pb_bak is not None}\n三态: {HIT_ANGLE}={status.get(HIT_ANGLE)} / {SUNK_ANGLE}={status.get(SUNK_ANGLE)}")

    # ── STEP 6：闭环回灌 → 再跑 strategy，prompt 三料齐全 ─────────────────
    captured.clear()
    r = client.post("/api/v1/content/strategy", headers=H(), json={
        "goal_id": GOAL_ID, "keywords": ["自助机点位招商"],
        "user_intent": "找深圳工厂点位", "topic_id": topic_id})
    p2 = captured.get("prompt", "")
    cond6 = (r.status_code == 200 and PKG_SENTINEL in p2 and EV_HOOK in p2
             and "已验证爆款规律（playbook）" in p2 and HIT_ANGLE in p2)
    step("闭环回灌：再 POST content/strategy → prompt 同时含[packaging]+[evidence]+[新 playbook]",
         cond6,
         f"status={r.status_code}\npackaging∈prompt={PKG_SENTINEL in p2}  evidence∈prompt={EV_HOOK in p2}\n"
         f"playbook段∈prompt={'已验证爆款规律（playbook）' in p2}  含已验证角度{HIT_ANGLE}={HIT_ANGLE in p2}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="g3e2e_"))
    print(f"[G.3 端到端闭环验收] 隔离目录 {tmp}\n")
    try:
        run(tmp)
        print("\n🎉 G.3 PASS — 闭环六步全通：packaging→evidence→prompt→录数据→evaluator→回灌")
        return 0
    except AssertionError as e:
        print(f"\n💥 G.3 FAIL — {e}")
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
