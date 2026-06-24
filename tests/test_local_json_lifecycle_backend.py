from __future__ import annotations

import pandas as pd
import pytest

from storage.base import RevMismatch
from storage.local_json import LocalJsonBackend


def test_local_json_calendar_items_support_list_create_update_delete(tmp_path):
    backend = LocalJsonBackend(base_dir=str(tmp_path))

    empty = backend.list_calendar_items(
        "tenant-a",
        date_from="2026-05-27",
        date_to="2026-06-09",
        include_deleted=False,
        page_size=100,
    )
    assert empty == {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 100,
        "has_more": False,
    }

    item = backend.create_calendar_item(
        "tenant-a",
        scheduled_date="2026-06-01",
        scheduled_time="20:30",
        topic_id="topic_1",
        funnel_stage="trust",
    )
    assert item["rev"] == 1

    listed = backend.list_calendar_items("tenant-a", date_from="2026-05-27", date_to="2026-06-09")
    assert listed["total"] == 1
    assert listed["items"][0]["calendar_item_id"] == item["calendar_item_id"]

    updated = backend.update_calendar_item(
        "tenant-a",
        item["calendar_item_id"],
        expected_rev=1,
        scheduled_time="12:00",
    )
    assert updated["scheduled_time"] == "12:00"
    assert updated["rev"] == 2

    with pytest.raises(RevMismatch):
        backend.update_calendar_item(
            "tenant-a",
            item["calendar_item_id"],
            expected_rev=1,
            scheduled_time="08:00",
        )

    deleted = backend.delete_calendar_item(
        "tenant-a",
        item["calendar_item_id"],
        expected_rev=2,
        mode="soft",
    )
    assert deleted["status"] == "cancelled"
    assert backend.list_calendar_items("tenant-a")["total"] == 0
    assert backend.list_calendar_items("tenant-a", include_deleted=True)["total"] == 1


def test_local_json_topics_and_strategies_support_lifecycle_context(tmp_path):
    backend = LocalJsonBackend(base_dir=str(tmp_path))

    topic = backend.create_topic(
        "tenant-a",
        title="深圳工厂自助机点位怎么选",
        goal_id="goal_1",
        angle="工具型",
        funnel_stage="trust",
        source_refs=[{"type": "keyword", "id": "自助机"}],
    )
    assert backend.get_topic("tenant-a", topic["topic_id"])["title"] == topic["title"]
    assert backend.list_topics("tenant-a", goal_id="goal_1")["total"] == 1

    strategy = backend.create_strategy(
        "tenant-a",
        topic_id=topic["topic_id"],
        angle="工具型",
        hook="先看人流，再看停留",
        key_points=["人流", "停留"],
        cta="评论区说说你的场地",
    )
    assert backend.get_strategy("tenant-a", strategy["strategy_id"])["hook"] == "先看人流，再看停留"
    assert backend.list_strategies("tenant-a", topic_id=topic["topic_id"])["total"] == 1


def test_local_json_generated_posts_support_draft_lookup_and_occ_update(tmp_path):
    backend = LocalJsonBackend(base_dir=str(tmp_path))
    backend.save_generated_posts(
        "tenant-a",
        pd.DataFrame([
            {
                "content_id": "content_1",
                "goal_id": "goal_1",
                "title": "原标题",
                "body": "正文",
                "hashtags": ["自助机"],
                "publish_at": "2026-06-01 20:30",
                "status": "draft",
                "topic_id": "topic_1",
                "strategy_id": "str_1",
                "calendar_item_id": "cal_1",
                "knowledge_refs": [],
                "memory_refs": [],
            }
        ]),
    )

    draft = backend.get_generated_post("tenant-a", "content_1")
    assert draft is not None
    assert draft["rev"] == 1
    assert draft["hashtags"] == ["自助机"]

    updated = backend.update_generated_post(
        "tenant-a",
        "content_1",
        expected_rev=1,
        title="新标题",
        status="edited",
    )
    assert updated["title"] == "新标题"
    assert updated["rev"] == 2

    with pytest.raises(RevMismatch):
        backend.update_generated_post("tenant-a", "content_1", expected_rev=1, title="过期")
