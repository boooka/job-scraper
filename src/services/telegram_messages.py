"""Telegram message templates for easy localization and UX updates."""

from __future__ import annotations


class TelegramMessages:
    @staticmethod
    def help_text(is_admin: bool = False) -> str:
        lines = [
            "📖 Как пользоваться ботом",
            "",
            "Кнопки меню:",
            "🔎 Поиск вакансий — поиск по фразе или словам.",
            "🧩 Фильтры — поиск с фильтрами (локация, зарплата, дата).",
            "⭐ Подписаться на поиск — сохранить запрос и получать новые вакансии.",
            "📋 Мои подписки — список подписок; у каждой кнопки «🔎 Текущие "
            "предложения» и «❌ Отписаться».",
            "❌ Отписаться — показать подписки, чтобы отменить нужную.",
            "❓ Помощь — эта справка.",
        ]
        if is_admin:
            lines += [
                "🛠 Админ: подписки — все подписки пользователей.",
                "📊 Админ: статистика — метрики рассылки и поиска.",
            ]
        lines += [
            "",
            "Меню «🧩 Фильтры»:",
            "🔤 Запрос — задать слова/фразу для поиска.",
            "📍 Локация — выбрать город из списка или ввести свой.",
            "📅 Дата — период публикации: сегодня / 3 / 7 / 14 / 30 дней / любая.",
            "💰 Зарплата — диапазон «от» и «до».",
            "🔎 Найти — выполнить поиск по заданным фильтрам.",
            "⚡ Автопоиск — вкл/выкл: запускать поиск сразу после каждого изменения фильтра.",
            "↩️ Убрать последний — отменить последнее изменение фильтра.",
            "🧹 Очистить все — сбросить все фильтры.",
            "",
            "В карточке вакансии:",
            "«Открыть вакансию» — ссылка на объявление.",
            "«Подписаться на этот запрос» — сохранить текущий запрос как подписку.",
            "",
            "Команды:",
            "/search <запрос> — поиск вакансий",
            "/subscribe <запрос> — подписаться на запрос",
            "/subscriptions — мои активные подписки",
            "/unsubscribe — отменить подписку (покажет список)",
        ]
        if is_admin:
            lines += [
                "/admin_subscriptions — все подписчики (только админ)",
                "/admin_stats — статистика рассылки/метрик (только админ)",
            ]
        lines += [
            "",
            "Синтаксис запроса:",
            '  слово или "фраза" — включить (AND)',
            "  -слово — исключить",
            "  ~шаблон* — нечеткое совпадение (* как wildcard)",
        ]
        if is_admin:
            lines.append("  /regex/ — регулярка (только админ)")
        return "\n".join(lines)

    ASK_SEARCH_QUERY = "Укажите запрос: /search <запрос>"
    ASK_SUBSCRIBE_QUERY = "Укажите запрос: /subscribe <запрос>"
    REGEX_ADMIN_ONLY = "Regex-поиск доступен только администраторам."
    REGEX_REJECTED = "Regex отклонен."
    NOTHING_FOUND = "Ничего не найдено."
    NO_SUBSCRIPTIONS = "У вас нет активных подписок."
    NO_ACTIVE_SUBSCRIPTIONS = "Активных подписок нет."
    SUBSCRIPTION_NOT_FOUND = "Подписка не найдена."
    ADMIN_ONLY = "Команда доступна только администраторам."
    UNKNOWN_COMMAND = "Неизвестная команда. Используйте /help."
    COMMAND_ERROR = "Ошибка при обработке команды."

    HELP_MENU_TITLE = "Помощь:"
    HELP_FEEDBACK_TITLE = "Обратная связь"
    HELP_HELP_TITLE = "Помочь"

    FEEDBACK_PROMPT = (
        "Пожелания, предложения или замечания можете описать в этом сообщении, "
        "разработчик обязательно отреагирует. Если хотите, что связался непосредственно с вам, "
        "оставьте контактные данные"
    )
    FEEDBACK_SENT = "Спасибо! Ваше сообщение отправлено разработчику."
    ASK_CONTEXT_QUERY = "Введите поисковый запрос (или фразу)."
    ASK_CONTEXT_LOCATION_CUSTOM = "Введите местоположение текстом."
    ASK_CONTEXT_SALARY_FROM = "Введите зарплату ОТ (число)."
    ASK_CONTEXT_SALARY_TO = "Введите зарплату ДО (число)."
    ASK_CONTEXT_SALARY_RANGE = "Введите диапазон в формате: <от> <до>"
    CONTEXT_RESET = "Контекст поиска очищен."
    CONTEXT_EMPTY = "Контекст пуст."
    REFINE_WITH_FILTERS = "Уточнить результат фильтрами (локация, зарплата, дата)?"

    @staticmethod
    def subscription_created(sub_id: int) -> str:
        return (
            f"✅ Подписка создана: #{sub_id}\n"
            "Буду присылать только новые вакансии по этому запросу. "
            "Текущие предложения смотрите в «📋 Мои подписки»."
        )

    @staticmethod
    def subscription_exists(sub_id: int) -> str:
        return (
            f"У вас уже есть активная подписка #{sub_id} на этот запрос. "
            "Текущие предложения — в «📋 Мои подписки»."
        )

    @staticmethod
    def subscription_cancelled(ok: bool) -> str:
        return "Подписка отменена." if ok else TelegramMessages.SUBSCRIPTION_NOT_FOUND

    SUBSCRIPTIONS_HEADER = "Ваши подписки:"

    @staticmethod
    def user_subscriptions(lines: list[str]) -> str:
        return "Ваши подписки:\n" + "\n".join(lines)

    @staticmethod
    def admin_subscriptions(lines: list[str]) -> str:
        return "Все активные подписки:\n" + "\n".join(lines)

    @staticmethod
    def search_many_found(total: int, next_count: int) -> str:
        return (
            f"Найдено {total} объявлений, вывести все или следующие {next_count} "
            "объявлений из результата поиска?"
        )

    @staticmethod
    def search_remaining(remaining: int, next_count: int) -> str:
        return (
            f"Осталось {remaining} объявлений, вывести все или следующие {next_count} "
            "объявлений?"
        )

    @staticmethod
    def context_summary(
        *,
        query: str | None,
        location: str | None,
        salary_from: int | None,
        salary_to: int | None,
        date_days: int | None,
        auto_search: bool,
    ) -> str:
        return (
            "Текущий контекст поиска:\n"
            f"- запрос: {query or '-'}\n"
            f"- локация: {location or '-'}\n"
            f"- дата: {f'последние {date_days} дн.' if date_days else '-'}\n"
            f"- зарплата от: {salary_from if salary_from is not None else '-'}\n"
            f"- зарплата до: {salary_to if salary_to is not None else '-'}\n"
            f"- автопоиск: {'вкл' if auto_search else 'выкл'}"
        )
