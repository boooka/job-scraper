from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from src.db.repository import (
    CityRepository,
    StatsRepository,
    TelegramDeliveryRepository,
    TelegramSubscriptionRepository,
    TelegramUserRepository,
    TranslationRepository,
    VacancyRepository,
    VacancySearchRepository,
)
from src.models.orm import TelegramUser, Vacancy, VacancyTranslation
from src.models.schemas import VacancyData


def _vacancy(**kwargs) -> VacancyData:
    base = dict(
        source="cvbankas",
        external_id="v1",
        title="Python developer",
        company="Acme",
        location="Vilnius",
        url="https://example.com/v1",
        description="<p>FastAPI and PostgreSQL</p>",
    )
    return VacancyData(**(base | kwargs))


@pytest.mark.asyncio
async def test_search_matches_original_html_and_translations(db_session):
    v_repo = VacancyRepository(db_session)
    action, _ = await v_repo.upsert_vacancy(_vacancy())
    assert action == "created"

    vacancy = (
        await db_session.execute(select(Vacancy).where(Vacancy.external_id == "v1"))
    ).scalar_one()
    t_repo = TranslationRepository(db_session)
    await t_repo.upsert(
        vacancy_id=vacancy.id,
        language="RU",
        title_translated="Разработчик Python",
        description_translated="Описание вакансии на русском",
        translator="test",
    )

    s_repo = VacancySearchRepository(db_session)

    # Match by HTML-stripped description text
    by_original = await s_repo.search(
        includes=["PostgreSQL"],
        excludes=[],
        fuzzy=[],
        regex=None,
        language="RU",
        limit=10,
        is_admin=False,
    )
    assert any(v.external_id == "v1" for v in by_original)

    # Match by translation text
    by_translation = await s_repo.search(
        includes=["русском"],
        excludes=[],
        fuzzy=[],
        regex=None,
        language="RU",
        limit=10,
        is_admin=False,
    )
    assert any(v.external_id == "v1" for v in by_translation)


@pytest.mark.asyncio
async def test_subscription_delivery_dedup(db_session):
    s_repo = TelegramSubscriptionRepository(db_session)
    sub = await s_repo.add(
        telegram_user_id=1,
        username="tester",
        chat_id=1001,
        query="python",
    )

    v_repo = VacancyRepository(db_session)
    await v_repo.upsert_vacancy(_vacancy(external_id="v2", url="https://example.com/v2"))
    vacancy = (
        await db_session.execute(select(Vacancy).where(Vacancy.external_id == "v2"))
    ).scalar_one()

    d_repo = TelegramDeliveryRepository(db_session)
    assert await d_repo.was_sent(sub.id, vacancy.id) is False
    await d_repo.mark_sent(sub.id, vacancy.id)
    assert await d_repo.was_sent(sub.id, vacancy.id) is True
    # second mark is idempotent
    await d_repo.mark_sent(sub.id, vacancy.id)


@pytest.mark.asyncio
async def test_unsubscribe_marks_inactive_and_keeps_row(db_session):
    repo = TelegramSubscriptionRepository(db_session)
    sub = await repo.add(
        telegram_user_id=42,
        username="tester",
        chat_id=1001,
        query="python",
    )
    ok = await repo.cancel_for_user(sub.id, 42)
    assert ok is True

    row = (await db_session.execute(select(type(sub)).where(type(sub).id == sub.id))).scalar_one()
    assert row.is_active is False
    assert row.cancelled_at is not None
    active_rows = await repo.list_active_for_user(42)
    assert active_rows == []


@pytest.mark.asyncio
async def test_subscription_dedup_and_get_for_user(db_session):
    repo = TelegramSubscriptionRepository(db_session)
    sub = await repo.add(telegram_user_id=7, username="u", chat_id=70, query="  Python Dev ")

    # Same query (case/space-insensitive) is found as an existing active sub
    dup = await repo.find_active_by_query(7, "python dev")
    assert dup is not None and dup.id == sub.id
    # A different query is not matched
    assert await repo.find_active_by_query(7, "java") is None
    # Ownership-scoped fetch
    assert (await repo.get_active_for_user(sub.id, 7)) is not None
    assert (await repo.get_active_for_user(sub.id, 999)) is None

    # After cancellation it is no longer found
    await repo.cancel_for_user(sub.id, 7)
    assert await repo.find_active_by_query(7, "python dev") is None


