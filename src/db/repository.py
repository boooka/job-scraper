"""Vacancy repository with upsert and change tracking."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.logger import get_logger
from src.models.orm import ScrapeRun, Vacancy, VacancyChange
from src.models.schemas import VacancyData

log = get_logger(__name__)

# Fields tracked for change detection
TRACKED_FIELDS: tuple[str, ...] = (
    "title",
    "company",
    "location",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_period",
    "description",
)


class VacancyRepository:
    """All DB operations for vacancies."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_vacancy(self, data: VacancyData) -> tuple[str, list[VacancyChange]]:
        """
        Insert or update a vacancy.

        Returns:
            (action, changes) where action is 'created' | 'updated' | 'unchanged'
        """
        # Try to load existing
        stmt = select(Vacancy).where(
            Vacancy.source == data.source,
            Vacancy.external_id == data.external_id,
        )
        result = await self._session.execute(stmt)
        existing: Vacancy | None = result.scalar_one_or_none()

        if existing is None:
            vacancy = Vacancy(
                source=data.source,
                external_id=data.external_id,
                title=data.title,
                company=data.company,
                location=data.location,
                url=data.url,
                description=data.description,
                salary_min=data.salary_min,
                salary_max=data.salary_max,
                salary_currency=data.salary_currency,
                salary_period=data.salary_period,
                extra=data.extra,
                is_active=True,
                first_seen_at=datetime.now(timezone.utc),
                last_seen_at=datetime.now(timezone.utc),
            )
            self._session.add(vacancy)
            await self._session.flush()
            log.debug("vacancy.created", source=data.source, external_id=data.external_id)
            return "created", []

        # Detect field-level changes
        changes: list[VacancyChange] = []
        update_fields: dict[str, Any] = {
            "last_seen_at": datetime.now(timezone.utc),
            "is_active": True,
        }

        for field in TRACKED_FIELDS:
            old_val = getattr(existing, field)
            new_val = getattr(data, field)
            if old_val != new_val:
                changes.append(
                    VacancyChange(
                        vacancy_id=existing.id,
                        field_name=field,
                        old_value=str(old_val) if old_val is not None else None,
                        new_value=str(new_val) if new_val is not None else None,
                    )
                )
                update_fields[field] = new_val

        if changes:
            for change in changes:
                self._session.add(change)
            stmt_upd = (
                update(Vacancy)
                .where(Vacancy.id == existing.id)
                .values(**update_fields)
            )
            await self._session.execute(stmt_upd)
            log.debug(
                "vacancy.updated",
                source=data.source,
                external_id=data.external_id,
                changed_fields=[c.field_name for c in changes],
            )
            return "changed", changes

        # Still touch last_seen_at
        await self._session.execute(
            update(Vacancy)
            .where(Vacancy.id == existing.id)
            .values(last_seen_at=datetime.now(timezone.utc), is_active=True)
        )
        return "unchanged", []

    async def deactivate_missing(self, source: str, seen_ids: set[str]) -> int:
        """
        Mark vacancies not present in current scrape as inactive.

        Returns count of deactivated vacancies.
        """
        stmt = select(Vacancy).where(
            Vacancy.source == source,
            Vacancy.is_active.is_(True),
            Vacancy.external_id.not_in(seen_ids),
        )
        result = await self._session.execute(stmt)
        missing = result.scalars().all()

        for v in missing:
            v.is_active = False
            self._session.add(
                VacancyChange(
                    vacancy_id=v.id,
                    field_name="is_active",
                    old_value="True",
                    new_value="False",
                )
            )

        log.info("vacancies.deactivated", source=source, count=len(missing))
        return len(missing)

    async def get_active_vacancies(self, source: str | None = None) -> Sequence[Vacancy]:
        stmt = select(Vacancy).where(Vacancy.is_active.is_(True))
        if source:
            stmt = stmt.where(Vacancy.source == source)
        result = await self._session.execute(stmt)
        return result.scalars().all()


class ScrapeRunRepository:
    """CRUD for scrape run logs."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def start_run(self, source: str) -> ScrapeRun:
        run = ScrapeRun(source=source, status="running")
        self._session.add(run)
        await self._session.flush()
        return run

    async def finish_run(
        self,
        run: ScrapeRun,
        *,
        status: str,
        vacancies_found: int = 0,
        new_count: int = 0,
        changed_count: int = 0,
        deactivated_count: int = 0,
        error_message: str | None = None,
    ) -> None:
        run.status = status
        run.finished_at = datetime.now(timezone.utc)
        run.vacancies_found = vacancies_found
        run.new_count = new_count
        run.changed_count = changed_count
        run.deactivated_count = deactivated_count
        run.error_message = error_message
        self._session.add(run)
