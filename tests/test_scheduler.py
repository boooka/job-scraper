"""Tests for admin-managed schedules: seed helpers and the repository."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.db.repository import ScheduleRepository
from src.scheduler import _JOB_REGISTRY, _default_schedules, _gate_ok, _parse_cron


def test_default_schedules_cover_registry():
    defaults = _default_schedules()
    assert {d["job_id"] for d in defaults} == set(_JOB_REGISTRY)
    # every seed row carries a non-empty cron and a name
    assert all(d["cron"] and d["name"] for d in defaults)


def test_parse_cron_valid():
    trigger = _parse_cron("0 */4 * * *")
    # CronTrigger stringifies its fields — check the expression mapped correctly
    text = repr(trigger)
    assert "minute='0'" in text
    assert "hour='*/4'" in text


def test_parse_cron_rejects_wrong_field_count():
    with pytest.raises(ValueError):
        _parse_cron("0 0 0")  # only 3 fields


def test_gate_ok_ungated_is_always_true():
    assert _gate_ok(None) is True


def test_gate_ok_respects_settings(monkeypatch):
    from src import scheduler as sched

    monkeypatch.setattr(sched.settings, "deepl_api_key", "", raising=False)
    monkeypatch.setattr(sched.settings, "telegram_bot_token", "", raising=False)
    assert _gate_ok("deepl") is False
    assert _gate_ok("telegram") is False

    monkeypatch.setattr(sched.settings, "deepl_api_key", "key", raising=False)
    monkeypatch.setattr(sched.settings, "telegram_bot_token", "tok", raising=False)
    assert _gate_ok("deepl") is True
    assert _gate_ok("telegram") is True


@pytest.mark.asyncio
async def test_seed_missing_is_idempotent(db_session):
    repo = ScheduleRepository(db_session)
    defaults = [
        {"job_id": "cvbankas", "name": "CVBankas scraper", "cron": "0 */4 * * *", "enabled": True},
    ]
    assert await repo.seed_missing(defaults) == 1
    assert await repo.seed_missing(defaults) == 0  # already present, no dupes
    rows = await repo.list_all()
    assert len(rows) == 1
    assert rows[0].job_id == "cvbankas"


@pytest.mark.asyncio
async def test_clear_run_now(db_session):
    repo = ScheduleRepository(db_session)
    await repo.seed_missing(
        [{"job_id": "cv", "name": "CV scraper", "cron": "0 1 * * *", "enabled": True}]
    )
    rows = await repo.list_all()
    rows[0].run_now_requested_at = datetime.now(UTC)
    await db_session.flush()

    await repo.clear_run_now("cv")
    rows = await repo.list_all()
    assert rows[0].run_now_requested_at is None
