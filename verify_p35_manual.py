"""P3.5.3 手工验收（后端段）· content-lifecycle-v2

走真实代码路径验证「performance CES → 三态判定 → goals.used_angles 写回」：
  1. seed 同一 angle ≥3 篇带 ces_score 的 generated post（满足 min_samples=3 阈值）
  2. 给 goal_001 注入对应 angle 的 unknown 条目（_update_playbook 只改已存在条目）
  3. 调真实 AnalystEvaluator._update_playbook（不经 LLM）
  4. 断言：高 CES angle → validated_hit，低 CES angle → sunk

阈值（agents/playbook_learning.py TRISTATE_THRESHOLDS）：
  validated_hit: 样本≥3 且平均 CES > 200
  sunk:          样本≥3 且平均 CES < 80

用法：
  python -X utf8 verify_p35_manual.py            # 自检：seed → 断言 → 自动还原
  python -X utf8 verify_p35_manual.py --seed      # 演示：seed 并保留，去前端看 /goals/goal_001
  python -X utf8 verify_p35_manual.py --cleanup   # 还原演示数据
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from agents.evaluators import AnalystEvaluator
import storage.factory

TENANT = "default"
GOAL_ID = "goal_001"
HIT_ANGLE = "反直觉型"
SUNK_ANGLE = "数字清单型"
BACKUP = Path(".p35_verify_backup.json")
SEED_PREFIX = "p35verify"  # generated_content_p35verify_*.xlsx 便于 cleanup


def _seed_posts(backend) -> None:
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for i in range(3):
        rows.append({"content_id": f"{SEED_PREFIX}_hit_{i}", "angle": HIT_ANGLE,
                     "ces_score": 300, "主标题": f"高互动测试{i}", "created_at": now})
        rows.append({"content_id": f"{SEED_PREFIX}_sunk_{i}", "angle": SUNK_ANGLE,
                     "ces_score": 30, "主标题": f"低互动测试{i}", "created_at": now})
    backend.save_generated_posts(TENANT, pd.DataFrame(rows), meta={"goal_id": SEED_PREFIX})

    # 自检 seed 是否可被学习逻辑读到（angle + ces_score 必须保留）
    df = backend.list_generated_posts(TENANT)
    seeded = df[df["content_id"].astype(str).str.startswith(SEED_PREFIX)] if "content_id" in df.columns else df
    assert not seeded.empty, "seed 的 generated post 读不回来"
    assert "angle" in seeded.columns and "ces_score" in seeded.columns, \
        f"seed 行缺 angle/ces_score 列，实际列={list(seeded.columns)}"


def _inject_goal_angles(backend) -> None:
    data = backend.load_goals(TENANT)
    if not BACKUP.exists():
        for g in data.get("goals", []):
            if g.get("id") == GOAL_ID:
                BACKUP.write_text(json.dumps(g.get("used_angles", []), ensure_ascii=False),
                                  encoding="utf-8")
    for g in data.get("goals", []):
        if g.get("id") == GOAL_ID:
            g["used_angles"] = [
                {"angle": HIT_ANGLE, "status": "unknown", "evidence_count": 3, "last_ces": 300},
                {"angle": SUNK_ANGLE, "status": "unknown", "evidence_count": 3, "last_ces": 30},
            ]
    backend.save_goals(TENANT, data)


def _current_goal_angles(backend) -> dict:
    data = backend.load_goals(TENANT)
    for g in data.get("goals", []):
        if g.get("id") == GOAL_ID:
            return {e["angle"]: e["status"] for e in (g.get("used_angles") or [])}
    return {}


def cleanup(backend) -> None:
    # 1. 还原 goal_001.used_angles
    if BACKUP.exists():
        original = json.loads(BACKUP.read_text(encoding="utf-8"))
        data = backend.load_goals(TENANT)
        for g in data.get("goals", []):
            if g.get("id") == GOAL_ID:
                g["used_angles"] = original
        backend.save_goals(TENANT, data)
        BACKUP.unlink()
    # 2. 删 seed 的 generated xlsx
    root = Path("xhs_data") / TENANT
    for f in root.glob(f"generated_content_{SEED_PREFIX}_*.xlsx"):
        f.unlink()
    # 3. 删 sidecar 中的 seed 条目
    sc = root / "generated_posts.json"
    if sc.exists():
        d = json.loads(sc.read_text(encoding="utf-8"))
        d = {k: v for k, v in d.items() if not str(k).startswith(SEED_PREFIX)}
        sc.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[cleanup] 已还原 goal_001 + 删除 seed 数据")


def run(keep: bool) -> int:
    backend = storage.factory.get_backend()
    _seed_posts(backend)
    _inject_goal_angles(backend)

    ev = AnalystEvaluator(tenant_id=TENANT)
    result = ev._update_playbook(TENANT)
    print(f"[_update_playbook] {json.dumps(result, ensure_ascii=False)}")

    angles = _current_goal_angles(backend)
    print(f"[goal_001 三态] {json.dumps(angles, ensure_ascii=False)}")

    ok = angles.get(HIT_ANGLE) == "validated_hit" and angles.get(SUNK_ANGLE) == "sunk"
    if ok:
        print(f"[PASS] {HIT_ANGLE}→validated_hit, {SUNK_ANGLE}→sunk")
    else:
        print(f"[FAIL] 期望 {HIT_ANGLE}=validated_hit / {SUNK_ANGLE}=sunk，实际 {angles}")

    if keep:
        print("\n演示数据已保留。前端验收：")
        print("  1. 后端  python -m uvicorn server.main:app --reload --port 8000")
        print("  2. 前端  cd frontend && npm run dev  → http://localhost:3000")
        print(f"  3. 打开  /goals/{GOAL_ID} → 看「角度表现」卡片：")
        print(f"     {HIT_ANGLE} = ✅ 已验证爆款 / {SUNK_ANGLE} = ❌ 沉底")
        print("  4. 看完执行  python -X utf8 verify_p35_manual.py --cleanup")
    else:
        cleanup(backend)
    return 0 if ok else 1


if __name__ == "__main__":
    backend = storage.factory.get_backend()
    if "--cleanup" in sys.argv:
        cleanup(backend)
        sys.exit(0)
    sys.exit(run(keep="--seed" in sys.argv))
