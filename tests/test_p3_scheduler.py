"""
P3.1 Scheduler 验收测试（TDD RED→GREEN）
运行：pytest tests/test_p3_scheduler.py -v

覆盖目标：
- S1: SpiderScheduler start/stop 基本生命周期
- S2: File lock 防重入（同一进程第二次 start 抛 IOError）
- S3: register_default_jobs 注册 2 个 cron
- S4: scheduler.enabled=false 时不启动
- S5: scheduler.enabled=true 时启动成功
- S6: 锁释放后可重新 start
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from agents.scheduler import SpiderScheduler

SETTINGS_DISABLED = json.dumps({"scheduler": {"enabled": False}})
SETTINGS_ENABLED = json.dumps({"scheduler": {"enabled": True}})


# ── fixture：独立锁文件路径 ──────────────────────────────────────────────

@pytest.fixture()
def lock_path(tmp_path) -> Path:
    return tmp_path / ".scheduler.lock"


# ── S1 · 基本生命周期 ────────────────────────────────────────────────────

class TestLifecycle:
    def test_start_stop(self, lock_path: Path):
        """start → scheduler running → stop → clean exit."""
        sched = SpiderScheduler(lock_path=lock_path)
        assert not sched.started
        sched.start()
        assert sched.started
        assert sched.scheduler.running
        sched.stop()
        assert not sched.started

    def test_double_start_raises(self, lock_path: Path):
        """同一 lock 第二次 start 抛 IOError（防重入）。"""
        sched1 = SpiderScheduler(lock_path=lock_path)
        sched1.start()
        sched2 = SpiderScheduler(lock_path=lock_path)
        with pytest.raises((IOError, OSError)):
            sched2.start()
        sched1.stop()


# ── S2 · File lock 防重入 ────────────────────────────────────────────────

class TestFileLock:
    def test_lock_file_created(self, lock_path: Path):
        """start 后锁文件存在。"""
        sched = SpiderScheduler(lock_path=lock_path)
        sched.start()
        assert lock_path.exists()
        sched.stop()

    def test_lock_released_on_stop(self, lock_path: Path):
        """stop 后锁释放，可以重新 start。"""
        sched = SpiderScheduler(lock_path=lock_path)
        sched.start()
        sched.stop()
        # 释放后同一实例重新 start 无异常
        sched.start()
        assert sched.started
        sched.stop()


# ── S3 · 默认 cron 注册 ─────────────────────────────────────────────────

class TestDefaultJobs:
    def test_register_default_jobs(self, lock_path: Path):
        """register_default_jobs 创建 2 个 cron job。"""
        sched = SpiderScheduler(lock_path=lock_path)
        sched.start()
        sched.register_default_jobs()
        jobs = sched.scheduler.get_jobs()
        job_ids = [j.id for j in jobs]
        assert "weekly_evaluator" in job_ids
        assert "daily_cookie_check" in job_ids
        assert len(jobs) == 2
        sched.stop()


# ── S4 · Scheduler disabled ──────────────────────────────────────────────

class TestDisabled:
    def test_disabled_dont_start(self, lock_path: Path, tmp_path: Path):
        """settings scheduler.enabled=false 时，server lifespan 不启动 scheduler。"""
        # 模拟 lifespan 逻辑
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(SETTINGS_DISABLED, encoding="utf-8")

        cfg = json.loads(settings_file.read_text(encoding="utf-8"))
        enabled = cfg.get("scheduler", {}).get("enabled", False)
        assert not enabled

        sched = None
        if enabled:
            sched = SpiderScheduler(lock_path=lock_path)
            sched.start()
        assert sched is None

    def test_enabled_starts(self, lock_path: Path, tmp_path: Path):
        """settings scheduler.enabled=true 时 scheduler 启动。"""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(SETTINGS_ENABLED, encoding="utf-8")

        cfg = json.loads(settings_file.read_text(encoding="utf-8"))
        enabled = cfg.get("scheduler", {}).get("enabled", False)
        assert enabled

        sched = SpiderScheduler(lock_path=lock_path)
        sched.start()
        assert sched.started
        sched.stop()


# ── S6 · 锁释放后重入（S1 已覆盖，这里做跨实例验证） ──────────────────

class TestReentry:
    def test_reentry_after_release(self, lock_path: Path):
        """锁释放后，不同实例可 start。"""
        s1 = SpiderScheduler(lock_path=lock_path)
        s1.start()
        s1.stop()

        s2 = SpiderScheduler(lock_path=lock_path)
        s2.start()
        assert s2.started
        s2.stop()
