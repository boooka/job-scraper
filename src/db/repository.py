"""Vacancy repository with upsert, change tracking, company upsert."""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from sqlalchemy import and_, func, literal, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.logger import get_logger
from src.models.orm import (
    Company,
    ScrapeRun,
    TelegramSubscription,
    TelegramSubscriptionDelivery,
    TranslationCache,
    Vacancy,
    VacancyChange,
    VacancyTranslation,
)
from src.models.schemas import VacancyData

log = get_logger(__name__)

# Fields tracked for change detection (must match Vacancy column names)
TRACKED_FIELDS: tuple[str, ...] = (
    "title",
    "company_name",
    "location",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_period",
    "salary_type",
    "description",
    "welcome_ukraine",
)


class CompanyRepository:
    """Upsert and lookup companies."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create(
        self,
        source: str,
        name: str,
        external_id: str | None = None,
    ) -> Company:
        """
        Return existing company by (source, external_id) or (source, name),
        creating it if not found.

        external_id: pass the site's own company ID when available.
        If not available a stable synthetic ID is derived from name.
        """
        ext_id = external_id or _synthetic_company_id(source, name)

        stmt = select(Company).where(
            Company.source == source,
            Company.external_id == ext_id,
        )
        result = await self._session.execute(stmt)
        company = result.scalar_one_or_none()

        if company is None:
            company = Company(
                source=source,
                external_id=ext_id,
                name=name,
            )
            self._session.add(company)
            await self._session.flush()
            log.debug("company.created", source=source, name=name)

        return company

    async def update_details(
        self,
        company: Company,
        *,
        employee_count: int | None = None,
        country: str | None = None,
        office_address: str | None = None,
        contact_person: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Patch company fields that were enriched later (e.g. from detail page)."""
        changed = False
        for attr, val in [
            ("employee_count", employee_count),
            ("country", country),
            ("office_address", office_address),
            ("contact_person", contact_person),
            ("extra", extra),
        ]:
            if val is not None and getattr(company, attr) != val:
                setattr(company, attr, val)
                changed = True

        if changed:
            company.updated_at = datetime.now(timezone.utc)
            self._session.add(company)


class VacancyRepository:
    """All DB operations for vacancies."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._company_repo = CompanyRepository(session)

    async def upsert_vacancy(self, data: VacancyData) -> tuple[str, list[VacancyChange]]:
        """
        Insert or update a vacancy, auto-upserting its company.

        Returns:
            (action, changes) where action is 'created' | 'updated' | 'unchanged'
        """
        # Resolve company FK when a company name is present
        company_id: uuid.UUID | None = None
        if data.company:
            company = await self._company_repo.get_or_create(
                source=data.source,
                name=data.company,
            )
            company_id = company.id

        # Load existing vacancy
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
                company_id=company_id,
                company_name=data.company,
                title=data.title,
                location=data.location,
                url=data.url,
                description=data.description,
                page_html=data.page_html,
                salary_min=data.salary_min,
                salary_max=data.salary_max,
                salary_currency=data.salary_currency,
                salary_period=data.salary_period,
                salary_type=data.salary_type,
                welcome_ukraine=data.welcome_ukraine if data.welcome_ukraine else False,
                extra=data.extra or {},
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
            # Always refresh non-tracked fields silently
            "page_html": data.page_html,
            "url": data.url,
        }

        # Sync company FK if it changed
        if company_id and existing.company_id != company_id:
            update_fields["company_id"] = company_id

        # Map VacancyData field → Vacancy column name
        schema_to_col: dict[str, str] = {
            "company": "company_name",  # schema uses "company", ORM uses "company_name"
        }

        for field in TRACKED_FIELDS:
            schema_field = {v: k for k, v in schema_to_col.items()}.get(field, field)
            old_val = getattr(existing, field)
            new_val = getattr(data, schema_field, getattr(data, field, None))

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
            await self._session.execute(
                update(Vacancy)
                .where(Vacancy.id == existing.id)
                .values(**update_fields)
            )
            log.debug(
                "vacancy.updated",
                source=data.source,
                external_id=data.external_id,
                changed_fields=[c.field_name for c in changes],
            )
            return "updated", changes

        # Touch last_seen_at even if nothing changed
        await self._session.execute(
            update(Vacancy)
            .where(Vacancy.id == existing.id)
            .values(
                last_seen_at=datetime.now(timezone.utc),
                is_active=True,
                page_html=data.page_html,
                url=data.url,
            )
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


class TranslationRepository:
    """Upsert vacancy translations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        vacancy_id: uuid.UUID,
        language: str,
        *,
        title_translated: str | None = None,
        description_translated: str | None = None,
        translator: str | None = None,
    ) -> VacancyTranslation:
        """Insert or overwrite a translation for a given vacancy + language."""
        stmt = select(VacancyTranslation).where(
            VacancyTranslation.vacancy_id == vacancy_id,
            VacancyTranslation.language == language,
        )
        result = await self._session.execute(stmt)
        translation = result.scalar_one_or_none()

        if translation is None:
            translation = VacancyTranslation(
                vacancy_id=vacancy_id,
                language=language,
                title_translated=title_translated,
                description_translated=description_translated,
                translator=translator,
                translated_at=datetime.now(timezone.utc),
            )
            self._session.add(translation)
        else:
            translation.title_translated = title_translated
            translation.description_translated = description_translated
            translation.translator = translator
            translation.translated_at = datetime.now(timezone.utc)
            self._session.add(translation)

        await self._session.flush()
        return translation

    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    async def get_cached_many(self, language: str, texts: Sequence[str]) -> dict[str, str]:
        """Return cached translations for the provided source texts."""
        if not texts:
            return {}

        unique_texts = {t for t in texts if t.strip()}
        if not unique_texts:
            return {}

        hash_to_text = {self._hash_text(t): t for t in unique_texts}
        stmt = select(TranslationCache).where(
            TranslationCache.language == language,
            TranslationCache.text_hash.in_(tuple(hash_to_text.keys())),
        )
        result = await self._session.execute(stmt)
        cached_rows = result.scalars().all()
        return {hash_to_text[row.text_hash]: row.translated_text for row in cached_rows}

    async def cache_many(
        self,
        language: str,
        pairs: Iterable[tuple[str, str]],
        *,
        translator: str | None = None,
    ) -> None:
        """Save new translation pairs into cache, skipping existing keys."""
        for source_text, translated_text in pairs:
            if not source_text.strip():
                continue

            text_hash = self._hash_text(source_text)
            exists_stmt = select(TranslationCache.id).where(
                TranslationCache.language == language,
                TranslationCache.text_hash == text_hash,
            )
            exists_result = await self._session.execute(exists_stmt)
            if exists_result.scalar_one_or_none() is not None:
                continue

            self._session.add(
                TranslationCache(
                    language=language,
                    text_hash=text_hash,
                    source_text=source_text,
                    translated_text=translated_text,
                    translator=translator,
                )
            )
        await self._session.flush()


