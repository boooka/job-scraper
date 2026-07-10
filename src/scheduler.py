"""APScheduler configuration and job definitions.

Cron schedules live in the ``schedules`` table and are editable from the Django
admin. The scheduler seeds that table from settings on first start, then reloads
it once a minute: changed crons are rescheduled, disabled jobs removed, enabled
jobs added, and admin "Run now" requests fired once — all without a restart.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.config import settings
from src.db.engine import get_session
from src.db.repository import ScheduleRepository
from src.logger import get_logger
from src.scrapers.cv import CVScraper
from src.scrapers.cvbankas import CVBankasScraper
from src.scrapers.cvmarket import CVMarketScraper
from src.scrapers.cvonline import CVOnlineScraper
from src.services.admin_notifier import run_daily_admin_report
from src.services.metrics_exporter import dump_metrics_to_disk
from src.services.scrape_service import run_scrape
from src.services.subscription_notifier import run_subscription_notifications
from src.services.translation_service import run_pending_translations

log = get_logger(__name__)

_TZ = "Europe/Vilnius"

# Registry of admin-managed jobs. Each entry: callable, positional args, display
# name, and an external gate (job is never scheduled while the gate is unmet,
# even if enabled in the DB). Keys match schedules.job_id.
_JOB_REGISTRY: dict[str, tuple[Callable[..., Any], list[Any], str, str | None]] = {
    "cvbankas": (run_scrape, [CVBankasScraper], "CVBankas scraper", None),
    "cvonline": (run_scrape, [CVOnlineScraper], "CVOnline scraper", None),
    "cvmarket": (run_scrape, [CVMarketScraper], "CVMarket scraper", None),
    "cv": (run_scrape, [CVScraper], "CV scraper", None),
    "translations": (run_pending_translations, [], "DeepL translation catch-up", "deepl"),
    "subscription_notifications": (
        run_subscription_notifications,
        [],
        "Telegram subscription notifications",
        "telegram",
    ),
    "daily_report": (run_daily_admin_report, [], "Daily admin health report", "telegram"),
}

# Per-job misfire grace (seconds); default applies to the rest.
_MISFIRE_GRACE = {"translations": 600, "daily_report": 3600}
_DEFAULT_MISFIRE = 300


def _parse_cron(expr: str) -> CronTrigger:
    """Parse a 5-field cron expression into a CronTrigger."""
    minute, hour, day, month, dow = expr.split()
    return CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=dow,
        timezone=_TZ,
    )


def _gate_ok(gate: str | None) -> bool:
    """Whether an external prerequisite for a job is satisfied."""
    if gate == "deepl":
        return bool(settings.deepl_api_key)
    if gate == "telegram":
        return bool(settings.telegram_bot_token)
    return True


def _default_schedules() -> list[dict[str, Any]]:
    """Seed rows built from the current settings (real .env cron values)."""
    cron_by_job = {
        "cvbankas": settings.schedule_cvbankas,
        "cvonline": settings.schedule_cvonline,
        "cvmarket": settings.schedule_cvmarket,
        "cv": settings.schedule_cv,
        "translations": settings.schedule_translations,
        "subscription_notifications": settings.schedule_subscription_notifications,
        "daily_report": settings.schedule_daily_report,
    }
    return [
        {
            "job_id": job_id,
            "name": name,
            "cron": cron_by_job[job_id],
            "enabled": _gate_ok(gate),
        }
        for job_id, (_func, _args, name, gate) in _JOB_REGISTRY.items()
    ]


async def _sync_schedules(
    scheduler: AsyncIOScheduler,
    applied_cron: dict[str, str],
    handled_run_now: dict[str, datetime],
    *,
    first_run: bool = False,
) -> None:
    """Reconcile the live scheduler with the ``schedules`` table.

    Adds/removes/reschedules admin-managed jobs and fires "Run now" requests.
    On ``first_run`` it seeds the table and treats any pre-existing run-now
    request as already handled (so a restart never re-triggers a stale one).
    """
    async with get_session() as session:
        repo = ScheduleRepository(session)
        if first_run:
            inserted = await repo.seed_missing(_default_schedules())
            if inserted:
                log.info("scheduler.schedules_seeded", inserted=inserted)

        rows = await repo.list_all()
        run_now_handled: list[str] = []

        for row in rows:
            entry = _JOB_REGISTRY.get(row.job_id)
            if entry is None:
                continue
            func, args, name, gate = entry
            job_id = row.job_id
            existing = scheduler.get_job(job_id)
            grace = _MISFIRE_GRACE.get(job_id, _DEFAULT_MISFIRE)

            # Disabled or externally gated off → ensure the job is not scheduled.
            if not row.enabled or not _gate_ok(gate):
                if existing:
                    scheduler.remove_job(job_id)
                    applied_cron.pop(job_id, None)
                    log.info("scheduler.job_removed", job=job_id)
                continue

            try:
                trigger = _parse_cron(row.cron)
            except Exception as exc:
                # A bad cron must not break syncing of the other jobs.
                log.error("scheduler.bad_cron", job=job_id, cron=row.cron, error=str(exc))
                continue

            if existing is None:
                scheduler.add_job(
                    func,
                    trigger=trigger,
                    args=args,
                    id=job_id,
                    name=name,
                    max_instances=1,
                    misfire_grace_time=grace,
                )
                applied_cron[job_id] = row.cron
                log.info("scheduler.job_added", job=job_id, cron=row.cron)
            elif applied_cron.get(job_id) != row.cron:
                scheduler.reschedule_job(job_id, trigger=trigger)
                applied_cron[job_id] = row.cron
                log.info("scheduler.job_rescheduled", job=job_id, cron=row.cron)

            # "Run now": fire once, out of schedule.
            rn = row.run_now_requested_at
            if rn is not None:
                if first_run:
                    handled_run_now[job_id] = rn
                    run_now_handled.append(job_id)
                elif handled_run_now.get(job_id) != rn:
                    handled_run_now[job_id] = rn
                    scheduler.add_job(
                        func,
                        trigger=DateTrigger(run_date=datetime.now(ZoneInfo(_TZ))),
                        args=args,
                        id=f"{job_id}:run_now:{rn.timestamp():.0f}",
                        name=f"{name} (run now)",
                        max_instances=1,
                        misfire_grace_time=grace,
                    )
                    run_now_handled.append(job_id)
                    log.info("scheduler.run_now", job=job_id)

        for job_id in run_now_handled:
            await repo.clear_run_now(job_id)


async def run_scheduler() -> None:
    """Start the scheduler, seed jobs from the DB, and keep reloading."""
    scheduler = AsyncIOScheduler(timezone=_TZ)

    # Cron expression currently applied per job, and the last handled run-now
    # timestamp per job — kept across reloads to avoid redundant reschedules.
    applied_cron: dict[str, str] = {}
    handled_run_now: dict[str, datetime] = {}

    # Metrics dump is an internal interval job, not admin-managed.
    scheduler.add_job(
        dump_metrics_to_disk,
        trigger=IntervalTrigger(seconds=settings.metrics_dump_interval_seconds),
        id="metrics_dump",
        name="Metrics dump to disk",
        max_instances=1,
        misfire_grace_time=120,
    )

    # Seed the table and build the admin-managed jobs from the DB.
    await _sync_schedules(scheduler, applied_cron, handled_run_now, first_run=True)

    async def _reload() -> None:
        try:
            await _sync_schedules(scheduler, applied_cron, handled_run_now)
        except Exception as exc:
            log.error("scheduler.reload_failed", error=str(exc))

    scheduler.add_job(
        _reload,
        trigger=IntervalTrigger(minutes=1),
        id="schedule_reload",
        name="Reload schedules from DB",
        max_instances=1,
        misfire_grace_time=30,
    )

    scheduler.start()
    log.info("scheduler.started", jobs=[j.id for j in scheduler.get_jobs()])

    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        log.info("scheduler.stopped")
