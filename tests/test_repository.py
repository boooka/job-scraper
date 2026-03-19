"""Tests for VacancyRepository — upsert and change tracking."""
from __future__ import annotations

import pytest

from src.db.repository import VacancyRepository
from src.models.orm import Vacancy, VacancyChange
from src.models.schemas import VacancyData


def _make_vacancy(**kwargs) -> VacancyData:
    defaults = dict(
        source="cvbankas",
        external_id="12345",
        title="Python Developer",
        company="Acme Ltd",
        location="Vilnius",
        url="https://en.cvbankas.lt/12345",
        salary_min=2000,
        salary_max=3000,
        salary_currency="EUR",
        salary_period="month",
    )
    return VacancyData(**(defaults | kwargs))


@pytest.mark.asyncio
async def test_upsert_creates_new_vacancy(db_session):
    repo = VacancyRepository(db_session)
    action, changes = await repo.upsert_vacancy(_make_vacancy())

    assert action == "created"
    assert changes == []

    from sqlalchemy import select
    result = await db_session.execute(
        select(Vacancy).where(Vacancy.external_id == "12345")
    )
    v = result.scalar_one()
    assert v.title == "Python Developer"
    assert v.salary_min == 2000


@pytest.mark.asyncio
async def test_upsert_unchanged_returns_unchanged(db_session):
    repo = VacancyRepository(db_session)
    data = _make_vacancy()

    await repo.upsert_vacancy(data)
    action, changes = await repo.upsert_vacancy(data)

    assert action == "unchanged"
    assert changes == []


@pytest.mark.asyncio
async def test_upsert_detects_salary_change(db_session):
    repo = VacancyRepository(db_session)
    await repo.upsert_vacancy(_make_vacancy(salary_min=2000, salary_max=3000))

    action, changes = await repo.upsert_vacancy(
        _make_vacancy(salary_min=2500, salary_max=3500)
    )

    assert action == "updated"
    field_names = {c.field_name for c in changes}
    assert "salary_min" in field_names
    assert "salary_max" in field_names


@pytest.mark.asyncio
async def test_upsert_detects_title_change(db_session):
    repo = VacancyRepository(db_session)
    await repo.upsert_vacancy(_make_vacancy(title="Old Title"))
    action, changes = await repo.upsert_vacancy(_make_vacancy(title="New Title"))

    assert action == "updated"
    assert any(c.field_name == "title" and c.old_value == "Old Title" for c in changes)


@pytest.mark.asyncio
async def test_deactivate_missing(db_session):
    repo = VacancyRepository(db_session)
    await repo.upsert_vacancy(_make_vacancy(external_id="aaa"))
    await repo.upsert_vacancy(_make_vacancy(external_id="bbb"))
    await repo.upsert_vacancy(_make_vacancy(external_id="ccc"))

    # Only "aaa" seen in new scrape
    count = await repo.deactivate_missing("cvbankas", {"aaa"})
    assert count == 2

    from sqlalchemy import select
    result = await db_session.execute(
        select(Vacancy).where(Vacancy.external_id == "bbb")
    )
    v = result.scalar_one()
    assert v.is_active is False


@pytest.mark.asyncio
async def test_vacancy_change_record_saved(db_session):
    repo = VacancyRepository(db_session)
    await repo.upsert_vacancy(_make_vacancy(company="Old Corp"))
    await repo.upsert_vacancy(_make_vacancy(company="New Corp"))

    from sqlalchemy import select
    result = await db_session.execute(
        select(VacancyChange).where(VacancyChange.field_name == "company_name")
    )
    change = result.scalar_one()
    assert change.old_value == "Old Corp"
    assert change.new_value == "New Corp"