class TelegramSubscriptionRepository:
    """CRUD for Telegram user subscriptions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        telegram_user_id: int,
        username: str | None,
        chat_id: int,
        query: str,
    ) -> TelegramSubscription:
        subscription = TelegramSubscription(
            telegram_user_id=telegram_user_id,
            username=username,
            chat_id=chat_id,
            query=query,
            is_active=True,
        )
        self._session.add(subscription)
        await self._session.flush()
        return subscription

    async def list_active_for_user(self, telegram_user_id: int) -> list[TelegramSubscription]:
        stmt = (
            select(TelegramSubscription)
            .where(
                TelegramSubscription.telegram_user_id == telegram_user_id,
                TelegramSubscription.is_active.is_(True),
            )
            .order_by(TelegramSubscription.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class TelegramDeliveryRepository:
    """Track already delivered vacancies per subscription."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def was_sent(self, subscription_id: int, vacancy_id: uuid.UUID) -> bool:
        stmt = select(TelegramSubscriptionDelivery.id).where(
            TelegramSubscriptionDelivery.subscription_id == subscription_id,
            TelegramSubscriptionDelivery.vacancy_id == vacancy_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def mark_sent(self, subscription_id: int, vacancy_id: uuid.UUID) -> None:
        if await self.was_sent(subscription_id, vacancy_id):
            return
        self._session.add(
            TelegramSubscriptionDelivery(
                subscription_id=subscription_id,
                vacancy_id=vacancy_id,
            )
        )
        await self._session.flush()

    async def cancel_for_user(self, subscription_id: int, telegram_user_id: int) -> bool:
        stmt = select(TelegramSubscription).where(
            TelegramSubscription.id == subscription_id,
            TelegramSubscription.telegram_user_id == telegram_user_id,
            TelegramSubscription.is_active.is_(True),
        )
        result = await self._session.execute(stmt)
        sub = result.scalar_one_or_none()
        if sub is None:
            return False
        sub.is_active = False
        sub.cancelled_at = datetime.now(timezone.utc)
        self._session.add(sub)
        return True

    async def list_all_active(self) -> list[TelegramSubscription]:
        stmt = (
            select(TelegramSubscription)
            .where(TelegramSubscription.is_active.is_(True))
            .order_by(TelegramSubscription.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class VacancySearchRepository:
    """Vacancy search with include/exclude/fuzzy and admin regex support."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _fuzzy_to_sql_like(term: str) -> str:
        escaped = re.escape(term).replace(r"\*", "%")
        return escaped.replace(r"\%", "%")

    @staticmethod
    def _clean_plain_text_sql(column: Any) -> Any:
        """Strip basic HTML tags for text search."""
        return func.regexp_replace(func.coalesce(column, ""), "<[^>]+>", " ", "g")

    async def search(
        self,
        *,
        includes: Sequence[str],
        excludes: Sequence[str],
        fuzzy: Sequence[str],
        regex: str | None,
        language: str,
        limit: int,
        is_admin: bool,
    ) -> Sequence[Vacancy]:
        dialect = self._session.bind.dialect.name if self._session.bind is not None else "postgresql"

        if dialect != "postgresql":
            return await self._search_fallback(
                includes=includes,
                excludes=excludes,
                fuzzy=fuzzy,
                regex=regex,
                limit=limit,
                is_admin=is_admin,
            )

        translated_text = func.coalesce(
            func.string_agg(
                func.concat(
                    func.coalesce(VacancyTranslation.title_translated, ""),
                    literal(" "),
                    self._clean_plain_text_sql(VacancyTranslation.description_translated),
                ),
                literal(" "),
            ),
            "",
        )
        original_text = func.concat(
            func.coalesce(Vacancy.title, ""),
            literal(" "),
            self._clean_plain_text_sql(Vacancy.description),
        )
        searchable_text = func.concat(original_text, literal(" "), translated_text)

        rank_expr = func.ts_rank_cd(
            func.to_tsvector("simple", searchable_text),
            func.plainto_tsquery("simple", " ".join(includes) if includes else ""),
        )

        stmt = (
            select(Vacancy, rank_expr.label("rank"))
            .outerjoin(VacancyTranslation, VacancyTranslation.vacancy_id == Vacancy.id)
            .where(Vacancy.is_active.is_(True))
            .group_by(Vacancy.id)
        )

        include_conditions = [
            func.to_tsvector("simple", searchable_text).op("@@")(
                func.plainto_tsquery("simple", term)
            )
            for term in includes
            if term.strip()
        ]
        if include_conditions:
            stmt = stmt.where(and_(*include_conditions))

        for term in excludes:
            if not term.strip():
                continue
            stmt = stmt.where(
                and_(
                    ~searchable_text.ilike(f"%{term}%"),
                )
            )

        for pattern in fuzzy:
            if not pattern.strip():
                continue
            sql_like = self._fuzzy_to_sql_like(pattern)
            stmt = stmt.where(
                searchable_text.ilike(f"%{sql_like}%")
            )

        if regex and is_admin:
            stmt = stmt.where(
                searchable_text.op("~*")(regex)
            )

        stmt = stmt.order_by(rank_expr.desc(), Vacancy.first_seen_at.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return [row[0] for row in result.all()]

    async def _search_fallback(
        self,
        *,
        includes: Sequence[str],
        excludes: Sequence[str],
        fuzzy: Sequence[str],
        regex: str | None,
        limit: int,
        is_admin: bool,
    ) -> Sequence[Vacancy]:
        translated_text = (
            func.coalesce(VacancyTranslation.title_translated, "")
            + literal(" ")
            + func.coalesce(VacancyTranslation.description_translated, "")
        )
        original_text = (
            func.coalesce(Vacancy.title, "")
            + literal(" ")
            + func.coalesce(Vacancy.description, "")
        )
        searchable_text = original_text + literal(" ") + translated_text

        stmt = (
            select(Vacancy)
            .outerjoin(VacancyTranslation, VacancyTranslation.vacancy_id == Vacancy.id)
            .where(Vacancy.is_active.is_(True))
            .group_by(Vacancy.id)
            .order_by(Vacancy.first_seen_at.desc())
            .limit(limit)
        )

        for term in includes:
            if term.strip():
                stmt = stmt.where(searchable_text.ilike(f"%{term}%"))
        for term in excludes:
            if term.strip():
                stmt = stmt.where(~searchable_text.ilike(f"%{term}%"))
        for pattern in fuzzy:
            if pattern.strip():
                stmt = stmt.where(searchable_text.ilike(f"%{self._fuzzy_to_sql_like(pattern)}%"))
        if regex and is_admin:
            # Regex operator differs per dialect; fallback keeps deterministic behavior.
            stmt = stmt.where(searchable_text.ilike(f"%{regex}%"))

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


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _synthetic_company_id(source: str, name: str) -> str:
    """Stable synthetic external_id for companies without a site-assigned ID."""
    import hashlib
    return hashlib.md5(f"{source}::{name}".encode()).hexdigest()