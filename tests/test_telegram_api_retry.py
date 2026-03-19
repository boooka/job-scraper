from __future__ import annotations

import pytest

from src.config import settings
from src.services.telegram_bot import TelegramBotService


def test_telegram_service_initializes_with_aiogram(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "telegram_bot_token", "123456:test-token")
    bot = TelegramBotService()
    assert bot.bot is not None
    assert bot.dp is not None
    assert bot.router is not None


def test_extract_args_supports_command_text() -> None:
    assert TelegramBotService._extract_args("/search python dev") == "python dev"
    assert TelegramBotService._extract_args("/search") == ""
