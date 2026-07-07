"""One-off backfill: populate `cities` and set `vacancies.city_id` for existing rows.

Idempotent — safe to re-run. New scrapes resolve the city automatically via
:class:`~src.db.repository.CityRepository`, so this is only needed once for data
that predates the cities feature (and after schema changes).
"""

from __future__ import annotations

from sqlalchemy import select, update

from src.db.engine import get_session
from src.db.repository import CityRepository
from src.logger import get_logger
from src.models.orm import Vacancy

log = get_logger(__name__)

BATCH_SIZE = 500


async def backfill_cities() -> dict[str, int]:
    """Resolve every vacancy's raw location to a normalised city.

    Works one distinct location at a time (there are only a couple hundred),
    resolving each to a City and bulk-updating all vacancies that share it.
    """
    async with get_session() as session:
        result = await session.execute(
            select(Vacancy.location).where(Vacancy.location.is_not(None)).group_by(Vacancy.location)
        )
        locations = [row[0] for row in result.all() if row[0] and row[0].strip()]

    log.info("city_backfill.start", distinct_locations=len(locations))

    updated = 0
    cities_seen: set[int] = set()
    for raw in locations:
        async with get_session() as session:
            city_repo = CityRepository(session)
            city = await city_repo.resolve(raw)
            if city is None:
                continue
            cities_seen.add(city.id)
            res = await session.execute(
                update(Vacancy)
                .where(Vacancy.location == raw, Vacancy.city_id.is_distinct_from(city.id))
                .values(city_id=city.id)
            )
            updated += res.rowcount or 0

    summary = {
        "distinct_locations": len(locations),
        "cities": len(cities_seen),
        "vacancies_updated": updated,
    }
    log.info("city_backfill.complete", **summary)
    return summary