@pytest.mark.asyncio
async def test_telegram_user_upsert_tracks_last_seen(db_session):
    repo = TelegramUserRepository(db_session)
    _, created_first = await repo.upsert_user(
        telegram_user_id=123456789,
        username="test_user",
        first_name="Test",
        last_name="User",
        language_code="ru",
        is_bot=False,
        is_premium=True,
        last_chat_id=123,
    )
    _, created_second = await repo.upsert_user(
        telegram_user_id=123456789,
        username="test_user_updated",
        first_name="Test",
        last_name="User",
        language_code="ru",
        is_bot=False,
        is_premium=True,
        last_chat_id=321,
    )
    # First sight reports created=True, subsequent upserts created=False
    assert created_first is True
    assert created_second is False
    users = (await db_session.execute(select(TelegramUser))).scalars().all()
    assert len(users) == 1
    assert users[0].username == "test_user_updated"
    assert users[0].last_chat_id == 321


@pytest.mark.asyncio
async def test_daily_report_counts_and_stale_flag(db_session):
    v_repo = VacancyRepository(db_session)
    # Two fresh vacancies from different sources
    await v_repo.upsert_vacancy(
        _vacancy(source="cvbankas", external_id="d1", url="https://example.com/d1")
    )
    await v_repo.upsert_vacancy(
        _vacancy(source="cv", external_id="d2", url="https://example.com/d2")
    )

    stats_repo = StatsRepository(db_session)
    since = datetime.now(UTC) - timedelta(hours=24)
    report = await stats_repo.daily_report(since)

    assert report["new_total"] == 2
    assert report["new_by_source"].get("cvbankas") == 1
    assert report["new_by_source"].get("cv") == 1
    assert report["active_vacancies"] == 2

    # Nothing added in the far future window → stale
    future = datetime.now(UTC) + timedelta(hours=1)
    stale = await stats_repo.daily_report(future)
    assert stale["new_total"] == 0

    from src.services.admin_notifier import format_daily_report

    text = format_daily_report(stale, stale_hours=24)
    assert "НЕ добавлено" in text


