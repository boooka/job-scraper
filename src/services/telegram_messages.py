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
            "  слово или \"фраза\" - включить (AND)\n"
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

