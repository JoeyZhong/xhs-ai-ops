"""回归：derive_benchmarks_md 对中文列名采集数据不再 KeyError: 'title'。

根因：采集产出为中文列名（标题/点赞数/…），而 derive_benchmarks_md 期望英文列
（title/likes/…）。旧代码对 note_id/数值列有 `in df.columns` 防护，唯独 df["title"]
裸取，于是非空但中文 schema 的采集结果会 KeyError，并在 build_system_prompt 阶段
连带拖垮 content/intel/analyst 三类子 agent（每次 submit 立即 ok=false: 'title'）。
"""
import pandas as pd

from agents import context


class _FakeBackend:
    def __init__(self, df):
        self._df = df

    def list_collected_data(self, tenant_id, since=None):
        return self._df


def _cn_df():
    return pd.DataFrame([
        {"标题": "30万拿两个学校内店铺", "点赞数": 1000, "收藏数": 500,
         "评论数": 20, "笔记ID": "n1", "搜索关键词": "学校便利店"},
        {"标题": "校内便利店能做吗", "点赞数": 300, "收藏数": 200,
         "评论数": 10, "笔记ID": "n2", "搜索关键词": "学校便利店"},
    ])


def test_chinese_columns_no_crash_and_produces_benchmarks(monkeypatch):
    monkeypatch.setattr(context, "get_backend", lambda: _FakeBackend(_cn_df()))
    out = context.derive_benchmarks_md("default")
    assert out is not None
    assert "爆款标题参考" in out
    # 互动 = 点赞+收藏+评论 → 第一条 1520 应排在前
    assert "30万拿两个学校内店铺" in out


def test_missing_title_column_returns_none(monkeypatch):
    df = pd.DataFrame([{"foo": 1, "bar": 2}])
    monkeypatch.setattr(context, "get_backend", lambda: _FakeBackend(df))
    assert context.derive_benchmarks_md("default") is None


def test_english_columns_still_work(monkeypatch):
    df = pd.DataFrame([
        {"title": "english title", "likes": 100, "collects": 50,
         "comments_count": 5, "note_id": "n1", "keyword": "kw"},
    ])
    monkeypatch.setattr(context, "get_backend", lambda: _FakeBackend(df))
    out = context.derive_benchmarks_md("default")
    assert out is not None
    assert "english title" in out
