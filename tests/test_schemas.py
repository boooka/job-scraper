"""Tests for Pydantic schemas and salary parsing."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models.schemas import VacancyData
from src.scrapers.base import BaseScraper


class _DummyScraper(BaseScraper):
    source = "test"

    async def scrape_all(self):
        return
        yield  # make it a generator


@pytest.mark.parametrize(
    "raw, expected_min, expected_max, expected_currency",
    [
        ("2000 - 3000 €", 2000, 3000, "EUR"),
        ("€1500", 1500, None, "EUR"),
        ("1 200 – 1 800 EUR", 1200, 1800, "EUR"),
        ("nuo 800 €", 800, None, "EUR"),
        (None, None, None, None),
        ("", None, None, None),
        ("3000-2000 €", 2000, 3000, "EUR"),  # swapped range
    ],
)
def test_parse_salary(raw, expected_min, expected_max, expected_currency):
    s = _DummyScraper.__new__(_DummyScraper)
    lo, hi, cur = s.parse_salary(raw)
    assert lo == expected_min
    assert hi == expected_max
    assert cur == expected_currency


def test_vacancy_data_strips_whitespace():
    v = VacancyData(
        source="cvbankas",
        external_id="1",
        title="  Dev  ",
        company="  Corp  ",
        location="  Vilnius  ",
        url="https://example.com",
    )
    assert v.title == "Dev"
    assert v.company == "Corp"
    assert v.location == "Vilnius"


def test_vacancy_data_salary_range_swap():
    v = VacancyData(
        source="cvbankas",
        external_id="1",
        title="Dev",
        url="https://example.com",
        salary_min=3000,
        salary_max=1000,
    )
    assert v.salary_min == 1000
    assert v.salary_max == 3000


def test_vacancy_data_external_id_coerced_to_str():
    v = VacancyData(
        source="cvbankas",
        external_id=99999,  # type: ignore[arg-type]
        title="Dev",
        url="https://example.com",
    )
    assert isinstance(v.external_id, str)
    assert v.external_id == "99999"


def test_to_comparable_dict_excludes_metadata():
    v = VacancyData(
        source="cvbankas",
        external_id="1",
        title="Dev",
        url="https://example.com",
    )
    d = v.to_comparable_dict()
    assert "source" not in d
    assert "external_id" not in d
    assert "url" not in d
    assert "title" in d
