"""Telegram message templates for easy localization and UX updates."""

from __future__ import annotations


class TelegramMessages:
    @staticmethod
    def help_text() -> str:
        return (
            "Команды:\n"
            "/search <запрос> - поиск вакансий\n"
            "/subscribe <запрос> - подписаться на запрос\n"
            "/subscriptions - мои активные подписки\n"
            "/unsubscribe <id> - отменить подписку\n"
            "/admin_subscriptions - все подписчики (только админ)\n"
            "/admin_stats - статистика рассылки/метрик (только админ)\n\n"
            "Синтаксис запроса:\n"
            '  слово или "фраза" - включить (AND)\n'
            "  -слово - исключить\n"
            "  ~шаблон* - нечеткое совпадение (* как wildcard)\n"
            "  /regex/ - регулярка (только админ)"
        )

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

    @staticmethod
    def subscription_created(sub_id: int) -> str:
        return f"Подписка создана: #{sub_id}"

    @staticmethod
    def subscription_cancelled(ok: bool) -> str:
        return "Подписка отменена." if ok else TelegramMessages.SUBSCRIPTION_NOT_FOUND

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
