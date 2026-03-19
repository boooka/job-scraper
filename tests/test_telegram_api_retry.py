from __future__ import annotations

import httpx
import pytest

from src.config import settings
from src.services.telegram_bot import TelegramBotService


class _DummyResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://api.telegram.org")
            raise httpx.HTTPStatusError("boom", request=request, response=httpx.Response(self.status_code))


@pytest.mark.asyncio
async def test_api_retry_on_429_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
    monkeypatch.setattr(settings, "telegram_api_max_retries", 3)
    monkeypatch.setattr(settings, "telegram_api_backoff_base_seconds", 0.01)

    responses = [
        _DummyResponse(429, {"ok": False, "error_code": 429, "parameters": {"retry_after": 0}}),
        _DummyResponse(200, {"ok": True, "result": {"status": "ok"}}),
    ]

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *_args, **_kwargs):
            return responses.pop(0)

    sleeps: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("src.services.telegram_bot.httpx.AsyncClient", lambda timeout=40: _Client())
    monkeypatch.setattr("src.services.telegram_bot.asyncio.sleep", _fake_sleep)

    bot = TelegramBotService()
    result = await bot._api("getMe")

    assert result["ok"] is True
    assert len(sleeps) == 1


@pytest.mark.asyncio
async def test_api_no_retry_on_400(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
    monkeypatch.setattr(settings, "telegram_api_max_retries", 3)
    monkeypatch.setattr(settings, "telegram_api_backoff_base_seconds", 0.01)

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *_args, **_kwargs):
            return _DummyResponse(400, {"ok": False, "error_code": 400})

    sleeps: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("src.services.telegram_bot.httpx.AsyncClient", lambda timeout=40: _Client())
    monkeypatch.setattr("src.services.telegram_bot.asyncio.sleep", _fake_sleep)

    bot = TelegramBotService()
    with pytest.raises(httpx.HTTPStatusError):
        await bot._api("getMe")

    assert sleeps == []
