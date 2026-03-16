"""APScheduler configuration and job definitions."""
from __future__ import annotations

import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config import settings
from src.logger import get_logger
from src.scrapers.cvbankas import CVBankasScraper
from src.scrapers.cvmarket import CVMarketScraper
from src.scrapers.cvonline import CVOnlineScraper
from src.scrapers.cv import CVScraper   

from src.services.scrape_service import run_scrape

log = get_logger(__name__)


def _parse_cron(expr: str) -> CronTrigger:
    """Parse a 5-field cron expression into a CronTrigger."""
    minute, hour, day, month, dow = expr.split()
    return CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=dow,
    )


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Europe/Vilnius")

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
    return scheduler


async def run_scheduler() -> None:
    """Start scheduler and keep running."""
    scheduler = create_scheduler()
    scheduler.start()
    log.info("scheduler.started", jobs=[j.id for j in scheduler.get_jobs()])

    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        log.info("scheduler.stopped")
