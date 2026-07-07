"""Orchestration: run scraper → persist in batches → track changes → translate."""

from __future__ import annotations

import asyncio

from src.config import settings
from src.db.engine import get_session
from src.db.repository import ScrapeRunRepository, VacancyRepository
from src.logger import get_logger
from src.models.schemas import VacancyData
from src.scrapers.base import BaseScraper

log = get_logger(__name__)

BATCH_SIZE = 200


async def _flush_batch(
    batch: list[VacancyData],
    seen_ids: set[str],
    summary: dict[str, int],
) -> None:
    """Upsert one batch of vacancies, accumulate counters, trigger translations."""
    if not batch:
        return

    new_before = summary.get("created", 0)

    async with get_session() as session:
        repo = VacancyRepository(session)
        for vacancy in batch:
            seen_ids.add(vacancy.external_id)
            action, changes = await repo.upsert_vacancy(vacancy)
            summary[action] = summary.get(action, 0) + 1
            if changes:
                summary["changed"] = summary.get("changed", 0) + 1

    new_after = summary.get("created", 0)
    new_in_batch = new_after - new_before

    log.info(
        "scrape.batch_flushed",
        batch_size=len(batch),
        new_in_batch=new_in_batch,
        total_seen=len(seen_ids),
    )

    # Fire translation for new vacancies immediately after each batch
    if new_in_batch > 0 and settings.deepl_api_key:
        asyncio.create_task(_run_translations_bg())


async def _run_translations_bg() -> None:
    """Run translations in background — errors are logged, never propagated."""
    try:
        from src.services.translation_service import run_pending_translations

        await run_pending_translations()
    except Exception as exc:
        log.error("translation.bg_failed", error=str(exc))


async def run_scrape(scraper_cls: type[BaseScraper]) -> dict[str, int]:
    """
    Execute a full scrape cycle for one source.

    Flow:
      1. Start run log entry
      2. Scrape via generator — flush to DB every BATCH_SIZE items
         → translations triggered automatically after each batch with new items
      3. Deactivate vacancies absent from this run
      4. Finalise run log with counters

    Returns summary dict with found / created / updated / unchanged / changed / deactivated.
    """
    source = scraper_cls.source
    summary: dict[str, int] = {
        "found": 0,
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "changed": 0,
        "skipped": 0,
        "deactivated": 0,
    }
    seen_ids: set[str] = set()

    # 1. Start run log
    async with get_session() as session:
        run_repo = ScrapeRunRepository(session)
        run = await run_repo.start_run(source)
        await session.commit()
    run_id = run.id

    try:
        # 2. Scrape + flush in batches
        async with scraper_cls() as scraper:
            batch: list[VacancyData] = []

            async for vacancy in scraper.scrape_all():
                summary["found"] += 1
                batch.append(vacancy)

                if len(batch) >= BATCH_SIZE:
                    await _flush_batch(batch, seen_ids, summary)
                    batch.clear()

            if batch:
                await _flush_batch(batch, seen_ids, summary)

        # 3. Deactivate missing
        async with get_session() as session:
            repo = VacancyRepository(session)
            summary["deactivated"] = await repo.deactivate_missing(source, seen_ids)

        # 4. Finalise run log
        async with get_session() as session:
            from sqlalchemy import select

            from src.models.orm import ScrapeRun

            result = await session.execute(select(ScrapeRun).where(ScrapeRun.id == run_id))
            run_obj = result.scalar_one()
            run_repo = ScrapeRunRepository(session)
            await run_repo.finish_run(
                run_obj,
                status="success",
                vacancies_found=summary["found"],
                new_count=summary["created"],
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

            result = await session.execute(select(ScrapeRun).where(ScrapeRun.id == run_id))
            run_obj = result.scalar_one_or_none()
            if run_obj:
                run_repo = ScrapeRunRepository(session)
                await run_repo.finish_run(
                    run_obj,
                    status="failed",
                    vacancies_found=summary["found"],
                    new_count=summary["created"],
                    changed_count=summary["changed"],
                    deactivated_count=summary["deactivated"],
                    error_message=str(exc),
                )
        raise
