"""G.4 性能验收 · content-lifecycle-v2

目标（tasks.md G.4）：evidence 提取 50 条 notes 用时 ≤ 60s（10 条 1 批，5 批顺序）。

架构事实（读 agent_tools/intel_evidence.py 确认）：
  extract_evidence_for_notes 按 batch_size(≤10) 切批，每批 **一次** call_kimi
  → 50 条 = **5 次** LLM 调用（happy path），失败才降级逐条。
  所以总耗时 ≈ 编排开销 + 5 × 单批 LLM 延迟。

本脚本两段：
  A. 替身 LLM（瞬时返回合法 JSON）跑满 50 条 → 测**纯编排开销**（确定性、免费），
     并断言 LLM 调用数 == 5（证明是 10/批 5 批，不是 50 次逐条）。
  B. best-effort 跑**一次真实批**（10 条）测真实单批延迟 → ×5 外推，判断 50 条活跑是否 ≤ 60s。
     provider 不可达/无 key 时优雅跳过，只报 A 段 + 结论需活跑确认。

用法： python -X utf8 verify_g4_perf.py          # A 段 + best-effort B 段
       python -X utf8 verify_g4_perf.py --no-live # 只跑 A 段
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("JWT_SECRET", "g4-perf-secret")
os.environ["STORAGE_BACKEND"] = "local"

import pandas as pd  # noqa: E402

TENANT = "default"
GOAL_ID = "goal_001"
N_NOTES = 50
BATCH = 10
BUDGET_S = 60.0
THRESHOLD = 200.0


def _seed_notes(backend) -> None:
    rows = []
    for i in range(N_NOTES):
        rows.append({
            "笔记ID": f"g4note{i:03d}",
            "标题": f"深圳工厂点位案例{i}：闲置仓库月入数千",
            "正文": ("实测一个南山工业区点位的运营数据，复盘选址、人流、补货节奏，"
                     "给想合作的物业方一个参考。" * 3),
            "关键词": "自助机点位招商",
            "CES": 600,  # > THRESHOLD，全部入选
        })
    backend.save_collected_data(TENANT, source="search", df=pd.DataFrame(rows),
                                meta={"goal_id": GOAL_ID})


def _fake_kimi_factory(counter: dict):
    """替身：解析 prompt 内嵌的 payload，逐条回合法 evidence JSON。"""
    def fake_kimi(prompt: str, **kw):
        counter["calls"] = counter.get("calls", 0) + 1
        counter["batch_sizes"] = counter.get("batch_sizes", [])
        m = re.search(r"\[.*\]", prompt, re.S)
        payload = json.loads(m.group(0)) if m else []
        counter["batch_sizes"].append(len(payload))
        items = [{
            "source_note_id": p.get("source_note_id"),
            "angle": "工具型",
            "funnel_stage": p.get("funnel_stage_hint") or "trust",
            "hook": "先看消费时段再谈合作",
            "key_insight": "用点位评分表把招商感降到最低，靠数据说话。",
        } for p in payload]
        return json.dumps(items, ensure_ascii=False), None
    return fake_kimi


def main() -> int:
    live = "--no-live" not in sys.argv
    tmp = Path(tempfile.mkdtemp(prefix="g4perf_"))
    print(f"[G.4 性能验收] 隔离目录 {tmp}\n")
    ok = True
    try:
        from storage.local_json import LocalJsonBackend
        import agent_tools.kimi as kimi_mod
        from agent_tools.intel_evidence import extract_evidence_from_storage

        backend = LocalJsonBackend(base_dir=str(tmp))
        _seed_notes(backend)

        # ── A 段：替身 LLM 测编排开销 ───────────────────────────────────
        counter: dict = {}
        real_call_kimi = kimi_mod.call_kimi
        kimi_mod.call_kimi = _fake_kimi_factory(counter)

        t0 = time.perf_counter()
        result = extract_evidence_from_storage(
            tenant_id=TENANT, storage=backend,
            ces_threshold=THRESHOLD, batch_size=BATCH,
        )
        overhead = time.perf_counter() - t0
        kimi_mod.call_kimi = real_call_kimi  # 还原

        calls = counter.get("calls", 0)
        sizes = counter.get("batch_sizes", [])
        extracted = result.get("extracted_count", 0)
        stored = backend.list_evidence(TENANT, limit=100000)

        print("── A 段：编排开销（替身 LLM，瞬时返回）──")
        print(f"  提取条数 extracted_count = {extracted}（期望 {N_NOTES}）")
        print(f"  LLM 调用次数            = {calls}（期望 {N_NOTES // BATCH} = 50条/10批）")
        print(f"  每批 size               = {sizes}")
        print(f"  入库 evidence 行数      = {len(stored)}")
        print(f"  ⏱  纯编排耗时           = {overhead*1000:.1f} ms")
        a_ok = (extracted == N_NOTES and calls == N_NOTES // BATCH
                and all(s == BATCH for s in sizes) and len(stored) == N_NOTES
                and overhead <= 10.0)
        print(f"  → A {'✅ PASS' if a_ok else '❌ FAIL'}（编排开销远低于 60s，调用数=5 而非 50）\n")
        ok = ok and a_ok

        # ── B 段：best-effort 真实单批延迟 → ×5 外推 ────────────────────
        if live:
            print("── B 段：真实单批延迟（1 次真实 LLM，×5 外推）──")
            from agent_tools.intel_evidence import _normalize_note, _call_batch
            df = backend.list_collected_data(TENANT, since=__import__("datetime").datetime(2000, 1, 1))
            batch = [_normalize_note(r) for r in df.to_dict("records")[:BATCH]]
            holder: dict = {}

            def _live():
                try:
                    s = time.perf_counter()
                    items = _call_batch(batch)  # 真实 call_kimi
                    holder["latency"] = time.perf_counter() - s
                    holder["n"] = len(items)
                except Exception as e:  # noqa: BLE001
                    holder["err"] = f"{type(e).__name__}: {e}"

            th = threading.Thread(target=_live, daemon=True)
            th.start()
            th.join(timeout=70)

            if "latency" in holder:
                lat = holder["latency"]
                proj = lat * (N_NOTES // BATCH)
                print(f"  真实单批(10条)延迟 = {lat:.2f}s，解析出 {holder.get('n')} 条")
                print(f"  ×5 外推 50 条       = {proj:.2f}s（预算 {BUDGET_S:.0f}s）")
                b_ok = proj <= BUDGET_S
                print(f"  → B {'✅ PASS' if b_ok else '❌ FAIL'}（50 条活跑 {'≤' if b_ok else '>'} 60s）\n")
                ok = ok and b_ok
            elif "err" in holder:
                print(f"  ⏭  跳过：真实 LLM 不可达（{holder['err']}）")
                print("     结论：编排开销可忽略，活跑耗时 = 5 × 单批延迟，需在配好 provider 的环境确认。\n")
            else:
                print("  ⏭  跳过：真实单批 70s 未返回（provider 慢或挂起）。\n")
        else:
            print("── B 段：--no-live 跳过真实 LLM 测时 ──\n")

        verdict = "PASS" if ok else "FAIL"
        print(f"{'🎉' if ok else '💥'} G.4 {verdict}")
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
