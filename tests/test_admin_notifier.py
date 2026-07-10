"""Tests for the daily admin report rendering (pure, no DB)."""

from __future__ import annotations

from src.services.admin_notifier import format_daily_report

_BASE_STATS = {
    "new_by_source": {"cvbankas": 5},
    "total_by_source": {"cvbankas": (100, 90)},
    "runs_by_source": {"cvbankas": {"success": 1, "failed": 0}},
    "new_total": 5,
    "translated_since": 10,
    "translated_total": 1000,
    "total_vacancies": 100,
    "active_vacancies": 90,
}


def test_report_warns_when_translations_disabled():
    stats = {
        **_BASE_STATS,
        "deepl": {
            "configured": 2,
            "translations_enabled": False,
            "keys": [
                {"key": "abc123…xyz", "count": 500000, "limit": 500000, "exhausted": True},
                {"key": "def456…uvw", "count": 500000, "limit": 500000, "exhausted": True},
            ],
        },
    }
    text = format_daily_report(stats, stale_hours=24)
    assert "Переводы ОТКЛЮЧЕНЫ" in text
    assert "исчерпан" in text


def test_report_shows_key_usage_without_warning_when_enabled():
    stats = {
        **_BASE_STATS,
        "deepl": {
            "configured": 1,
            "translations_enabled": True,
            "keys": [{"key": "abc123…xyz", "count": 120000, "limit": 500000, "exhausted": False}],
        },
    }
    text = format_daily_report(stats, stale_hours=24)
    assert "Переводы ОТКЛЮЧЕНЫ" not in text
    assert "осталось 380000" in text


def test_report_omits_deepl_section_when_not_configured():
    text = format_daily_report(
        {**_BASE_STATS, "deepl": {"configured": 0, "keys": []}}, stale_hours=24
    )
    assert "DeepL" not in text
