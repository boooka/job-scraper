"""Vacancy repository with upsert, change tracking, company upsert."""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, literal, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.logger import get_logger
from src.models.orm import (
    City,
    Company,
    CompanyGroup,
    Schedule,
    ScrapeRun,
    TelegramSubscription,
    TelegramSubscriptionDelivery,
    TelegramUser,
    TranslationCache,
    Vacancy,
    VacancyChange,
    VacancyTranslation,
)
from src.models.schemas import VacancyData
from src.services.city_normalizer import normalize_city
from src.services.company_normalizer import normalize_company_name

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


class CompanyGroupRepository:
    """Resolve company names to a canonical cross-source CompanyGroup."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._cache: dict[str, CompanyGroup] = {}

    async def _select(self, normalized_key: str) -> CompanyGroup | None:
        result = await self._session.execute(
            select(CompanyGroup).where(CompanyGroup.normalized_key == normalized_key)
        )
        return result.scalar_one_or_none()

    async def get_or_create(self, name: str) -> CompanyGroup | None:
        """Return the group for this company name, creating it if absent.

        Returns None when the name yields an empty key (e.g. only a legal form).
        Uses a per-batch cache + SAVEPOINT to survive a concurrent unique-insert
        race (same pattern as CityRepository).
        """
        key = normalize_company_name(name)
        if not key:
            return None

        cached = self._cache.get(key)
        if cached is not None:
            return cached

        group = await self._select(key)
        if group is None:
            group = CompanyGroup(normalized_key=key, name=name.strip())
            try:
                async with self._session.begin_nested():
                    self._session.add(group)
                    await self._session.flush()
            except IntegrityError:
                group = await self._select(key)
                if group is None:  # pragma: no cover
                    raise
        self._cache[key] = group
        return group


class CompanyRepository:
    """Upsert and lookup companies."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._group_repo = CompanyGroupRepository(session)

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

        group = await self._group_repo.get_or_create(name)
        group_id = group.id if group else None

        if company is None:
            company = Company(
                source=source,
                external_id=ext_id,
                name=name,
                group_id=group_id,
            )
            self._session.add(company)
            await self._session.flush()
            log.debug("company.created", source=source, name=name)
        elif group_id is not None and company.group_id != group_id:
            # Backfill / correct the canonical group link
            company.group_id = group_id
            self._session.add(company)

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
            company.updated_at = datetime.now(UTC)
            self._session.add(company)


