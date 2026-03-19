"""Telegram bot for vacancy search and subscription management."""
from __future__ import annotations

import asyncio
import random
import re
import time
from typing import Any

import httpx

from src.config import settings
from src.db.engine import get_session
from src.db.repository import TelegramSubscriptionRepository, VacancySearchRepository
from src.logger import get_logger
from src.services.metrics import metrics_registry
from src.services.telegram_messages import TelegramMessages
from src.services.search_query import parse_search_query
from src.services.subscription_notifier import get_last_notification_stats

log = get_logger(__name__)
RETRYABLE_HTTP_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}


class TelegramBotService:
    def __init__(self) -> None:
        if not settings.telegram_bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

        self._token = settings.telegram_bot_token
        self._base_url = f"https://api.telegram.org/bot{self._token}"
        self._offset = 0
        self._admin_usernames = {
            u.strip().lstrip("@").lower()
            for u in settings.telegram_admin_usernames.split(",")
            if u.strip()
        }

    async def _api(self, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        backoff = settings.telegram_api_backoff_base_seconds
        for attempt in range(1, settings.telegram_api_max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=40) as client:
                    response = await client.post(f"{self._base_url}/{method}", json=payload or {})
                status = response.status_code
                if status >= 400:
                    if status in RETRYABLE_HTTP_STATUSES and attempt < settings.telegram_api_max_retries:
                        await asyncio.sleep(backoff + random.uniform(0.0, settings.telegram_api_backoff_jitter_seconds))
                        backoff *= 2
                        continue
                    response.raise_for_status()

                data = response.json()
                if not data.get("ok", False):
                    error_code = int(data.get("error_code", 500))
                    if error_code in RETRYABLE_HTTP_STATUSES and attempt < settings.telegram_api_max_retries:
                        retry_after = data.get("parameters", {}).get("retry_after")
                        if retry_after:
                            await asyncio.sleep(float(retry_after))
                        else:
                            await asyncio.sleep(backoff + random.uniform(0.0, settings.telegram_api_backoff_jitter_seconds))
                        backoff *= 2
                        continue
                    raise RuntimeError(f"Telegram API error for {method}: {data}")
                return data
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt >= settings.telegram_api_max_retries:
                    raise RuntimeError(f"Telegram API network failure for {method}") from exc
                await asyncio.sleep(backoff + random.uniform(0.0, settings.telegram_api_backoff_jitter_seconds))
                backoff *= 2

        raise RuntimeError(f"Telegram API failed after retries for {method}")

    @staticmethod
    def _escape_md(text: str) -> str:
        escaped = text
        for ch in ("\\", "_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"):
            escaped = escaped.replace(ch, f"\\{ch}")
        return escaped

    async def _send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        markdown: bool = False,
    ) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
        }
        if markdown:
            payload["parse_mode"] = "MarkdownV2"
        if reply_markup:
            payload["reply_markup"] = reply_markup
        await self._api("sendMessage", payload)

    async def send_message_public(self, chat_id: int, text: str) -> None:
        """Public sender for background notification jobs."""
        await self._send_message(chat_id, text)

    async def _is_group_admin(self, chat_id: int, user_id: int) -> bool:
        try:
            result = await self._api(
                "getChatMember",
                {"chat_id": chat_id, "user_id": user_id},
            )
            status = result["result"].get("status")
            return status in {"administrator", "creator"}
        except Exception as exc:
            log.warning("telegram.get_chat_member_failed", error=str(exc), chat_id=chat_id)
            return False

    async def _is_admin(self, chat_id: int, user: dict[str, Any]) -> bool:
        username = (user.get("username") or "").lower()
        if username and username in self._admin_usernames:
            return True
        return await self._is_group_admin(chat_id, int(user["id"]))

    @staticmethod
    def _extract_args(text: str) -> str:
        parts = text.split(maxsplit=1)
        return parts[1].strip() if len(parts) > 1 else ""

    @staticmethod
    def _help_text() -> str:
        return TelegramMessages.help_text()

    @staticmethod
    def _validate_regex_pattern(pattern: str) -> tuple[bool, str | None]:
        if len(pattern) > settings.telegram_regex_max_length:
            return False, f"Regex слишком длинный (>{settings.telegram_regex_max_length} символов)."
        if pattern.count("(") > settings.telegram_regex_max_groups:
            return False, "Regex слишком сложный: слишком много групп."
        if pattern.count("|") > settings.telegram_regex_max_alternations:
            return False, "Regex слишком сложный: слишком много альтернатив."
        # Basic guard against nested quantifiers that can trigger heavy backtracking.
        risky = re.search(r"\((?:[^()]*[+*][^()]*)+\)[+*]", pattern)
        if risky:
            return False, "Regex содержит потенциально тяжелую вложенную квантификацию."
        return True, None

    async def _handle_search(self, chat_id: int, user: dict[str, Any], text: str) -> None:
        started = time.perf_counter()
        query_raw = self._extract_args(text)
        if not query_raw:
            await self._send_message(chat_id, TelegramMessages.ASK_SEARCH_QUERY)
            return

        is_admin = await self._is_admin(chat_id, user)
        parsed = parse_search_query(query_raw)

        if parsed.regex and not is_admin:
            await self._send_message(chat_id, TelegramMessages.REGEX_ADMIN_ONLY)
            return
        if parsed.regex:
            ok, reason = self._validate_regex_pattern(parsed.regex)
            if not ok:
                await self._send_message(chat_id, reason or TelegramMessages.REGEX_REJECTED)
                return

        async with get_session() as session:
            repo = VacancySearchRepository(session)
            rows = await repo.search(
                includes=parsed.includes,
                excludes=parsed.excludes,
                fuzzy=parsed.fuzzy,
                regex=parsed.regex,
                language=settings.deepl_target_lang,
                limit=settings.telegram_search_limit,
                is_admin=is_admin,
            )

        if not rows:
            await self._send_message(chat_id, TelegramMessages.NOTHING_FOUND)
            return

        lines = []
        for row in rows:
            company = self._escape_md(row.company_name or "Unknown company")
            location = self._escape_md(row.location or "Unknown location")
            title = self._escape_md(row.title)
            url = row.url
            lines.append(f"*{title}*\n_{company} | {location}_\n{url}")
            keyboard = {
                "inline_keyboard": [
                    [{"text": "Открыть вакансию", "url": url}],
                    [{"text": "Подписаться на этот запрос", "callback_data": f"subq:{query_raw[:48]}"}],
                ],
            }
            await self._send_message(chat_id, lines[-1], reply_markup=keyboard, markdown=True)
        elapsed_ms = (time.perf_counter() - started) * 1000
        metrics_registry.observe_ms("search_latency_ms", elapsed_ms)
        metrics_registry.observe_search_query_latency(query_raw, elapsed_ms)
        top_n = settings.metrics_top_search_queries
        expensive = metrics_registry.top_search_queries(top_n)
        log.info(
            "search.expensive_queries",
            top_n=top_n,
            items=[{"query": q, "latency_ms": round(ms, 2)} for q, ms in expensive],
        )

    async def _handle_subscribe(self, chat_id: int, user: dict[str, Any], text: str) -> None:
        query_raw = self._extract_args(text)
        if not query_raw:
            await self._send_message(chat_id, TelegramMessages.ASK_SUBSCRIBE_QUERY)
            return

        async with get_session() as session:
            repo = TelegramSubscriptionRepository(session)
            sub = await repo.add(
                telegram_user_id=int(user["id"]),
                username=user.get("username"),
                chat_id=chat_id,
                query=query_raw,
            )
        await self._send_message(chat_id, TelegramMessages.subscription_created(sub.id))

    async def _handle_subscriptions(self, chat_id: int, user: dict[str, Any]) -> None:
        async with get_session() as session:
            repo = TelegramSubscriptionRepository(session)
            rows = await repo.list_active_for_user(int(user["id"]))

        if not rows:
            await self._send_message(chat_id, TelegramMessages.NO_SUBSCRIPTIONS)
            return

        text = TelegramMessages.user_subscriptions([f"#{r.id}: {r.query}" for r in rows])
        await self._send_message(chat_id, text)

    async def _handle_unsubscribe(self, chat_id: int, user: dict[str, Any], text: str) -> None:
        arg = self._extract_args(text)
        if not arg.isdigit():
            await self._send_message(chat_id, "Укажите ID: /unsubscribe <id>")
            return

        async with get_session() as session:
            repo = TelegramSubscriptionRepository(session)
            ok = await repo.cancel_for_user(int(arg), int(user["id"]))
        await self._send_message(chat_id, TelegramMessages.subscription_cancelled(ok))

    async def _handle_admin_subscriptions(self, chat_id: int, user: dict[str, Any]) -> None:
        if not await self._is_admin(chat_id, user):
            await self._send_message(chat_id, TelegramMessages.ADMIN_ONLY)
            return

        async with get_session() as session:
            repo = TelegramSubscriptionRepository(session)
            rows = await repo.list_all_active()

        if not rows:
            await self._send_message(chat_id, TelegramMessages.NO_ACTIVE_SUBSCRIPTIONS)
            return

        lines = [
            f"#{r.id} user={r.telegram_user_id} @{r.username or '-'} chat={r.chat_id} query={r.query}"
            for r in rows
        ]
        await self._send_message(chat_id, TelegramMessages.admin_subscriptions(lines))

    async def _handle_admin_stats(self, chat_id: int, user: dict[str, Any]) -> None:
        if not await self._is_admin(chat_id, user):
            await self._send_message(chat_id, TelegramMessages.ADMIN_ONLY)
            return

        notify = get_last_notification_stats()
        metrics = metrics_registry.snapshot()
        text = (
            "*Статистика рассылки и поиска*\n"
            f"Подписок обработано: *{notify['subscriptions']}*\n"
            f"Отправлено: *{notify['sent']}*\n"
            f"Пропущено \\(уже отправляли\\): *{notify['skipped']}*\n"
            f"Ошибок: *{notify['errors']}*\n\n"
            f"cache\\_hit\\_rate: *{metrics.cache_hit_rate:.2%}*\n"
            f"notifications\\_sent: *{metrics.notifications_sent}*\n"
            f"search\\_latency\\_avg\\_ms: *{metrics.search_latency_avg_ms:.2f}*\n"
            f"top\\_expensive\\_queries: *{len(metrics.top_search_queries)}*"
        )
        await self._send_message(chat_id, text, markdown=True)

    async def _handle_callback_query(self, update: dict[str, Any]) -> None:
        callback = update.get("callback_query")
        if not callback:
            return

        data = callback.get("data") or ""
        from_user = callback.get("from") or {}
        message = callback.get("message") or {}
        chat = message.get("chat") or {}

        if not data.startswith("subq:") or "id" not in from_user or "id" not in chat:
            await self._api("answerCallbackQuery", {"callback_query_id": callback["id"]})
            return

        query_raw = data[5:].strip()
        if not query_raw:
            await self._api("answerCallbackQuery", {"callback_query_id": callback["id"]})
            return

        async with get_session() as session:
            repo = TelegramSubscriptionRepository(session)
            sub = await repo.add(
                telegram_user_id=int(from_user["id"]),
                username=from_user.get("username"),
                chat_id=int(chat["id"]),
                query=query_raw,
            )

        await self._api(
            "answerCallbackQuery",
            {"callback_query_id": callback["id"], "text": f"Подписка #{sub.id} создана"},
        )

    async def _handle_update(self, update: dict[str, Any]) -> None:
        if update.get("callback_query"):
            await self._handle_callback_query(update)
            return

        message = update.get("message") or update.get("edited_message")
        if not message:
            return

        text = message.get("text") or ""
        if not text.startswith("/"):
            return

        chat_id = int(message["chat"]["id"])
        user = message.get("from")
        if user is None:
            return

        try:
            if text.startswith("/start") or text.startswith("/help"):
                await self._send_message(chat_id, self._help_text())
            elif text.startswith("/search"):
                await self._handle_search(chat_id, user, text)
            elif text.startswith("/subscribe"):
                await self._handle_subscribe(chat_id, user, text)
            elif text.startswith("/subscriptions"):
                await self._handle_subscriptions(chat_id, user)
            elif text.startswith("/unsubscribe"):
                await self._handle_unsubscribe(chat_id, user, text)
            elif text.startswith("/admin_subscriptions"):
                await self._handle_admin_subscriptions(chat_id, user)
            elif text.startswith("/admin_stats"):
                await self._handle_admin_stats(chat_id, user)
            else:
                await self._send_message(chat_id, TelegramMessages.UNKNOWN_COMMAND)
        except Exception as exc:
            log.error("telegram.command_failed", error=str(exc))
            await self._send_message(chat_id, TelegramMessages.COMMAND_ERROR)

    async def run(self) -> None:
        log.info("telegram.bot_started")
        while True:
            try:
                response = await self._api(
                    "getUpdates",
                    {
                        "offset": self._offset,
                        "timeout": settings.telegram_poll_timeout_seconds,
                    },
                )
                for update in response.get("result", []):
                    self._offset = int(update["update_id"]) + 1
                    await self._handle_update(update)
            except Exception as exc:
                log.error("telegram.poll_failed", error=str(exc))
                await asyncio.sleep(2)


async def run_telegram_bot() -> None:
    bot = TelegramBotService()
    await bot.run()
