"""回归：_goal_methodology 必须带上目标基础身份信息（名称/目标/受众/定位/关键词），
而不仅是 overall_strategy。

根因：稀疏配置的目标（如刚建好、只填了名字的「获取快速审计出表客户」）原先只取
overall_strategy + used_angles，于是 methodology 为空 → 主 Agent 系统提示无目标上下文 →
回答时反问"你是哪个行业的"。见 agents/orchestrator.py::_goal_methodology。
"""
from agents.orchestrator import _goal_methodology


def test_sparse_goal_includes_name():
    goal = {"id": "g1", "name": "获取快速审计出表客户",
            "objective": "", "description": "", "target_audience": {},
            "brand_position": "", "keywords": []}
    out = _goal_methodology(goal)
    assert out.strip() != ""
    assert "获取快速审计出表客户" in out


def test_basic_fields_included():
    goal = {
        "name": "审计获客", "objective": "拿下中小企业审计客户",
        "description": "面向需要快速出审计报告的中小企业",
        "target_audience": {"who": "中小企业财务负责人"},
        "brand_position": "8年审计经验，快速出表",
        "keywords": ["审计出表", "中小企业审计"],
    }
    out = _goal_methodology(goal)
    assert "拿下中小企业审计客户" in out
    assert "中小企业财务负责人" in out
    assert "8年审计经验" in out
    assert "审计出表" in out


def test_advanced_strategy_still_included():
    goal = {
        "name": "x",
        "overall_strategy": {"core_message": "用闲置场地换收入",
                             "content_funnel": {"top": "引流"}},
        "used_angles": [{"angle": "反直觉型", "status": "validated_hit"},
                        {"angle": "纯广告", "status": "sunk"}],
    }
    out = _goal_methodology(goal)
    assert "用闲置场地换收入" in out
    assert "反直觉型" in out
    assert "纯广告" in out


def test_audience_as_string():
    out = _goal_methodology({"name": "g", "target_audience": "深圳工厂老板"})
    assert "深圳工厂老板" in out


def test_empty_goal_returns_empty():
    assert _goal_methodology({}) == ""