class CityRepository:
    """Resolve raw location strings to normalised City rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        # Per-instance cache: within one batch the same city repeats hundreds of
        # times, so avoid re-querying it for every vacancy.
        self._cache: dict[str, City] = {}

    async def _select_by_name(self, name_en: str) -> City | None:
        result = await self._session.execute(select(City).where(City.name_en == name_en))
        return result.scalar_one_or_none()

    def _backfill_translation(self, city: City, name_translated: str | None) -> None:
        if name_translated and not city.name_translated:
            city.name_translated = name_translated
            self._session.add(city)

    async def get_or_create(self, name_en: str, name_translated: str | None) -> City:
        """Return the City with this canonical English name, creating if absent.

        If an existing row lacks a translation and one is now known, it is
        backfilled so later scrapes enrich earlier rows. Concurrent scrapers may
        try to insert the same brand-new city at once; the INSERT is wrapped in a
        SAVEPOINT so a unique-constraint race is recovered by re-reading the row
        the other transaction committed, instead of aborting the whole batch.
        """
        cached = self._cache.get(name_en)
        if cached is not None:
            self._backfill_translation(cached, name_translated)
            return cached

        city = await self._select_by_name(name_en)
        if city is None:
            city = City(name_en=name_en, name_translated=name_translated)
            try:
                async with self._session.begin_nested():
                    self._session.add(city)
                    await self._session.flush()
                log.debug("city.created", name_en=name_en)
            except IntegrityError:
                # Lost the race — another transaction inserted it first.
                city = await self._select_by_name(name_en)
                if city is None:  # pragma: no cover - only if the row truly vanished
                    raise

        self._backfill_translation(city, name_translated)
        self._cache[name_en] = city
        return city

    async def resolve(self, raw_location: str | None) -> City | None:
        """Normalise a raw location string and return its City (or None)."""
        normalized = normalize_city(raw_location)
        if normalized is None:
            return None
        name_en, name_translated = normalized
        return await self.get_or_create(name_en, name_translated)


class VacancyRepository:
    """All DB operations for vacancies."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._company_repo = CompanyRepository(session)
        self._city_repo = CityRepository(session)

    async def upsert_vacancy(self, data: VacancyData) -> tuple[str, list[VacancyChange]]:
        """
        Insert or update a vacancy, auto-upserting its company.

        Returns:
            (action, changes) where action is
            'created' | 'updated' | 'unchanged' | 'skipped'
        """
        # ── Integrity guard ────────────────────────────────────────────────
        # A parser bug can yield a blank external_id or a URL that fell back to
        # the site root (no path). A blank/duplicate external_id collapses many
        # distinct vacancies into a single row — every scrape then rewrites it,
        # producing thousands of bogus change records (see the cvonline
        # ""/https://cvonline.lt incident). Drop such items instead of writing
        # them, so one scraper regression can never corrupt the table again and
        # an already-good row is never overwritten with garbage.
        if not (data.external_id or "").strip():
            log.warning("vacancy.skipped_blank_external_id", source=data.source, url=data.url)
            return "skipped", []
        if _is_rootlike_url(data.url):
            log.warning(
                "vacancy.skipped_rootlike_url",
                source=data.source,
                external_id=data.external_id,
                url=data.url,
            )
            return "skipped", []

        # Resolve company FK when a company name is present
        company_id: uuid.UUID | None = None
        if data.company:
            company = await self._company_repo.get_or_create(
                source=data.source,
                name=data.company,
            )
            company_id = company.id

        # Resolve normalised city FK from the raw location string
        city = await self._city_repo.resolve(data.location)
        city_id: int | None = city.id if city else None

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
                city_id=city_id,
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
                first_seen_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
            )
            self._session.add(vacancy)
            await self._session.flush()
            log.debug("vacancy.created", source=data.source, external_id=data.external_id)
            return "created", []

        # Detect field-level changes
        changes: list[VacancyChange] = []
        update_fields: dict[str, Any] = {
            "last_seen_at": datetime.now(UTC),
            "is_active": True,
            # Always refresh non-tracked fields silently
            "page_html": data.page_html,
            "url": data.url,
        }

        # Sync company FK if it changed
        if company_id and existing.company_id != company_id:
            update_fields["company_id"] = company_id

        # Sync normalised city FK if it changed
        if existing.city_id != city_id:
            update_fields["city_id"] = city_id

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
                update(Vacancy).where(Vacancy.id == existing.id).values(**update_fields)
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
                last_seen_at=datetime.now(UTC),
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
                translated_at=datetime.now(UTC),
            )
            self._session.add(translation)
        else:
            translation.title_translated = title_translated
            translation.description_translated = description_translated
            translation.translator = translator
            translation.translated_at = datetime.now(UTC)
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

    async def get_active_for_user(
        self, subscription_id: int, telegram_user_id: int
    ) -> TelegramSubscription | None:
        stmt = select(TelegramSubscription).where(
            TelegramSubscription.id == subscription_id,
            TelegramSubscription.telegram_user_id == telegram_user_id,
            TelegramSubscription.is_active.is_(True),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_active_by_query(
        self, telegram_user_id: int, query: str
    ) -> TelegramSubscription | None:
        """Return an existing active subscription with the same query (dedup)."""
        stmt = select(TelegramSubscription).where(
            TelegramSubscription.telegram_user_id == telegram_user_id,
            TelegramSubscription.is_active.is_(True),
            func.lower(func.trim(TelegramSubscription.query)) == query.strip().lower(),
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

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
        sub.cancelled_at = datetime.now(UTC)
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


class TelegramUserRepository:
    """Store telegram users who interacted with the bot."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_user(
        self,
        *,
        telegram_user_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        language_code: str | None,
        is_bot: bool,
        is_premium: bool | None,
        last_chat_id: int | None,
    ) -> tuple[TelegramUser, bool]:
        """Insert or update a Telegram user.

        Returns ``(user, created)`` where ``created`` is True only on first
        sight of this telegram_user_id — used to fire the new-user admin alert.
        """
        stmt = select(TelegramUser).where(TelegramUser.telegram_user_id == telegram_user_id)
        result = await self._session.execute(stmt)
        user = result.scalar_one_or_none()
        now = datetime.now(UTC)
        if user is None:
            user = TelegramUser(
                telegram_user_id=telegram_user_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                language_code=language_code,
                is_bot=is_bot,
                is_premium=is_premium,
                last_chat_id=last_chat_id,
                last_seen_at=now,
            )
            self._session.add(user)
            await self._session.flush()
            return user, True

        user.username = username
        user.first_name = first_name
        user.last_name = last_name
        user.language_code = language_code
        user.is_bot = is_bot
        user.is_premium = is_premium
        user.last_chat_id = last_chat_id
        user.last_seen_at = now
        self._session.add(user)
        await self._session.flush()
        return user, False

    async def list_last_chat_ids_for_usernames(self, usernames: set[str]) -> list[int]:
        if not usernames:
            return []
        normalized = {u.strip().lstrip("@").lower() for u in usernames if u.strip()}
        if not normalized:
            return []
        stmt = (
            select(TelegramUser.last_chat_id)
            .where(TelegramUser.last_chat_id.is_not(None))
            .where(TelegramUser.username.is_not(None))
            .where(func.lower(TelegramUser.username).in_(tuple(normalized)))
        )
        result = await self._session.execute(stmt)
        return [int(x) for x in result.scalars().all() if x is not None]


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


class VacancySearchRepository:
    """Vacancy search with include/exclude/fuzzy and admin regex support."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _apply_location_filter(stmt: Any, loc: str) -> Any:
        """Match a location term against the raw string and the normalised city.

        Users may type either language ("Vilnius" or "Вильнюс"); we match the
        raw ``location``, both city name columns, and the resolved canonical
        English name so cross-language input still works.
        """
        normalized = normalize_city(loc)
        canonical_en = normalized[0] if normalized else None
        conditions = [
            func.coalesce(Vacancy.location, "").ilike(f"%{loc}%"),
            func.coalesce(City.name_en, "").ilike(f"%{loc}%"),
            func.coalesce(City.name_translated, "").ilike(f"%{loc}%"),
        ]
        if canonical_en:
            conditions.append(City.name_en == canonical_en)
        return stmt.outerjoin(City, City.id == Vacancy.city_id).where(or_(*conditions))

    @staticmethod
    def _apply_company_filter(stmt: Any, term: str) -> Any:
        """Match a company term ignoring case and diacritics.

        The canonical ``company_groups.normalized_key`` is already deaccented +
        casefolded, so normalising the user's input the same way and matching it
        gives diacritic/case-insensitive search. Falls back to the raw
        ``company_name`` (case-insensitive) for rows without a group.
        """
        key = normalize_company_name(term)
        conditions = [func.coalesce(Vacancy.company_name, "").ilike(f"%{term}%")]
        if key:
            conditions.append(func.coalesce(CompanyGroup.normalized_key, "").like(f"%{key}%"))
        return (
            stmt.outerjoin(Company, Company.id == Vacancy.company_id)
            .outerjoin(CompanyGroup, CompanyGroup.id == Company.group_id)
            .where(or_(*conditions))
        )

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
        location: str | None = None,
        company: str | None = None,
        published_from: datetime | None = None,
        published_to: datetime | None = None,
        salary_from: int | None = None,
        salary_to: int | None = None,
    ) -> Sequence[Vacancy]:
        dialect = (
            self._session.bind.dialect.name if self._session.bind is not None else "postgresql"
        )

        if dialect != "postgresql":
            return await self._search_fallback(
                includes=includes,
                excludes=excludes,
                fuzzy=fuzzy,
                regex=regex,
                limit=limit,
                is_admin=is_admin,
                location=location,
                company=company,
                published_from=published_from,
                published_to=published_to,
                salary_from=salary_from,
                salary_to=salary_to,
            )

        translations_subq = (
            select(
                VacancyTranslation.vacancy_id.label("vacancy_id"),
                func.coalesce(
                    func.string_agg(
                        func.concat(
                            func.coalesce(VacancyTranslation.title_translated, ""),
                            literal(" "),
                            self._clean_plain_text_sql(VacancyTranslation.description_translated),
                        ),
                        literal(" "),
                    ),
                    "",
                ).label("translated_text"),
            )
            .group_by(VacancyTranslation.vacancy_id)
            .subquery()
        )

        original_text = func.concat(
            func.coalesce(Vacancy.title, ""),
            literal(" "),
            self._clean_plain_text_sql(Vacancy.description),
        )
        searchable_text = func.concat(
            original_text,
            literal(" "),
            func.coalesce(translations_subq.c.translated_text, ""),
        )

        rank_expr = func.ts_rank_cd(
            func.to_tsvector("simple", searchable_text),
            func.plainto_tsquery("simple", " ".join(includes) if includes else ""),
        )

        stmt = (
            select(Vacancy, rank_expr.label("rank"))
            .outerjoin(translations_subq, translations_subq.c.vacancy_id == Vacancy.id)
            .options(
                selectinload(Vacancy.city),
                selectinload(Vacancy.company_ref).selectinload(Company.group),
            )
            .where(Vacancy.is_active.is_(True))
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
            stmt = stmt.where(searchable_text.ilike(f"%{sql_like}%"))

        if regex and is_admin:
            stmt = stmt.where(searchable_text.op("~*")(regex))

        if location and location.strip():
            stmt = self._apply_location_filter(stmt, location.strip())

        if company and company.strip():
            stmt = self._apply_company_filter(stmt, company.strip())

        if published_from is not None:
            stmt = stmt.where(Vacancy.first_seen_at >= published_from)
        if published_to is not None:
            stmt = stmt.where(Vacancy.first_seen_at <= published_to)

        if salary_from is not None:
            upper_salary = func.coalesce(Vacancy.salary_max, Vacancy.salary_min, 0)
            stmt = stmt.where(upper_salary >= salary_from)
        if salary_to is not None:
            lower_salary = func.coalesce(Vacancy.salary_min, Vacancy.salary_max, 10**9)
            stmt = stmt.where(lower_salary <= salary_to)

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
        location: str | None = None,
        company: str | None = None,
        published_from: datetime | None = None,
        published_to: datetime | None = None,
        salary_from: int | None = None,
        salary_to: int | None = None,
    ) -> Sequence[Vacancy]:
        translated_text = (
            func.coalesce(VacancyTranslation.title_translated, "")
            + literal(" ")
            + func.coalesce(VacancyTranslation.description_translated, "")
        )
        original_text = (
            func.coalesce(Vacancy.title, "") + literal(" ") + func.coalesce(Vacancy.description, "")
        )
        searchable_text = original_text + literal(" ") + translated_text

        stmt = (
            select(Vacancy)
            .outerjoin(VacancyTranslation, VacancyTranslation.vacancy_id == Vacancy.id)
            .options(
                selectinload(Vacancy.city),
                selectinload(Vacancy.company_ref).selectinload(Company.group),
            )
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
        if location and location.strip():
            stmt = self._apply_location_filter(stmt, location.strip())
        if company and company.strip():
            stmt = self._apply_company_filter(stmt, company.strip())
        if published_from is not None:
            stmt = stmt.where(Vacancy.first_seen_at >= published_from)
        if published_to is not None:
            stmt = stmt.where(Vacancy.first_seen_at <= published_to)
        if salary_from is not None:
            upper_salary = func.coalesce(Vacancy.salary_max, Vacancy.salary_min, 0)
            stmt = stmt.where(upper_salary >= salary_from)
        if salary_to is not None:
            lower_salary = func.coalesce(Vacancy.salary_min, Vacancy.salary_max, 10**9)
            stmt = stmt.where(lower_salary <= salary_to)

        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def list_top_locations(self, *, limit: int = 8) -> list[str]:
        # Group by the normalised city and surface its display label
        # (translation preferred, English otherwise) so each place appears once.
        display = func.coalesce(City.name_translated, City.name_en)
        stmt = (
            select(display)
            .select_from(Vacancy)
            .join(City, City.id == Vacancy.city_id)
            .where(Vacancy.is_active.is_(True))
            .group_by(display)
            .order_by(func.count(Vacancy.id).desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [row[0] for row in result.all() if row[0]]

    async def list_top_companies(self, *, limit: int = 8) -> list[str]:
        # Group by the canonical company group so each company appears once,
        # ranked by number of active vacancies.
        stmt = (
            select(CompanyGroup.name)
            .select_from(Vacancy)
            .join(Company, Company.id == Vacancy.company_id)
            .join(CompanyGroup, CompanyGroup.id == Company.group_id)
            .where(Vacancy.is_active.is_(True))
            .group_by(CompanyGroup.name)
            .order_by(func.count(Vacancy.id).desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [row[0] for row in result.all() if row[0]]

    async def list_salary_suggestions(self, *, limit: int = 8) -> list[int]:
        anchor = func.coalesce(Vacancy.salary_min, Vacancy.salary_max)
        stmt = (
            select(anchor)
            .where(
                Vacancy.is_active.is_(True),
                anchor.is_not(None),
            )
            .group_by(anchor)
            .order_by(func.count(Vacancy.id).desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [int(row[0]) for row in result.all() if row[0] is not None]


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
        run.finished_at = datetime.now(UTC)
        run.vacancies_found = vacancies_found
        run.new_count = new_count
        run.changed_count = changed_count
        run.deactivated_count = deactivated_count
        run.error_message = error_message
        self._session.add(run)


class ScheduleRepository:
    """Read and seed admin-managed cron schedules (see orm.Schedule)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> Sequence[Schedule]:
        result = await self._session.execute(select(Schedule).order_by(Schedule.id))
        return result.scalars().all()

    async def get(self, job_id: str) -> Schedule | None:
        result = await self._session.execute(select(Schedule).where(Schedule.job_id == job_id))
        return result.scalar_one_or_none()

    async def seed_missing(self, defaults: Iterable[dict[str, Any]]) -> int:
        """Insert a row for every job_id not yet present. Returns count inserted.

        Called by the scheduler on startup so the table is populated from the
        real .env cron values without clobbering admin edits made later.
        """
        existing = await self._session.execute(select(Schedule.job_id))
        known = set(existing.scalars().all())
        inserted = 0
        for d in defaults:
            if d["job_id"] in known:
                continue
            self._session.add(
                Schedule(
                    job_id=d["job_id"],
                    name=d["name"],
                    cron=d["cron"],
                    enabled=d.get("enabled", True),
                )
            )
            inserted += 1
        await self._session.flush()
        return inserted

    async def clear_run_now(self, job_id: str) -> None:
        """Reset the run-now flag after the scheduler has handled it."""
        await self._session.execute(
            update(Schedule).where(Schedule.job_id == job_id).values(run_now_requested_at=None)
        )

    async def set_enabled(self, job_id: str, enabled: bool) -> None:
        """Toggle a job on/off (no-op if the row does not exist)."""
        await self._session.execute(
            update(Schedule).where(Schedule.job_id == job_id).values(enabled=enabled)
        )


class StatsRepository:
    """Aggregate counters for the daily admin health report."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def daily_report(self, since: datetime) -> dict[str, Any]:
        """Collect added/translated/total counters and per-source run health.

        ``since`` bounds the "new" window (typically now − 24h).
        """
        # New vacancies added in the window, per source
        new_rows = await self._session.execute(
            select(Vacancy.source, func.count(Vacancy.id))
            .where(Vacancy.first_seen_at >= since)
            .group_by(Vacancy.source)
        )
        new_by_source = {src: int(cnt) for src, cnt in new_rows.all()}

        # Overall totals per source (all-time + active)
        total_rows = await self._session.execute(
            select(
                Vacancy.source,
                func.count(Vacancy.id),
                func.count(Vacancy.id).filter(Vacancy.is_active.is_(True)),
            ).group_by(Vacancy.source)
        )
        total_by_source: dict[str, tuple[int, int]] = {
            src: (int(total), int(active)) for src, total, active in total_rows.all()
        }

        # Translations
        translated_since = int(
            (
                await self._session.execute(
                    select(func.count(VacancyTranslation.id)).where(
                        VacancyTranslation.translated_at >= since
                    )
                )
            ).scalar_one()
        )
        translated_total = int(
            (await self._session.execute(select(func.count(VacancyTranslation.id)))).scalar_one()
        )

        # Per-source scrape-run health within the window
        run_rows = await self._session.execute(
            select(
                ScrapeRun.source,
                func.count(ScrapeRun.id).filter(ScrapeRun.status == "success"),
                func.count(ScrapeRun.id).filter(ScrapeRun.status == "failed"),
                func.coalesce(func.sum(ScrapeRun.new_count), 0),
            )
            .where(ScrapeRun.started_at >= since)
            .group_by(ScrapeRun.source)
        )
        runs_by_source = {
            src: {"success": int(ok), "failed": int(failed), "new_count": int(new_count)}
            for src, ok, failed, new_count in run_rows.all()
        }

        new_total = sum(new_by_source.values())
        total_vacancies = sum(t for t, _ in total_by_source.values())
        active_vacancies = sum(a for _, a in total_by_source.values())

        return {
            "since": since,
            "new_by_source": new_by_source,
            "new_total": new_total,
            "total_by_source": total_by_source,
            "total_vacancies": total_vacancies,
            "active_vacancies": active_vacancies,
            "translated_since": translated_since,
            "translated_total": translated_total,
            "runs_by_source": runs_by_source,
        }


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

# Scheme + host with at most a trailing slash and no path/query — the signature
# of a scraper that lost the vacancy href and fell back to the site root.
_ROOTLIKE_URL_RE = re.compile(r"^https?://[^/]+/?$", re.IGNORECASE)


def _is_rootlike_url(url: str | None) -> bool:
    return bool(url) and _ROOTLIKE_URL_RE.match(url.strip()) is not None


def _synthetic_company_id(source: str, name: str) -> str:
    """Stable synthetic external_id for companies without a site-assigned ID."""
    import hashlib

    return hashlib.md5(f"{source}::{name}".encode()).hexdigest()