@pytest.mark.asyncio
async def test_upsert_skips_blank_external_id_and_rootlike_url(db_session):
    v_repo = VacancyRepository(db_session)

    # Blank external_id would collapse many vacancies into one row → skip
    action, changes = await v_repo.upsert_vacancy(
        _vacancy(external_id="", url="https://cvonline.lt/vacancy/1/x")
    )
    assert action == "skipped"
    assert changes == []

    # URL that fell back to the site root (no path) → skip
    action, _ = await v_repo.upsert_vacancy(_vacancy(external_id="42", url="https://cvonline.lt"))
    assert action == "skipped"

    # Nothing was written for either
    rows = (await db_session.execute(select(Vacancy))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_rootlike_url_does_not_overwrite_existing_good_row(db_session):
    v_repo = VacancyRepository(db_session)
    action, _ = await v_repo.upsert_vacancy(
        _vacancy(
            external_id="777",
            title="Good title",
            url="https://cvonline.lt/vacancy/777/company/good",
        )
    )
    assert action == "created"

    # A later mis-parse of the same id with a root URL must not clobber it
    action, _ = await v_repo.upsert_vacancy(
        _vacancy(external_id="777", title="Broken", url="https://cvonline.lt")
    )
    assert action == "skipped"

    row = (
        await db_session.execute(select(Vacancy).where(Vacancy.external_id == "777"))
    ).scalar_one()
    assert row.title == "Good title"
    assert row.url == "https://cvonline.lt/vacancy/777/company/good"


@pytest.mark.asyncio
async def test_city_get_or_create_is_idempotent_and_cached(db_session):
    from src.models.orm import City

    repo = CityRepository(db_session)
    first = await repo.get_or_create("Vilnius", None)
    # Second call within the same repo returns the cached instance, no dup row
    second = await repo.get_or_create("Vilnius", "Вильнюс")
    assert first is second
    # Translation is backfilled onto the existing row
    assert second.name_translated == "Вильнюс"

    cities = (
        (await db_session.execute(select(City).where(City.name_en == "Vilnius"))).scalars().all()
    )
    assert len(cities) == 1

    # A fresh repo (new batch) must also find the existing row, not insert again
    repo2 = CityRepository(db_session)
    again = await repo2.get_or_create("Vilnius", None)
    assert again.id == first.id
    cities = (
        (await db_session.execute(select(City).where(City.name_en == "Vilnius"))).scalars().all()
    )
    assert len(cities) == 1


@pytest.mark.asyncio
async def test_search_covers_all_languages(db_session):
    v_repo = VacancyRepository(db_session)
    await v_repo.upsert_vacancy(_vacancy(external_id="v3", url="https://example.com/v3"))
    vacancy = (
        await db_session.execute(select(Vacancy).where(Vacancy.external_id == "v3"))
    ).scalar_one()

    db_session.add(
        VacancyTranslation(
            vacancy_id=vacancy.id,
            language="EN",
            title_translated="Backend engineer",
            description_translated="Data pipelines and jobs",
            translator="test",
        )
    )
    db_session.add(
        VacancyTranslation(
            vacancy_id=vacancy.id,
            language="DE",
            title_translated="Dateningenieur",
            description_translated="Batch Verarbeitung",
            translator="test",
        )
    )
    await db_session.flush()

    s_repo = VacancySearchRepository(db_session)
    rows = await s_repo.search(
        includes=["Dateningenieur"],
        excludes=[],
        fuzzy=[],
        regex=None,
        language="RU",
        limit=10,
        is_admin=False,
    )
    assert any(v.external_id == "v3" for v in rows)


@pytest.mark.asyncio
async def test_search_with_location_date_salary_filters(db_session):
    v_repo = VacancyRepository(db_session)
    await v_repo.upsert_vacancy(
        _vacancy(
            external_id="v4",
            location="Vilnius",
            salary_min=2000,
            salary_max=3000,
            url="https://example.com/v4",
        )
    )
    await v_repo.upsert_vacancy(
        _vacancy(
            external_id="v5",
            location="Kaunas",
            salary_min=1200,
            salary_max=1500,
            url="https://example.com/v5",
        )
    )
    old_vacancy = (
        await db_session.execute(select(Vacancy).where(Vacancy.external_id == "v5"))
    ).scalar_one()
    old_vacancy.first_seen_at = datetime.now(UTC) - timedelta(days=20)
    await db_session.flush()

    s_repo = VacancySearchRepository(db_session)
    rows = await s_repo.search(
        includes=["Python"],
        excludes=[],
        fuzzy=[],
        regex=None,
        language="RU",
        limit=20,
        is_admin=False,
        location="Vilnius",
        published_from=datetime.now(UTC) - timedelta(days=7),
        salary_from=1800,
        salary_to=3500,
    )
    assert [v.external_id for v in rows] == ["v4"]


@pytest.mark.asyncio
async def test_location_and_salary_suggestions_from_db(db_session):
    v_repo = VacancyRepository(db_session)
    await v_repo.upsert_vacancy(
        _vacancy(
            external_id="v6",
            location="Vilnius",
            salary_min=2000,
            salary_max=2500,
            url="https://example.com/v6",
        )
    )
    await v_repo.upsert_vacancy(
        _vacancy(
            external_id="v7",
            location="Kaunas",
            salary_min=1800,
            salary_max=2200,
            url="https://example.com/v7",
        )
    )
    s_repo = VacancySearchRepository(db_session)
    locations = await s_repo.list_top_locations(limit=5)
    salaries = await s_repo.list_salary_suggestions(limit=5)
    # Locations are normalised to a city and shown with the RU translation
    # ("Vilnius" → "Вильнюс") preferred over the English name.
    assert "Вильнюс" in locations
    assert "Каунас" in locations
    assert any(v in salaries for v in (1800, 2000))


@pytest.mark.asyncio
async def test_city_normalisation_unifies_spellings(db_session):
    """ "Vilnius" (LT) and "Вильнюс" (RU) resolve to one city; display uses RU."""
    from sqlalchemy.orm import selectinload

    from src.models.orm import City

    v_repo = VacancyRepository(db_session)
    await v_repo.upsert_vacancy(
        _vacancy(external_id="lt", location="Vilnius", url="https://example.com/lt")
    )
    await v_repo.upsert_vacancy(
        _vacancy(external_id="ru", location="Вильнюс", url="https://example.com/ru")
    )

    cities = (await db_session.execute(select(City))).scalars().all()
    vilnius = [c for c in cities if c.name_en == "Vilnius"]
    assert len(vilnius) == 1
    assert vilnius[0].name_translated == "Вильнюс"

    rows = (
        (
            await db_session.execute(
                select(Vacancy)
                .options(selectinload(Vacancy.city))
                .where(Vacancy.external_id.in_(("lt", "ru")))
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    assert {r.city_id for r in rows} == {vilnius[0].id}
    # Display prefers the translation regardless of the scraped spelling
    assert all(r.display_location == "Вильнюс" for r in rows)


@pytest.mark.asyncio
async def test_city_search_filter_cross_language(db_session):
    """A location filter typed in either language matches the same vacancies."""
    v_repo = VacancyRepository(db_session)
    await v_repo.upsert_vacancy(
        _vacancy(
            external_id="c1", location="Kaunas", title="Rust engineer", url="https://example.com/c1"
        )
    )
    s_repo = VacancySearchRepository(db_session)

    for term in ("Kaunas", "Каунас"):
        rows = await s_repo.search(
            includes=[],
            excludes=[],
            fuzzy=[],
            regex=None,
            language="RU",
            limit=10,
            is_admin=False,
            location=term,
        )
        assert any(r.external_id == "c1" for r in rows), term
