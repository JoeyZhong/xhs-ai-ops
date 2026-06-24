"""P3.3 · AnalystEvaluator playbook 学习闭环 —— 纯逻辑单测。

design.md §5（三态判定）+ §6（playbook 防污染注释块）。

- classify_angles(posts, thresholds): 按 angle group 最近窗口，算平均 CES → 三态 + top/bottom
- render_auto_block(verdicts): 渲染成 markdown
- merge_playbook(existing, auto_block): 只替换 <!-- analyst-auto: v2 --> 块，保留手写区
"""
from __future__ import annotations

from agents.playbook_learning import (
    classify_angles,
    render_auto_block,
    merge_playbook,
    AUTO_BEGIN,
    AUTO_END,
    TRISTATE_THRESHOLDS,
)


def _post(angle, ces):
    return {"angle": angle, "ces_score": ces}


class TestClassifyAngles:
    def test_validated_hit(self):
        # 反直觉型: 3 篇平均 CES = (300+250+200)/3 = 250 > 200 → validated_hit
        posts = [_post("反直觉型", 300), _post("反直觉型", 250), _post("反直觉型", 200)]
        v = classify_angles(posts)
        assert v["反直觉型"]["status"] == "validated_hit"
        assert v["反直觉型"]["avg_ces"] == 250

    def test_sunk(self):
        # 工具型: 3 篇平均 CES = (60+50+40)/3 = 50 < 80 → sunk
        posts = [_post("工具型", 60), _post("工具型", 50), _post("工具型", 40)]
        v = classify_angles(posts)
        assert v["工具型"]["status"] == "sunk"

    def test_unknown_when_in_between(self):
        # 平均 CES 在 80~200 之间 → unknown
        posts = [_post("数字清单型", 150), _post("数字清单型", 120), _post("数字清单型", 130)]
        v = classify_angles(posts)
        assert v["数字清单型"]["status"] == "unknown"

    def test_below_min_samples_excluded(self):
        # 只有 2 篇 < min_samples(3) → 不判定，不出现在结果
        posts = [_post("本地汇总型", 300), _post("本地汇总型", 300)]
        v = classify_angles(posts)
        assert "本地汇总型" not in v

    def test_blank_angle_ignored(self):
        posts = [_post("", 300), _post(None, 300), _post("  ", 300)]
        assert classify_angles(posts) == {}

    def test_missing_ces_treated_zero(self):
        posts = [{"angle": "工具型"}, {"angle": "工具型"}, {"angle": "工具型"}]
        v = classify_angles(posts)
        assert v["工具型"]["status"] == "sunk"  # avg 0 < 80

    def test_thresholds_overridable(self):
        posts = [_post("X", 100), _post("X", 100), _post("X", 100)]
        v = classify_angles(posts, thresholds={"validated_hit_ces": 90, "sunk_ces": 50,
                                               "min_samples": 3})
        assert v["X"]["status"] == "validated_hit"


class TestRenderAutoBlock:
    def test_contains_status_and_angle(self):
        verdicts = {
            "反直觉型": {"status": "validated_hit", "avg_ces": 250, "count": 3},
            "工具型": {"status": "sunk", "avg_ces": 50, "count": 3},
        }
        md = render_auto_block(verdicts)
        assert "反直觉型" in md
        assert "工具型" in md
        assert "250" in md

    def test_empty_verdicts(self):
        md = render_auto_block({})
        assert isinstance(md, str)


class TestMergePlaybook:
    def test_insert_when_no_auto_block(self):
        existing = "# 手写区\n运营人手写的内容。"
        merged = merge_playbook(existing, "自动内容")
        assert "运营人手写的内容。" in merged  # 手写区保留
        assert AUTO_BEGIN in merged and AUTO_END in merged
        assert "自动内容" in merged

    def test_replace_existing_auto_block(self):
        existing = (
            "# 手写区\n保留我。\n\n"
            f"{AUTO_BEGIN}\n旧的自动内容\n{AUTO_END}\n"
        )
        merged = merge_playbook(existing, "新的自动内容")
        assert "保留我。" in merged
        assert "新的自动内容" in merged
        assert "旧的自动内容" not in merged
        # 只有一个 auto block
        assert merged.count(AUTO_BEGIN) == 1

    def test_empty_existing(self):
        merged = merge_playbook("", "自动内容")
        assert "自动内容" in merged
        assert AUTO_BEGIN in merged


def test_thresholds_contract():
    assert TRISTATE_THRESHOLDS["validated_hit_ces"] == 200
    assert TRISTATE_THRESHOLDS["sunk_ces"] == 80
    assert TRISTATE_THRESHOLDS["min_samples"] == 3
    assert TRISTATE_THRESHOLDS["window_days"] == 30
