"""Orchestration: run scraper → persist → track changes."""
from __future__ import annotations

from typing import Type

from src.db.engine import get_session
from src.db.repository import ScrapeRunRepository, VacancyRepository
from src.logger import get_logger
from src.models.schemas import VacancyData
from src.scrapers.base import BaseScraper

log = get_logger(__name__)


async def run_scrape(scraper_cls: Type[BaseScraper]) -> dict[str, int]:
    """
    Execute a full scrape cycle for one source:
      1. Scrape all vacancies
      2. Upsert each with change tracking
      3. Deactivate vacancies no longer present
      4. Record the run log

    Returns a summary dict.
    """
    source = scraper_cls.source
    summary = {
        "found": 0,
        "new": 0,
        "changed": 0,
        "unchanged": 0,
        "deactivated": 0,
        "created": 0,
    }

    async with get_session() as session:
        run_repo = ScrapeRunRepository(session)
        run = await run_repo.start_run(source)
        await session.commit()  # persist run ID before starting

    try:
        async with scraper_cls() as scraper:
            vacancies: list[VacancyData] = await scraper.run()

        summary["found"] = len(vacancies)
        seen_ids: set[str] = set()

        async with get_session() as session:
            repo = VacancyRepository(session)
            for vacancy in vacancies:
                seen_ids.add(vacancy.external_id)
                action, changes = await repo.upsert_vacancy(vacancy)
                
                summary[action] += 1
                if changes:
                    summary["changed"] += 1
                    summary["unchanged"] -= 1  # fix double-count with action='updated'

            summary["deactivated"] = await repo.deactivate_missing(source, seen_ids)

        # Persist final run stats
        async with get_session() as session:
            run_repo = ScrapeRunRepository(session)
            # Re-attach the run object
            from sqlalchemy import select
            from src.models.orm import ScrapeRun
            result = await session.execute(select(ScrapeRun).where(ScrapeRun.id == run.id))
            run = result.scalar_one()
            await run_repo.finish_run(
                run,
                status="success",
                vacancies_found=summary["found"],
                new_count=summary["new"],
                changed_count=summary["changed"],
                deactivated_count=summary["deactivated"],
            )

        log.info("scrape.complete", source=source, **summary)
        return summary

    except Exception as exc:
        log.error("scrape.failed", source=source, error=str(exc))
        async with get_session() as session:
            from sqlalchemy import select
            from src.models.orm import ScrapeRun
            result = await session.execute(select(ScrapeRun).where(ScrapeRun.id == run.id))
            run_obj = result.scalar_one_or_none()
            if run_obj:
                run_repo = ScrapeRunRepository(session)
                await run_repo.finish_run(run_obj, status="failed", error_message=str(exc))
        raise
