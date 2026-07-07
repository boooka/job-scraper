"""DeepL translation service for vacancy titles and descriptions."""
from __future__ import annotations

import httpx

from src.config import settings
from src.logger import get_logger

log = get_logger(__name__)

DEEPL_API_URL = "https://api-free.deepl.com/v2/translate"  # free tier
DEEPL_API_URL_PRO = "https://api.deepl.com/v2/translate"   # paid tier


class DeepLClient:
    """Async DeepL API client."""

    def __init__(self) -> None:
        self._api_key = settings.deepl_api_key
        # Pro keys end with ":fx" — free keys do not
        self._base_url = (
            DEEPL_API_URL_PRO
            if not self._api_key.endswith(":fx")
            else DEEPL_API_URL
        )

    async def translate_batch(
        self,
        texts: list[str],
        target_lang: str | None = None,
        source_lang: str | None = None,
    ) -> list[str]:
        """
        Translate a list of texts in a single API call.

        Returns translated strings in the same order.
        Raises httpx.HTTPStatusError on API errors.
        """
        if not texts:
            return []

        if not self._api_key:
            raise RuntimeError("DEEPL_API_KEY is not configured")

        lang = target_lang or settings.deepl_target_lang

        payload: dict = {
            "text": texts,
            "target_lang": lang,
        }
        if source_lang:
            payload["source_lang"] = source_lang

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                self._base_url,
                headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
                json=payload,
            )
            response.raise_for_status()

        data = response.json()
        return [t["text"] for t in data["translations"]]

    async def translate_one(self, text: str, target_lang: str | None = None) -> str:
        results = await self.translate_batch([text], target_lang=target_lang)
        return results[0]


# Module-level singleton
_client: DeepLClient | None = None


def get_deepl_client() -> DeepLClient:
    global _client
    if _client is None:
        _client = DeepLClient()
    return _client
