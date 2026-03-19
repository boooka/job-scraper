from __future__ import annotations

import pytest
from sqlalchemy import select

from src.db.repository import (
    TelegramDeliveryRepository,
    TelegramSubscriptionRepository,
    TranslationRepository,
    VacancyRepository,
    VacancySearchRepository,
)
from src.models.orm import Vacancy, VacancyTranslation
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
