"""Daily admin health report + shared admin-alert helpers.

Runs once a day from the scheduler: aggregates how many vacancies were added
(per source and total) and translated in the last window, and alerts admins if
no new vacancies appeared — a strong signal a scraper broke.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from src.config import settings
from src.db.engine import get_session
from src.db.repository import StatsRepository
from src.logger import get_logger

log = get_logger(__name__)

# Fixed order so the report reads the same every day, even for silent sources.
_SOURCES = ("cvbankas", "cvonline", "cvmarket", "cv")


def format_daily_report(stats: dict[str, Any], *, stale_hours: int) -> str:
    """Render the stats dict into a plain-text admin message."""
    new_by_source: dict[str, int] = stats["new_by_source"]
    total_by_source: dict[str, tuple[int, int]] = stats["total_by_source"]
    runs_by_source: dict[str, dict[str, int]] = stats["runs_by_source"]
    new_total: int = stats["new_total"]

    lines: list[str] = []
    if new_total == 0:
        lines.append(f"⚠️ За последние {stale_hours}ч НЕ добавлено ни одной новой вакансии!")
    else:
        lines.append(f"📊 Ежедневный отчёт (за {stale_hours}ч)")
    lines.append("")

    # Per-source added, with run counts and a warning marker for silent/failed scrapers
    lines.append("Добавлено вакансий:")
    known = set(_SOURCES)
    ordered = list(_SOURCES) + [s for s in sorted(new_by_source) if s not in known]
    for src in ordered:
        added = new_by_source.get(src, 0)
        run = runs_by_source.get(src, {})
        ok = run.get("success", 0)
        failed = run.get("failed", 0)
        total_runs = ok + failed
        marker = ""
        if added == 0:
            marker = "  ⚠️ нет новых"
        if failed:
            marker += f"  ❗ с ошибкой: {failed}"
        elif total_runs == 0 and src in known:
            marker += "  ❗ не было прогонов"
        lines.append(f"  • {src}: +{added} (прогонов: {total_runs}){marker}")
    lines.append(f"  Всего добавлено: +{new_total}")
    lines.append("")

    # Translations
    lines.append(
        f"Переведено за период: {stats['translated_since']} "
        f"(всего переводов: {stats['translated_total']})"
    )
    lines.append("")

    # Overall totals
    lines.append(
        f"Всего вакансий в базе: {stats['total_vacancies']} "
        f"(активных: {stats['active_vacancies']})"
    )
    for src in ordered:
        if src in total_by_source:
            total, active = total_by_source[src]
            lines.append(f"  • {src}: {active} активных / {total} всего")

    return "\n".join(lines)


async def build_daily_report() -> tuple[str, dict[str, Any]]:
    """Aggregate stats and render the daily report text — without sending it.

    Shared by the scheduled job and the "Админ: статистика" button so both
    produce an identical report.
    """
    stale_hours = settings.daily_report_stale_hours
    since = datetime.now(UTC) - timedelta(hours=stale_hours)

    async with get_session() as session:
        stats = await StatsRepository(session).daily_report(since)

    report = format_daily_report(stats, stale_hours=stale_hours)
    log.info(
        "daily_report.built",
        new_total=stats["new_total"],
        translated_since=stats["translated_since"],
        active=stats["active_vacancies"],
    )
    return report, stats


async def run_daily_admin_report() -> dict[str, Any]:
    """Build the daily report and push it to admins. Safe to run manually."""
    report, stats = await build_daily_report()

    if not settings.telegram_bot_token:
        log.warning("daily_report.skipped_send", reason="TELEGRAM_BOT_TOKEN not configured")
        return {"sent_to_admins": 0, "report": report, **stats}

    # Instantiate the bot client only to send (mirrors subscription_notifier).
    from src.services.telegram_bot import TelegramBotService

    bot = TelegramBotService()
    try:
        sent = await bot.notify_admins(report)
    finally:
        await bot.close()
    log.info("daily_report.sent", admins_notified=sent, stale_alert=stats["new_total"] == 0)
    return {"sent_to_admins": sent, "report": report, **stats}
