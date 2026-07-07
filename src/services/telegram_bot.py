"""Telegram bot implemented with aiogram."""
from __future__ import annotations

import json
import logging
import re
import time
import traceback
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    TelegramObject,
    User,
)

from src.config import settings
from src.db.engine import get_session
from src.db.repository import (
    TelegramSubscriptionRepository,
    TelegramUserRepository,
    VacancySearchRepository,
)
from src.logger import get_logger
from src.services.metrics import metrics_registry
from src.services.search_query import parse_search_query
from src.services.subscription_notifier import get_last_notification_stats
from src.services.telegram_messages import TelegramMessages

log = get_logger(__name__)


class TelegramDebugMiddleware(BaseMiddleware):
    def __init__(self, debug_cb: Callable[[str, dict[str, Any]], None]) -> None:
        super().__init__()
        self._debug = debug_cb

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        self._debug("telegram.update", {"event": event.model_dump(mode="json", exclude_none=True)})
        try:
            return await handler(event, data)
        except Exception as exc:
            self._debug(
                "telegram.handler_error",
                {"error": str(exc), "traceback": traceback.format_exc()},
            )
            raise


class TelegramBotService:
    BTN_SEARCH = "🔎 Поиск вакансий"
    BTN_CONTEXT_SEARCH = "🧩 Фильтры"
    BTN_HELP = "❓ Помощь"
    BTN_SUBSCRIBE = "⭐ Подписаться на поиск"
    BTN_SUBSCRIPTIONS = "📋 Мои подписки"
    BTN_UNSUBSCRIBE = "❌ Отписаться"
    BTN_ADMIN_SUBS = "🛠 Админ: подписки"
    BTN_ADMIN_STATS = "📊 Админ: статистика"

    def __init__(self) -> None:
        if not settings.telegram_bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

        self._admin_usernames = {
            u.strip().lstrip("@").lower()
            for u in settings.telegram_admin_usernames.split(",")
            if u.strip()
        }
        self._pending_inline_queries: dict[str, str] = {}
        self._search_sessions: dict[str, dict[str, Any]] = {}
        self._pending_user_action: dict[int, str] = {}
        self._search_context_by_user: dict[int, dict[str, Any]] = {}
        self._context_location_choices: dict[int, list[str]] = {}

        self._debug_logger = self._build_debug_logger()
        self.bot = Bot(
            token=settings.telegram_bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN_V2),
        )
        self.dp = Dispatcher()
        self.router = Router()
        self.router.message.middleware(TelegramDebugMiddleware(self._tg_debug))
        self.router.callback_query.middleware(TelegramDebugMiddleware(self._tg_debug))
        self._register_handlers()
        self.dp.include_router(self.router)

    @staticmethod
    def _build_debug_logger() -> logging.Logger:
        logger = logging.getLogger("telegram.debug")
        logger.setLevel(logging.DEBUG)
        if logger.handlers:
            return logger
        path = Path(settings.telegram_debug_log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.propagate = False
        return logger

    def _tg_debug(self, event: str, payload: dict[str, Any]) -> None:
        if not settings.telegram_debug_logging:
            return
        self._debug_logger.debug("%s %s", event, json.dumps(payload, ensure_ascii=False, default=str))

    @staticmethod
    def _escape_md(text: str) -> str:
        escaped = text
        for ch in ("\\", "_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"):
            escaped = escaped.replace(ch, f"\\{ch}")
        return escaped

    @classmethod
    def _format_vacancy_card_md(
        cls,
        *,
        title: str,
        company: str,
        location: str,
        url: str,
    ) -> str:
        """Build a MarkdownV2-safe vacancy card string."""
        safe_title = cls._escape_md(title)
        safe_company = cls._escape_md(company)
        safe_location = cls._escape_md(location)
        # URL can contain characters special for MarkdownV2 in plain text context.
        safe_url = cls._escape_md(url)
        return f"*{safe_title}*\n_{safe_company} \\| {safe_location}_\n{safe_url}"

    @staticmethod
    def _row_location(row: Any) -> str:
        """Display label for a vacancy's location — normalised city with its
        translation when available, otherwise the raw scraped string."""
        loc = getattr(row, "display_location", None)
        if loc is None:
            loc = getattr(row, "location", None)
        return loc or "Unknown location"

    @staticmethod
    def _extract_args(text: str) -> str:
        parts = text.split(maxsplit=1)
        return parts[1].strip() if len(parts) > 1 else ""

    @staticmethod
    def _validate_regex_pattern(pattern: str) -> tuple[bool, str | None]:
        if len(pattern) > settings.telegram_regex_max_length:
            return False, f"Regex слишком длинный (>{settings.telegram_regex_max_length} символов)."
        if pattern.count("(") > settings.telegram_regex_max_groups:
            return False, "Regex слишком сложный: слишком много групп."
        if pattern.count("|") > settings.telegram_regex_max_alternations:
            return False, "Regex слишком сложный: слишком много альтернатив."
        risky = re.search(r"\((?:[^()]*[+*][^()]*)+\)[+*]", pattern)
        if risky:
            return False, "Regex содержит потенциально тяжелую вложенную квантификацию."
        return True, None

    async def _send_text(
        self,
        chat_id: int,
        text: str,
        *,
        markdown: bool = False,
        reply_markup: InlineKeyboardMarkup | None = None,
        reply_keyboard: ReplyKeyboardMarkup | None = None,
    ) -> None:
        self._tg_debug(
            "telegram.request",
            {"method": "sendMessage", "chat_id": chat_id, "text": text, "markdown": markdown},
        )
        msg = await self.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.MARKDOWN_V2 if markdown else None,
            reply_markup=reply_markup or reply_keyboard,
        )
        self._tg_debug("telegram.response", {"method": "sendMessage", "message_id": msg.message_id})

    def _main_menu(self, is_admin: bool) -> ReplyKeyboardMarkup:
        rows: list[list[KeyboardButton]] = [
            [KeyboardButton(text=self.BTN_SEARCH), KeyboardButton(text=self.BTN_CONTEXT_SEARCH)],
            [KeyboardButton(text=self.BTN_SUBSCRIBE), KeyboardButton(text=self.BTN_SUBSCRIPTIONS)],
            [KeyboardButton(text=self.BTN_UNSUBSCRIBE)],
        ]
        rows.append([KeyboardButton(text=self.BTN_HELP)])
        if is_admin:
            rows.append([KeyboardButton(text=self.BTN_ADMIN_SUBS), KeyboardButton(text=self.BTN_ADMIN_STATS)])
        return ReplyKeyboardMarkup(
            keyboard=rows,
            resize_keyboard=True,
            one_time_keyboard=False,
        )

    async def send_message_public(self, chat_id: int, text: str) -> None:
        await self._send_text(chat_id, text, markdown=False)

    async def close(self) -> None:
        """Close the underlying aiohttp session.

        For throwaway service instances created just to push a message from the
        scheduler process (see subscription_notifier / admin_notifier); the
        long-running polling instance manages its own session lifecycle.
        """
        await self.bot.session.close()

    async def notify_admins(self, text: str) -> int:
        """Send a plain-text message to every configured admin's last chat.

        Best-effort: failures per admin are logged, never raised. Returns the
        number of admins successfully notified.
        """
        async with get_session() as session:
            repo = TelegramUserRepository(session)
            chat_ids = await repo.list_last_chat_ids_for_usernames(self._admin_usernames)
        sent = 0
        for chat_id in chat_ids:
            try:
                await self._send_text(chat_id, text, markdown=False)
                sent += 1
            except Exception as exc:
                self._tg_debug("admin_notify.failed", {"chat_id": chat_id, "error": str(exc)})
        return sent

    async def _is_admin(self, message: Message) -> bool:
        user = message.from_user
        if user is None:
            return False
        username = (user.username or "").lower()
        if username and username in self._admin_usernames:
            return True
        try:
            member = await self.bot.get_chat_member(message.chat.id, user.id)
            return member.status in {"administrator", "creator"}
        except Exception as exc:
            self._tg_debug("telegram.get_chat_member_failed", {"error": str(exc)})
            return False

    @staticmethod
    def _user_display(user: User) -> str:
        name = " ".join(p for p in (user.first_name, user.last_name) if p)
        handle = f"@{user.username}" if user.username else f"id={user.id}"
        return f"{name} ({handle})" if name else handle

    async def _remember_user(self, user: User | None, chat_id: int | None = None) -> None:
        if user is None:
            return
        async with get_session() as session:
            repo = TelegramUserRepository(session)
            _, created = await repo.upsert_user(
                telegram_user_id=user.id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                language_code=user.language_code,
                is_bot=user.is_bot,
                is_premium=user.is_premium,
                last_chat_id=chat_id,
            )
        # Alert admins the first time a real (non-bot, non-admin) user appears
        if (
            created
            and settings.admin_notify_new_users
            and not user.is_bot
            and (user.username or "").lower() not in self._admin_usernames
        ):
            ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
            await self.notify_admins(
                f"👤 Новый пользователь бота\n{self._user_display(user)}\nВремя: {ts}"
            )

    @staticmethod
    def _new_context() -> dict[str, Any]:
        return {
            "query": None,
            "location": None,
            "date_days": None,
            "salary_from": None,
            "salary_to": None,
            "auto_search": False,
            "history": [],
        }

    def _get_context(self, user_id: int) -> dict[str, Any]:
        if user_id not in self._search_context_by_user:
            self._search_context_by_user[user_id] = self._new_context()
        return self._search_context_by_user[user_id]

    @staticmethod
    def _context_menu_keyboard(auto_search: bool) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔤 Запрос", callback_data="ctx:set:query"),
                    InlineKeyboardButton(text="📍 Локация", callback_data="ctx:set:location"),
                ],
                [
                    InlineKeyboardButton(text="📅 Дата: 7 дней", callback_data="ctx:set:date7"),
                    InlineKeyboardButton(text="💰 Зарплата", callback_data="ctx:set:salary"),
                ],
                [
                    InlineKeyboardButton(text="🔎 Найти", callback_data="ctx:run"),
                    InlineKeyboardButton(
                        text=f"⚡ Автопоиск: {'вкл' if auto_search else 'выкл'}",
                        callback_data="ctx:auto",
                    ),
                ],
                [
                    InlineKeyboardButton(text="↩️ Убрать последний", callback_data="ctx:undo"),
                    InlineKeyboardButton(text="🧹 Очистить все", callback_data="ctx:clear"),
                ],
            ]
        )

    @staticmethod
    def _help_inline_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🗣 Обратная связь", callback_data="help:feedback")],
                [InlineKeyboardButton(text="❓ Помочь", callback_data="help:help")],
            ]
        )

    async def _open_context_menu(self, message: Message, *, user_id: int | None = None) -> None:
        effective_user_id = user_id
        if effective_user_id is None:
            if message.from_user is None:
                return
            effective_user_id = message.from_user.id
        ctx = self._get_context(effective_user_id)
        await self._send_text(
            message.chat.id,
            TelegramMessages.context_summary(
                query=ctx["query"],
                location=ctx["location"],
                salary_from=ctx["salary_from"],
                salary_to=ctx["salary_to"],
                date_days=ctx["date_days"],
                auto_search=ctx["auto_search"],
            ),
            reply_markup=self._context_menu_keyboard(bool(ctx["auto_search"])),
        )

    async def _is_admin_user(self, chat_id: int, user: User) -> bool:
        username = (user.username or "").lower()
        if username and username in self._admin_usernames:
            return True
        try:
            member = await self.bot.get_chat_member(chat_id, user.id)
            return member.status in {"administrator", "creator"}
        except Exception as exc:
            self._tg_debug("telegram.get_chat_member_failed", {"error": str(exc)})
            return False

    async def _run_context_search(self, message: Message, *, user: User | None = None) -> None:
        effective_user = user or message.from_user
        if effective_user is None:
            return
        ctx = self._get_context(effective_user.id)
        query = str(ctx["query"] or "").strip()
        parsed = parse_search_query(query) if query else parse_search_query("")
        published_from: datetime | None = None
        if ctx["date_days"]:
            published_from = datetime.now(UTC) - timedelta(days=int(ctx["date_days"]))
        started = time.perf_counter()
        is_admin = await self._is_admin_user(message.chat.id, effective_user)
        async with get_session() as session:
            repo = VacancySearchRepository(session)
            rows = await repo.search(
                includes=parsed.includes,
                excludes=parsed.excludes,
                fuzzy=parsed.fuzzy,
                regex=parsed.regex if is_admin else None,
                language=settings.deepl_target_lang,
                limit=settings.telegram_search_limit,
                is_admin=is_admin,
                location=ctx["location"],
                published_from=published_from,
                salary_from=ctx["salary_from"],
                salary_to=ctx["salary_to"],
            )
        if not rows:
            await self._send_text(message.chat.id, TelegramMessages.NOTHING_FOUND)
            return
        total = len(rows)
        show_all_threshold = max(1, settings.telegram_search_show_all_threshold)
        next_batch_size = max(1, settings.telegram_search_next_batch_size)
        if total <= show_all_threshold:
            await self._send_rows(chat_id=message.chat.id, rows=rows, query_raw=query or "*")
        else:
            session_token = uuid.uuid4().hex[:12]
            self._search_sessions[session_token] = {
                "chat_id": message.chat.id,
                "query_raw": query or "*",
                "rows": rows,
                "offset": 1,
            }
            await self._send_rows(chat_id=message.chat.id, rows=rows[:1], query_raw=query or "*")
            await self._send_text(
                message.chat.id,
                TelegramMessages.search_many_found(total=total, next_count=next_batch_size),
                reply_markup=self._search_controls_keyboard(session_token, has_more=True),
            )
        elapsed_ms = (time.perf_counter() - started) * 1000
        metrics_registry.observe_ms("search_latency_ms", elapsed_ms)

    async def _execute_search(self, message: Message, query_raw: str) -> None:
        started = time.perf_counter()
        is_admin = await self._is_admin(message)
        parsed = parse_search_query(query_raw)
        if parsed.regex and not is_admin:
            await self._send_text(message.chat.id, TelegramMessages.REGEX_ADMIN_ONLY)
            return
        if parsed.regex:
            ok, reason = self._validate_regex_pattern(parsed.regex)
            if not ok:
                await self._send_text(message.chat.id, reason or TelegramMessages.REGEX_REJECTED)
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
            await self._send_text(message.chat.id, TelegramMessages.NOTHING_FOUND)
            return
        total = len(rows)
        show_all_threshold = max(1, settings.telegram_search_show_all_threshold)
        next_batch_size = max(1, settings.telegram_search_next_batch_size)
        if total <= show_all_threshold:
            await self._send_rows(
                chat_id=message.chat.id,
                rows=rows,
                query_raw=query_raw,
            )
        else:
            session_token = uuid.uuid4().hex[:12]
            self._search_sessions[session_token] = {
                "chat_id": message.chat.id,
                "query_raw": query_raw,
                "rows": rows,
                "offset": 1,
            }
            await self._send_rows(
                chat_id=message.chat.id,
                rows=rows[:1],
                query_raw=query_raw,
            )
            await self._send_text(
                message.chat.id,
                TelegramMessages.search_many_found(total=total, next_count=next_batch_size),
                reply_markup=self._search_controls_keyboard(session_token, has_more=True),
            )

        elapsed_ms = (time.perf_counter() - started) * 1000
        metrics_registry.observe_ms("search_latency_ms", elapsed_ms)
        metrics_registry.observe_search_query_latency(query_raw, elapsed_ms)
        expensive = metrics_registry.top_search_queries(settings.metrics_top_search_queries)
        log.info(
            "search.expensive_queries",
            items=[{"query": q, "latency_ms": round(ms, 2)} for q, ms in expensive],
        )

    async def _send_rows(self, *, chat_id: int, rows: list[Any], query_raw: str) -> None:
        for row in rows:
            token = uuid.uuid4().hex[:12]
            self._pending_inline_queries[token] = query_raw
            card = self._format_vacancy_card_md(
                title=getattr(row, "title", "") or "Untitled",
                company=getattr(row, "company_name", "") or "Unknown company",
                location=self._row_location(row),
                url=getattr(row, "url", ""),
            )
            url = getattr(row, "url", "")
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Открыть вакансию", url=url)],
                    [InlineKeyboardButton(text="Подписаться на этот запрос", callback_data=f"subq:{token}")],
                ]
            )
            await self._send_text(chat_id, card, markdown=True, reply_markup=keyboard)

    @staticmethod
    def _search_controls_keyboard(session_token: str, *, has_more: bool) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        if has_more:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"Следующие {max(1, settings.telegram_search_next_batch_size)}",
                        callback_data=f"s_next:{session_token}",
                    )
                ]
            )
        rows.append([InlineKeyboardButton(text="Показать все", callback_data=f"s_all:{session_token}")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def _notify_new_subscription(self, user: User | None, sub_id: int, query_raw: str) -> None:
        """Alert admins when a (non-admin) user creates a subscription."""
        if not settings.admin_notify_new_subscriptions or user is None:
            return
        if (user.username or "").lower() in self._admin_usernames:
            return
        query_preview = query_raw if len(query_raw) <= 120 else query_raw[:120] + "…"
        await self.notify_admins(
            f"⭐ Новая подписка #{sub_id}\n{self._user_display(user)}\nЗапрос: {query_preview}"
        )

    async def _execute_subscribe(self, message: Message, query_raw: str) -> None:
        if message.from_user is None:
            return
        async with get_session() as session:
            repo = TelegramSubscriptionRepository(session)
            sub = await repo.add(
                telegram_user_id=message.from_user.id,
                username=message.from_user.username,
                chat_id=message.chat.id,
                query=query_raw,
            )
        await self._send_text(message.chat.id, TelegramMessages.subscription_created(sub.id))
        await self._notify_new_subscription(message.from_user, sub.id, query_raw)

    def _register_handlers(self) -> None:
        @self.router.message(Command("start"))
        @self.router.message(Command("help"))
        async def _help(message: Message) -> None:
            await self._remember_user(message.from_user, message.chat.id)
            await self._send_text(
                message.chat.id,
                TelegramMessages.help_text(),
                reply_keyboard=self._main_menu(await self._is_admin(message)),
            )

        @self.router.message(Command("search"))
        async def _search(message: Message) -> None:
            await self._remember_user(message.from_user, message.chat.id)
            query = self._extract_args(message.text or "")
            if not query:
                if message.from_user:
                    self._pending_user_action[message.from_user.id] = "search"
                await self._send_text(message.chat.id, TelegramMessages.ASK_SEARCH_QUERY)
                return
            await self._execute_search(message, query)

        @self.router.message(Command("subscribe"))
        async def _subscribe(message: Message) -> None:
            await self._remember_user(message.from_user, message.chat.id)
            query = self._extract_args(message.text or "")
            if not query:
                if message.from_user:
                    self._pending_user_action[message.from_user.id] = "subscribe"
                await self._send_text(message.chat.id, TelegramMessages.ASK_SUBSCRIBE_QUERY)
                return
            await self._execute_subscribe(message, query)

        @self.router.message(Command("subscriptions"))
        async def _subscriptions(message: Message) -> None:
            await self._remember_user(message.from_user, message.chat.id)
            if message.from_user is None:
                return
            async with get_session() as session:
                repo = TelegramSubscriptionRepository(session)
                rows = await repo.list_active_for_user(message.from_user.id)
            if not rows:
                await self._send_text(message.chat.id, TelegramMessages.NO_SUBSCRIPTIONS)
                return
            await self._send_text(
                message.chat.id,
                TelegramMessages.user_subscriptions([f"#{r.id}: {r.query}" for r in rows]),
            )

        @self.router.message(Command("unsubscribe"))
        async def _unsubscribe(message: Message) -> None:
            await self._remember_user(message.from_user, message.chat.id)
            if message.from_user is None:
                return
            arg = self._extract_args(message.text or "")
            if not arg.isdigit():
                if message.from_user:
                    self._pending_user_action[message.from_user.id] = "unsubscribe"
                await self._send_text(message.chat.id, "Укажите ID подписки для отмены:")
                return
            async with get_session() as session:
                repo = TelegramSubscriptionRepository(session)
                ok = await repo.cancel_for_user(int(arg), message.from_user.id)
            await self._send_text(message.chat.id, TelegramMessages.subscription_cancelled(ok))

        @self.router.message(Command("admin_subscriptions"))
        async def _admin_subs(message: Message) -> None:
            await self._remember_user(message.from_user, message.chat.id)
            if not await self._is_admin(message):
                await self._send_text(message.chat.id, TelegramMessages.ADMIN_ONLY)
                return
            async with get_session() as session:
                repo = TelegramSubscriptionRepository(session)
                rows = await repo.list_all_active()
            if not rows:
                await self._send_text(message.chat.id, TelegramMessages.NO_ACTIVE_SUBSCRIPTIONS)
                return
            lines = [
                f"#{r.id} user={r.telegram_user_id} @{r.username or '-'} chat={r.chat_id} query={r.query}"
                for r in rows
            ]
            await self._send_text(message.chat.id, TelegramMessages.admin_subscriptions(lines))

        @self.router.message(Command("admin_stats"))
        async def _admin_stats(message: Message) -> None:
            await self._remember_user(message.from_user, message.chat.id)
            if not await self._is_admin(message):
                await self._send_text(message.chat.id, TelegramMessages.ADMIN_ONLY)
                return
            notify = get_last_notification_stats()
            metrics = metrics_registry.snapshot()
            lines = [
                "Статистика рассылки и поиска",
                f"Подписок обработано: {notify['subscriptions']}",
                f"Отправлено: {notify['sent']}",
                f"Пропущено (уже отправляли): {notify['skipped']}",
                f"Ошибок: {notify['errors']}",
                "",
                f"cache_hit_rate: {metrics.cache_hit_rate:.2%}",
                f"notifications_sent: {metrics.notifications_sent}",
                f"search_latency_avg_ms: {metrics.search_latency_avg_ms:.2f}",
                "top expensive queries:",
            ]
            top = metrics.top_search_queries[: settings.metrics_top_search_queries]
            lines.extend(
                ["- (empty)"]
                if not top
                else [f"- {latency:.2f} ms | {(query[:80] + ('...' if len(query) > 80 else ''))}" for query, latency in top]
            )
            await self._send_text(message.chat.id, "\n".join(lines))

        @self.router.callback_query()
        async def _callback(callback: CallbackQuery) -> None:
            await self._remember_user(
                callback.from_user,
                callback.message.chat.id if callback.message else None,
            )
            data = callback.data or ""
            if data.startswith("help:"):
                if callback.message is None or callback.from_user is None:
                    await callback.answer()
                    return
                user_id = callback.from_user.id
                if data == "help:help":
                    await self._send_text(callback.message.chat.id, TelegramMessages.help_text())
                    await callback.answer()
                    return
                if data == "help:feedback":
                    self._pending_user_action[user_id] = "feedback"
                    await self._send_text(
                        callback.message.chat.id, TelegramMessages.FEEDBACK_PROMPT, markdown=False
                    )
                    await callback.answer()
                    return
            if data.startswith("ctx:"):
                if callback.message is None or callback.from_user is None:
                    await callback.answer()
                    return
                user_id = callback.from_user.id
                ctx = self._get_context(user_id)
                if data == "ctx:set:query":
                    self._pending_user_action[user_id] = "ctx_query"
                    await self._send_text(callback.message.chat.id, TelegramMessages.ASK_CONTEXT_QUERY)
                    await callback.answer()
                    return
                if data == "ctx:set:location":
                    async with get_session() as session:
                        repo = VacancySearchRepository(session)
                        choices = await repo.list_top_locations(limit=8)
                    self._context_location_choices[user_id] = choices
                    keyboard_rows: list[list[InlineKeyboardButton]] = [
                        [InlineKeyboardButton(text=loc[:50], callback_data=f"ctx:loc:{idx}")]
                        for idx, loc in enumerate(choices)
                    ]
                    keyboard_rows.append(
                        [InlineKeyboardButton(text="✍️ Другое", callback_data="ctx:loc:custom")]
                    )
                    await self._send_text(
                        callback.message.chat.id,
                        "Выберите местоположение:",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
                    )
                    await callback.answer()
                    return
                if data.startswith("ctx:loc:"):
                    suffix = data.split(":", 2)[2]
                    if suffix == "custom":
                        self._pending_user_action[user_id] = "ctx_location_custom"
                        await self._send_text(
                            callback.message.chat.id, TelegramMessages.ASK_CONTEXT_LOCATION_CUSTOM
                        )
                        await callback.answer()
                        return
                    if suffix.isdigit():
                        idx = int(suffix)
                        choices = self._context_location_choices.get(user_id, [])
                        if 0 <= idx < len(choices):
                            ctx["history"].append(("location", ctx["location"]))
                            ctx["location"] = choices[idx]
                            await self._open_context_menu(callback.message, user_id=user_id)
                            if ctx["auto_search"]:
                                await self._run_context_search(callback.message, user=callback.from_user)
                    await callback.answer()
                    return
                if data == "ctx:set:date7":
                    ctx["history"].append(("date_days", ctx["date_days"]))
                    ctx["date_days"] = 7
                    await self._open_context_menu(callback.message, user_id=user_id)
                    if ctx["auto_search"]:
                        await self._run_context_search(callback.message, user=callback.from_user)
                    await callback.answer("Фильтр даты применен")
                    return
                if data == "ctx:set:salary":
                    async with get_session() as session:
                        repo = VacancySearchRepository(session)
                        salary_suggestions = await repo.list_salary_suggestions(limit=6)
                    buttons: list[list[InlineKeyboardButton]] = []
                    for amount in salary_suggestions:
                        buttons.append(
                            [
                                InlineKeyboardButton(
                                    text=f"ОТ {amount}",
                                    callback_data=f"ctx:salary_from:{amount}",
                                ),
                                InlineKeyboardButton(
                                    text=f"ДО {amount}",
                                    callback_data=f"ctx:salary_to:{amount}",
                                ),
                            ]
                        )
                    buttons.extend(
                        [
                            [
                                InlineKeyboardButton(
                                    text="✍️ Ввести ОТ", callback_data="ctx:salary:input_from"
                                ),
                                InlineKeyboardButton(
                                    text="✍️ Ввести ДО", callback_data="ctx:salary:input_to"
                                ),
                            ],
                            [
                                InlineKeyboardButton(
                                    text="✍️ Ввести ОТ-ДО", callback_data="ctx:salary:input_range"
                                )
                            ],
                        ]
                    )
                    await self._send_text(
                        callback.message.chat.id,
                        "Выберите фильтр зарплаты:",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                    )
                    await callback.answer()
                    return
                if data.startswith("ctx:salary_from:"):
                    amount = data.split(":")[-1]
                    if amount.isdigit():
                        ctx["history"].append(("salary_from", ctx["salary_from"]))
                        ctx["salary_from"] = int(amount)
                        await self._open_context_menu(callback.message, user_id=user_id)
                        if ctx["auto_search"]:
                            await self._run_context_search(callback.message, user=callback.from_user)
                    await callback.answer()
                    return
                if data.startswith("ctx:salary_to:"):
                    amount = data.split(":")[-1]
                    if amount.isdigit():
                        ctx["history"].append(("salary_to", ctx["salary_to"]))
                        ctx["salary_to"] = int(amount)
                        await self._open_context_menu(callback.message, user_id=user_id)
                        if ctx["auto_search"]:
                            await self._run_context_search(callback.message, user=callback.from_user)
                    await callback.answer()
                    return
                if data == "ctx:salary:input_from":
                    self._pending_user_action[user_id] = "ctx_salary_from"
                    await self._send_text(callback.message.chat.id, TelegramMessages.ASK_CONTEXT_SALARY_FROM)
                    await callback.answer()
                    return
                if data == "ctx:salary:input_to":
                    self._pending_user_action[user_id] = "ctx_salary_to"
                    await self._send_text(callback.message.chat.id, TelegramMessages.ASK_CONTEXT_SALARY_TO)
                    await callback.answer()
                    return
                if data == "ctx:salary:input_range":
                    self._pending_user_action[user_id] = "ctx_salary_range"
                    await self._send_text(callback.message.chat.id, TelegramMessages.ASK_CONTEXT_SALARY_RANGE)
                    await callback.answer()
                    return
                if data == "ctx:auto":
                    ctx["auto_search"] = not bool(ctx["auto_search"])
                    await self._open_context_menu(callback.message, user_id=user_id)
                    await callback.answer()
                    return
                if data == "ctx:undo":
                    if not ctx["history"]:
                        await callback.answer(TelegramMessages.CONTEXT_EMPTY)
                        return
                    field, old_value = ctx["history"].pop()
                    ctx[field] = old_value
                    await self._open_context_menu(callback.message, user_id=user_id)
                    await callback.answer()
                    return
                if data == "ctx:clear":
                    self._search_context_by_user[user_id] = self._new_context()
                    await self._open_context_menu(callback.message, user_id=user_id)
                    await callback.answer(TelegramMessages.CONTEXT_RESET)
                    return
                if data == "ctx:run":
                    await self._run_context_search(callback.message, user=callback.from_user)
                    await callback.answer()
                    return
            if data.startswith("s_next:") or data.startswith("s_all:"):
                token = data.split(":", 1)[1]
                session = self._search_sessions.get(token)
                if callback.message is None or session is None:
                    await callback.answer("Запрос устарел", show_alert=False)
                    return
                rows = session["rows"]
                offset = int(session["offset"])
                total = len(rows)
                next_batch_size = max(1, settings.telegram_search_next_batch_size)
                if data.startswith("s_all:"):
                    batch = rows[offset:]
                    session["offset"] = total
                else:
                    next_offset = min(total, offset + next_batch_size)
                    batch = rows[offset:next_offset]
                    session["offset"] = next_offset
                if batch:
                    await self._send_rows(
                        chat_id=callback.message.chat.id,
                        rows=batch,
                        query_raw=session["query_raw"],
                    )
                new_offset = int(session["offset"])
                remaining = total - new_offset
                if remaining > 0:
                    await self._send_text(
                        callback.message.chat.id,
                        TelegramMessages.search_remaining(
                            remaining=remaining,
                            next_count=min(next_batch_size, remaining),
                        ),
                        reply_markup=self._search_controls_keyboard(token, has_more=remaining > 0),
                    )
                else:
                    self._search_sessions.pop(token, None)
                    await self._send_text(callback.message.chat.id, "Показаны все объявления.")
                await callback.answer()
                return
            if not data.startswith("subq:"):
                await callback.answer()
                return
            token = data[5:]
            query_raw = self._pending_inline_queries.get(token, "")
            if not query_raw or callback.from_user is None or callback.message is None:
                await callback.answer()
                return
            async with get_session() as session:
                repo = TelegramSubscriptionRepository(session)
                sub = await repo.add(
                    telegram_user_id=callback.from_user.id,
                    username=callback.from_user.username,
                    chat_id=callback.message.chat.id,
                    query=query_raw,
                )
            self._pending_inline_queries.pop(token, None)
            await callback.answer(text=f"Подписка #{sub.id} создана")
            await self._notify_new_subscription(callback.from_user, sub.id, query_raw)

        @self.router.message()
        async def _pending_text(message: Message) -> None:
            await self._remember_user(message.from_user, message.chat.id)
            if message.from_user is None or not message.text or message.text.startswith("/"):
                return
            if message.text == self.BTN_SEARCH:
                self._pending_user_action[message.from_user.id] = "search"
                await self._send_text(message.chat.id, TelegramMessages.ASK_SEARCH_QUERY)
                return
            if message.text == self.BTN_CONTEXT_SEARCH:
                await self._open_context_menu(message)
                return
            if message.text == self.BTN_HELP:
                await self._send_text(
                    message.chat.id,
                    TelegramMessages.HELP_MENU_TITLE,
                    reply_markup=self._help_inline_keyboard(),
                    markdown=False,
                )
                return
            if message.text == self.BTN_SUBSCRIBE:
                self._pending_user_action[message.from_user.id] = "subscribe"
                await self._send_text(message.chat.id, TelegramMessages.ASK_SUBSCRIBE_QUERY)
                return
            if message.text == self.BTN_SUBSCRIPTIONS:
                await _subscriptions(message)
                return
            if message.text == self.BTN_UNSUBSCRIBE:
                self._pending_user_action[message.from_user.id] = "unsubscribe"
                await self._send_text(message.chat.id, "Укажите ID подписки для отмены:")
                return
            if message.text == self.BTN_ADMIN_SUBS:
                await _admin_subs(message)
                return
            if message.text == self.BTN_ADMIN_STATS:
                await _admin_stats(message)
                return
            action = self._pending_user_action.pop(message.from_user.id, "")
            if action == "search":
                await self._execute_search(message, message.text.strip())
            elif action == "subscribe":
                await self._execute_subscribe(message, message.text.strip())
            elif action == "unsubscribe":
                if message.text.strip().isdigit():
                    async with get_session() as session:
                        repo = TelegramSubscriptionRepository(session)
                        ok = await repo.cancel_for_user(int(message.text.strip()), message.from_user.id)
                    await self._send_text(message.chat.id, TelegramMessages.subscription_cancelled(ok))
                else:
                    await self._send_text(message.chat.id, "Нужен числовой ID подписки.")
            elif action == "feedback":
                if message.from_user is None:
                    return
                feedback_text = (message.text or "").strip()
                if not feedback_text:
                    await self._send_text(message.chat.id, TelegramMessages.FEEDBACK_PROMPT)
                    return
                username = message.from_user.username
                user_display = f"@{username}" if username else f"id={message.from_user.id}"
                ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
                report = (
                    "📝 Обратная связь\n"
                    f"От: {user_display}\n"
                    f"Время: {ts}\n\n"
                    f"{feedback_text}"
                )
                async with get_session() as session:
                    repo = TelegramUserRepository(session)
                    admin_chat_ids = await repo.list_last_chat_ids_for_usernames(self._admin_usernames)
                for chat_id in admin_chat_ids:
                    await self._send_text(chat_id, report, markdown=False)
                await self._send_text(message.chat.id, TelegramMessages.FEEDBACK_SENT)
            elif action == "ctx_query":
                ctx = self._get_context(message.from_user.id)
                ctx["history"].append(("query", ctx["query"]))
                ctx["query"] = message.text.strip()
                await self._open_context_menu(message)
                if ctx["auto_search"]:
                    await self._run_context_search(message)
            elif action == "ctx_location_custom":
                ctx = self._get_context(message.from_user.id)
                ctx["history"].append(("location", ctx["location"]))
                ctx["location"] = message.text.strip()
                await self._open_context_menu(message)
                if ctx["auto_search"]:
                    await self._run_context_search(message)
            elif action == "ctx_salary_from":
                if message.text.strip().isdigit():
                    ctx = self._get_context(message.from_user.id)
                    ctx["history"].append(("salary_from", ctx["salary_from"]))
                    ctx["salary_from"] = int(message.text.strip())
                    await self._open_context_menu(message)
                    if ctx["auto_search"]:
                        await self._run_context_search(message)
                else:
                    await self._send_text(message.chat.id, TelegramMessages.ASK_CONTEXT_SALARY_FROM)
            elif action == "ctx_salary_to":
                if message.text.strip().isdigit():
                    ctx = self._get_context(message.from_user.id)
                    ctx["history"].append(("salary_to", ctx["salary_to"]))
                    ctx["salary_to"] = int(message.text.strip())
                    await self._open_context_menu(message)
                    if ctx["auto_search"]:
                        await self._run_context_search(message)
                else:
                    await self._send_text(message.chat.id, TelegramMessages.ASK_CONTEXT_SALARY_TO)
            elif action == "ctx_salary_range":
                parts = message.text.strip().split()
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    low = int(parts[0])
                    high = int(parts[1])
                    if low > high:
                        low, high = high, low
                    ctx = self._get_context(message.from_user.id)
                    ctx["history"].append(("salary_from", ctx["salary_from"]))
                    ctx["history"].append(("salary_to", ctx["salary_to"]))
                    ctx["salary_from"] = low
                    ctx["salary_to"] = high
                    await self._open_context_menu(message)
                    if ctx["auto_search"]:
                        await self._run_context_search(message)
                else:
                    await self._send_text(message.chat.id, TelegramMessages.ASK_CONTEXT_SALARY_RANGE)

    async def run(self) -> None:
        log.info("telegram.bot_started", framework="aiogram")
        await self.dp.start_polling(self.bot)


async def run_telegram_bot() -> None:
    await TelegramBotService().run()
