"""APScheduler configuration and job definitions."""

from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.config import settings
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


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=_TZ)

    scheduler.add_job(
        run_scrape,
        trigger=_parse_cron(settings.schedule_cvbankas),
        args=[CVBankasScraper],
        id="cvbankas",
        name="CVBankas scraper",
        max_instances=1,
        misfire_grace_time=300,
    )

    scheduler.add_job(
        run_scrape,
        trigger=_parse_cron(settings.schedule_cvonline),
        args=[CVOnlineScraper],
        id="cvonline",
        name="CVOnline scraper",
        max_instances=1,
        misfire_grace_time=300,
    )

    scheduler.add_job(
        run_scrape,
        trigger=_parse_cron(settings.schedule_cvmarket),
        args=[CVMarketScraper],
        id="cvmarket",
        name="CVMarket scraper",
        max_instances=1,
        misfire_grace_time=300,
    )

    scheduler.add_job(
        run_scrape,
        trigger=_parse_cron(settings.schedule_cv),
        args=[CVScraper],
        id="cv",
        name="CV scraper",
        max_instances=1,
        misfire_grace_time=300,
    )

    # Translation catch-up job — handles any vacancies missed by real-time trigger
    # (e.g. if DeepL was unavailable during scrape)
    if settings.deepl_api_key:
        scheduler.add_job(
            run_pending_translations,
            trigger=_parse_cron(settings.schedule_translations),
            id="translations",
            name="DeepL translation catch-up",
            max_instances=1,
            misfire_grace_time=600,
        )

    if settings.telegram_bot_token:
        scheduler.add_job(
            run_subscription_notifications,
            trigger=_parse_cron(settings.schedule_subscription_notifications),
            id="subscription_notifications",
            name="Telegram subscription notifications",
            max_instances=1,
            misfire_grace_time=300,
        )

        # Daily health report: added/translated counts + stale-scraper alert
        scheduler.add_job(
            run_daily_admin_report,
            trigger=_parse_cron(settings.schedule_daily_report),
            id="daily_report",
            name="Daily admin health report",
            max_instances=1,
            misfire_grace_time=3600,
        )

    scheduler.add_job(
        dump_metrics_to_disk,
        trigger=IntervalTrigger(seconds=settings.metrics_dump_interval_seconds),
        id="metrics_dump",
        name="Metrics dump to disk",
        max_instances=1,
        misfire_grace_time=120,
    )

    return scheduler


async def run_scheduler() -> None:
    """Start scheduler and keep running."""
    scheduler = create_scheduler()

    # Логируем ближайшие next_run_time, чтобы было с чем сравнить `scrape_runs.started_at`
    now = datetime.now(tz=ZoneInfo(_TZ))
    jobs = scheduler.get_jobs()
    next_runs: dict[str, str | None] = {}
    for job in jobs:
        try:
            nxt = job.trigger.get_next_fire_time(None, now)  # type: ignore[attr-defined]
        except Exception:
            nxt = None
        next_runs[job.id] = nxt.isoformat() if nxt else None

    scheduler.start()
    log.info("scheduler.started", jobs=[j.id for j in jobs], next_runs=next_runs)

    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        log.info("scheduler.stopped")
