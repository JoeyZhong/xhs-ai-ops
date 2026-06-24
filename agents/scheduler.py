from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

LOCK_DIR = Path("xhs_data")
LOCK_FILE = ".scheduler.lock"


class SchedulerLock:
    """Cross-platform exclusive file lock via fcntl (Unix) / msvcrt (Windows)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: Optional[int] = None

    def acquire(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(str(self._path), os.O_CREAT | os.O_RDWR)
        try:
            if sys.platform == "win32":
                import msvcrt  # noqa: PLC0415

                msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl  # noqa: PLC0415

                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError):
            os.close(self._fd)
            self._fd = None
            raise

    def release(self) -> None:
        if self._fd is not None:
            try:
                if sys.platform == "win32":
                    import msvcrt  # noqa: PLC0415

                    msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl  # noqa: PLC0415

                    fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None

    def __enter__(self) -> "SchedulerLock":
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.release()


class SpiderScheduler:
    """Wrapper around APScheduler BackgroundScheduler with file-lock guard.

    Usage::

        sched = SpiderScheduler()
        sched.start()          # acquires lock
        sched.stop()           # releases lock
    """

    def __init__(self, lock_path: Path = LOCK_DIR / LOCK_FILE) -> None:
        self._lock = SchedulerLock(lock_path)
        self._scheduler = BackgroundScheduler(daemon=True)
        self._started = False

    # ── built-in cron jobs ─────────────────────────────────────────────

    def _weekly_evaluator(self) -> None:
        """Monday 09:00 — Analyst weekly evaluation (P3.2)."""
        logger = logging.getLogger("uvicorn.access")
        logger.info("[Scheduler] Weekly evaluator started.")
        try:
            from agents.evaluators import AnalystEvaluator  # noqa: PLC0415

            evaluator = AnalystEvaluator()
            result = evaluator.run()
            logger.info(
                "[Scheduler] Weekly evaluator finished: ok=%s entry=%s conf=%s",
                result.get("ok"), result.get("entry_id"), result.get("confidence"),
            )
        except Exception as exc:
            logger.exception("[Scheduler] Weekly evaluator failed: %s", exc)

    def _daily_cookie_check(self) -> None:
        """Daily 06:00 — cookie health check (P3.3).

        Calls search API with a probe keyword; on failure writes
        xhs_data/_health/cookie_alert.json.  On success cleans the alert
        and saves a probe snapshot (keeps 3 most recent).
        """
        logger = logging.getLogger("uvicorn.access")
        health_dir = Path("xhs_data/_health")
        health_dir.mkdir(parents=True, exist_ok=True)
        alert_path = health_dir / "cookie_alert.json"
        last_success_path = health_dir / "last_success.txt"

        # 1. Get cookies
        cookies_str = ""
        try:
            from storage.cookie_manager import get_cookie  # noqa: PLC0415
            cookies_str = get_cookie("default") or ""
        except Exception:
            pass
        if not cookies_str:
            import os as _os  # noqa: PLC0415
            cookies_str = _os.environ.get("COOKIES", "")

        if not cookies_str:
            _write_alert(alert_path, "no cookies available")
            logger.warning("[Scheduler] Cookie health check: no cookies available")
            return

        # 2. Probe the API
        try:
            from agent_tools.search import collect_for_keyword  # noqa: PLC0415
            notes, err, _ = collect_for_keyword("测试", 1, cookies_str,
                                                 enable_browser_fallback=False)
        except Exception as exc:
            _write_alert(alert_path, f"exception: {exc}")
            logger.exception("[Scheduler] Cookie health check exception: %s", exc)
            return

        if err:
            _write_alert(alert_path, err, _read_last_success(last_success_path))
            logger.warning("[Scheduler] Cookie health check FAILED: %s", err)
            return

        # 3. Success — save snapshot & clean alert
        if alert_path.exists():
            try:
                alert_path.unlink()
            except Exception:
                pass
        _write_last_success(last_success_path)
        # Save probe snapshot (collect_notes with output_dir saves + cleans old)
        try:
            from agent_tools.search import collect_batch  # noqa: PLC0415
            result = collect_batch(["测试"], 1, cookies_str, progress_print=False)
            if result["records"]:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                xlsx = health_dir / f"health_check_{ts}.xlsx"
                import pandas as pd  # noqa: PLC0415
                pd.DataFrame(result["records"]).to_excel(xlsx, index=False)
                # keep 3 most recent
                files = sorted(health_dir.glob("health_check_*.xlsx"), reverse=True)
                for f in files[3:]:
                    try:
                        f.unlink()
                    except Exception:
                        pass
        except Exception as exc:
            logger.warning("[Scheduler] Cookie health check snapshot save failed: %s", exc)

        logger.info("[Scheduler] Cookie health check OK")

    def _weekly_sidecar_update(self) -> None:
        """Weekly 03:00 — 自动升级 xhs SDK 并重启 sidecar。

        如果 sidecar 未运行则跳过（不报错）。安装失败不重启。
        """
        logger = logging.getLogger("uvicorn.access")
        logger.info("[Scheduler] Weekly sidecar update started.")
        pid_path = Path("sidecar/.sidecar.pid")
        if not pid_path.exists():
            logger.info("[Scheduler] Sidecar not running — skip update.")
            return

        import subprocess  # noqa: PLC0415

        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "xhs"],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                logger.error("[Scheduler] xhs upgrade failed: %s", result.stderr.strip())
                return
            logger.info("[Scheduler] xhs upgraded: %s", result.stdout.strip()[:200])
        except subprocess.TimeoutExpired:
            logger.warning("[Scheduler] xhs upgrade timed out — skip this week.")
            return

        # 重启 sidecar
        try:
            subprocess.run(
                [sys.executable, "sidecar/manage.py", "restart"],
                capture_output=True, timeout=30,
            )
            logger.info("[Scheduler] Sidecar restarted after xhs upgrade.")
        except Exception as exc:
            logger.error("[Scheduler] Sidecar restart failed: %s", exc)

    def _radar_scan(self) -> None:
        """每日 09:30/14:30/20:30 — 线索雷达扫描（lead-intent-radar V1）。

        扫描所有 opt-in（goal.lead_radar_enabled=true）且有 keywords 的 goal：
        采集 → 意图判定 → 合格则草稿+入库。频率 3 次/天、≥2h 间隔，符合采集频控规则。
        """
        logger = logging.getLogger("uvicorn.access")
        logger.info("[Scheduler] Lead radar scan started.")
        # 定时雷达 = 真实采集：默认走 native（项目自带 JS 签名，真抓小红书）。
        # 不设则 _get_collector() 会退回库级 fixture 默认（假数据）。
        # operator 可用 env XHS_COLLECTOR 显式覆盖为 fixture/sidecar——setdefault 不覆盖已设值。
        os.environ.setdefault("XHS_COLLECTOR", "native")
        logger.info("[Scheduler] Lead radar collector = %s",
                    os.environ.get("XHS_COLLECTOR"))
        try:
            import storage.factory  # noqa: PLC0415
            from agents.lead_radar import scan_goal  # noqa: PLC0415

            backend = storage.factory.get_backend()
            tenant = "default"
            goals = (backend.load_goals(tenant) or {}).get("goals", [])
            targets = [g for g in goals
                       if g.get("lead_radar_enabled") and g.get("keywords")]
            if not targets:
                logger.info("[Scheduler] Lead radar: no opt-in goals, skip.")
                return
            for g in targets:
                try:
                    r = scan_goal(tenant, g["id"], storage=backend)
                    s = r.get("stats", {})
                    logger.info(
                        "[Scheduler] Lead radar goal=%s ok=%s scanned=%s "
                        "qualified=%s created=%s dup=%s",
                        g.get("id"), r.get("ok"), s.get("scanned"),
                        s.get("qualified"), s.get("created"), s.get("duplicate"),
                    )
                except Exception as exc:
                    logger.exception(
                        "[Scheduler] Lead radar goal=%s failed: %s", g.get("id"), exc)
        except Exception as exc:
            logger.exception("[Scheduler] Lead radar scan failed: %s", exc)

    def register_default_jobs(self) -> None:
        """Register the standard cron jobs."""
        self._scheduler.add_job(
            self._weekly_evaluator,
            CronTrigger(day_of_week="mon", hour=9, minute=0),
            id="weekly_evaluator",
            name="Weekly Evaluator — 周一 09:00",
            replace_existing=True,
        )
        self._scheduler.add_job(
            self._daily_cookie_check,
            CronTrigger(hour=6, minute=0),
            id="daily_cookie_check",
            name="Cookie Health Check — 每日 06:00",
            replace_existing=True,
        )
        self._scheduler.add_job(
            self._radar_scan,
            CronTrigger(hour="9,14,20", minute=30),
            id="lead_radar_scan",
            name="Lead Radar Scan — 每日 09:30/14:30/20:30",
            replace_existing=True,
        )
        # Sidecar SDK 自动更新（周日 03:00，低峰期）
        self._scheduler.add_job(
            self._weekly_sidecar_update,
            CronTrigger(day_of_week="sun", hour=3, minute=0),
            id="weekly_sidecar_update",
            name="Sidecar SDK Update — 周日 03:00",
            replace_existing=True,
        )

    @property
    def started(self) -> bool:
        return self._started

    @property
    def scheduler(self) -> BackgroundScheduler:
        return self._scheduler

    def start(self) -> None:
        """Acquire file lock and start the background scheduler.

        Raises IOError if lock is already held.
        """
        self._lock.acquire()
        try:
            self._scheduler.start()
            self._started = True
        except Exception:
            self._lock.release()
            raise

    def stop(self) -> None:
        """Shut down scheduler and release file lock."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        self._started = False
        self._lock.release()

    def get_jobs_info(self) -> list[dict]:
        """Return human-readable list of registered jobs (for dashboard / debug)."""
        return [
            {
                "id": j.id,
                "name": j.name,
                "next_run_time": str(j.next_run_time) if j.next_run_time else None,
            }
            for j in self._scheduler.get_jobs()
        ]


# ── Module-level helpers ───────────────────────────────────────


def _write_alert(path: Path, error: str, last_success: str = "") -> None:
    import json  # noqa: PLC0415
    from datetime import datetime  # noqa: PLC0415
    alert = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "error": error,
        "last_success": last_success,
    }
    path.write_text(json.dumps(alert, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_last_success(path: Path) -> None:
    from datetime import datetime  # noqa: PLC0415
    path.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")


def _read_last_success(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
