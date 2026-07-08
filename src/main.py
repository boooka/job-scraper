"""Main entrypoint — scheduler or one-off scrape."""

from __future__ import annotations

import asyncio
import sys

from src.logger import configure_logging, get_logger

configure_logging()
log = get_logger(__name__)


async def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "scheduler"

    if command == "scheduler":
        from src.scheduler import run_scheduler

        await run_scheduler()

    elif command == "scrape":
        # Usage: python -m src.main scrape [cvbankas|cvonline|cvmarket|all]
        target = sys.argv[2] if len(sys.argv) > 2 else "all"
        from src.scrapers.cv import CVScraper
        from src.scrapers.cvbankas import CVBankasScraper
        from src.scrapers.cvmarket import CVMarketScraper
        from src.scrapers.cvonline import CVOnlineScraper
        from src.services.scrape_service import run_scrape

        scrapers = {
            "cvbankas": CVBankasScraper,
            "cvonline": CVOnlineScraper,
            "cvmarket": CVMarketScraper,
            "cv": CVScraper,
        }

        targets = list(scrapers.values()) if target == "all" else [scrapers[target]]
        for scraper_cls in targets:
            if not scraper_cls.is_active:
                continue
            result = await run_scrape(scraper_cls)
            log.info("run.summary", **result)

    elif command == "migrate":
        from src.db.engine import create_tables

        await create_tables()
        log.info("db.tables_created")

    elif command == "backfill-cities":
        from src.services.city_backfill import backfill_cities

        summary = await backfill_cities()
        log.info("backfill.summary", **summary)

    elif command == "backfill-company-groups":
        from src.services.company_backfill import backfill_company_groups

        summary = await backfill_company_groups()
        log.info("backfill.summary", **summary)

    elif command == "daily-report":
        from src.services.admin_notifier import run_daily_admin_report

        result = await run_daily_admin_report()
        print(result.get("report", ""))
        log.info("daily_report.manual_run", sent_to_admins=result.get("sent_to_admins", 0))

    elif command == "bot":
        from src.services.telegram_bot import run_telegram_bot

        await run_telegram_bot()

    else:
        print(f"Unknown command: {command}")
        print(
            "Usage: python -m src.main "
            "[scheduler|scrape [source]|migrate|backfill-cities|"
            "backfill-company-groups|daily-report|bot]"
        )
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
