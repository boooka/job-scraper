from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from src.db.repository import (
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

    row = (
        await db_session.execute(select(type(sub)).where(type(sub).id == sub.id))
    ).scalar_one()
    assert row.is_active is False
    assert row.cancelled_at is not None
    active_rows = await repo.list_active_for_user(42)
    assert active_rows == []


@pytest.mark.asyncio
async def test_telegram_user_upsert_tracks_last_seen(db_session):
    repo = TelegramUserRepository(db_session)
    await repo.upsert_user(
        telegram_user_id=123456789,
        username="test_user",
        first_name="Test",
        last_name="User",
        language_code="ru",
        is_bot=False,
        is_premium=True,
        last_chat_id=123,
    )
    await repo.upsert_user(
        telegram_user_id=123456789,
        username="test_user_updated",
        first_name="Test",
        last_name="User",
        language_code="ru",
        is_bot=False,
        is_premium=True,
        last_chat_id=321,
    )
    users = (await db_session.execute(select(TelegramUser))).scalars().all()
    assert len(users) == 1
    assert users[0].username == "test_user_updated"
    assert users[0].last_chat_id == 321


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
    old_vacancy.first_seen_at = datetime.now(timezone.utc) - timedelta(days=20)
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
        published_from=datetime.now(timezone.utc) - timedelta(days=7),
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
    assert "Vilnius" in locations
    assert "Kaunas" in locations
    assert any(v in salaries for v in (1800, 2000))
