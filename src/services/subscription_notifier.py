"""Send new vacancies to Telegram subscribers with dedup delivery log."""

from __future__ import annotations

from src.config import settings
from src.db.engine import get_session
from src.db.repository import (
    TelegramDeliveryRepository,
    TelegramSubscriptionRepository,
    VacancySearchRepository,
)
from src.logger import get_logger
from src.services.metrics import metrics_registry
from src.services.search_query import parse_search_query

log = get_logger(__name__)
_last_run_stats: dict[str, int] = {"subscriptions": 0, "sent": 0, "skipped": 0, "errors": 0}


def get_last_notification_stats() -> dict[str, int]:
    return dict(_last_run_stats)


async def run_subscription_notifications() -> dict[str, int]:
    """
    Send only new (not yet delivered) matches to all active subscriptions.
    """
    if not settings.telegram_bot_token:
        log.warning(
            "subscription_notifications.skipped", reason="TELEGRAM_BOT_TOKEN not configured"
        )
        return {"subscriptions": 0, "sent": 0, "errors": 0}

    sent = 0
    skipped = 0
    errors = 0
    subs: list = []
    from src.services.telegram_bot import TelegramBotService

    bot = TelegramBotService()

    try:
        async with get_session() as session:
            sub_repo = TelegramSubscriptionRepository(session)
            search_repo = VacancySearchRepository(session)
            delivery_repo = TelegramDeliveryRepository(session)

            subs = await sub_repo.list_all_active()
            for sub in subs:
                parsed = parse_search_query(sub.query)
                try:
                    rows = await search_repo.search(
                        includes=parsed.includes,
                        excludes=parsed.excludes,
                        fuzzy=parsed.fuzzy,
                        regex=parsed.regex,
                        language=settings.deepl_target_lang,
                        limit=settings.telegram_search_limit,
                        is_admin=False,
                    )
                    for row in rows:
                        if await delivery_repo.was_sent(sub.id, row.id):
                            skipped += 1
                            metrics_registry.incr("notifications_skipped")
                            continue
                        company = row.display_company or "Unknown company"
                        location = row.display_location or "Unknown location"
                        text = (
                            f"Новая вакансия по подписке #{sub.id}\n"
                            f"{row.title}\n{company} | {location}\n{row.url}"
                        )
                        await bot.send_message_public(sub.chat_id, text)
                        await delivery_repo.mark_sent(sub.id, row.id)
                        sent += 1
                        metrics_registry.incr("notifications_sent")
                except Exception as exc:
                    errors += 1
                    metrics_registry.incr("notifications_errors")
                    log.error(
                        "subscription_notifications.failed",
                        subscription_id=sub.id,
                        error=str(exc),
                    )
    finally:
        await bot.close()

    result = {"subscriptions": len(subs), "sent": sent, "skipped": skipped, "errors": errors}
    _last_run_stats.update(result)
    log.info("subscription_notifications.complete", **result)
    return result
