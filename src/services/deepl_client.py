"""DeepL translation client with multi-key rotation."""

from __future__ import annotations

import httpx

from src.config import settings
from src.logger import get_logger

log = get_logger(__name__)

DEEPL_API_URL = "https://api-free.deepl.com/v2/translate"  # free tier
DEEPL_API_URL_PRO = "https://api.deepl.com/v2/translate"  # paid tier


class DeepLQuotaExceeded(Exception):
    """Every configured DeepL key has hit HTTP 456 (character quota spent).

    Retrying is pointless until a quota resets or the plan is upgraded, so
    callers should stop the batch and back off (e.g. disable the schedule)."""


def _is_free_key(key: str) -> bool:
    # DeepL free keys end with ":fx"; Pro keys do not.
    return key.endswith(":fx")


def _translate_url(key: str) -> str:
    return DEEPL_API_URL if _is_free_key(key) else DEEPL_API_URL_PRO


def _usage_url(key: str) -> str:
    return _translate_url(key).replace("/translate", "/usage")


def _mask(key: str) -> str:
    """Short, non-sensitive key label for logs and the admin report."""
    return f"{key[:6]}…{key[-3:]}" if len(key) > 12 else "***"


class DeepLClient:
    """Async DeepL API client that rotates over multiple keys on quota errors."""

    def __init__(self) -> None:
        self._keys = settings.deepl_api_key_list
        # Keys that returned 456 during the current run; cleared by
        # reset_exhausted() at the start of each translation run.
        self._exhausted: set[str] = set()

    def reset_exhausted(self) -> None:
        """Forget which keys were exhausted — call once per translation run so a
        new run re-probes every key (quotas may have reset since last time)."""
        self._exhausted.clear()

    async def _request(
        self, key: str, texts: list[str], lang: str, source_lang: str | None
    ) -> list[str]:
        payload: dict = {"text": texts, "target_lang": lang}
        if source_lang:
            payload["source_lang"] = source_lang

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                _translate_url(key),
                headers={"Authorization": f"DeepL-Auth-Key {key}"},
                json=payload,
            )
            if response.status_code == 456:
                raise DeepLQuotaExceeded("DeepL character quota exceeded (HTTP 456)")
            response.raise_for_status()

        data = response.json()
        return [t["text"] for t in data["translations"]]

    async def translate_batch(
        self,
        texts: list[str],
        target_lang: str | None = None,
        source_lang: str | None = None,
    ) -> list[str]:
        """Translate a list of texts in a single API call, rotating keys on 456.

        Returns translated strings in the same order. Raises DeepLQuotaExceeded
        only when every configured key is exhausted; other API errors propagate
        as httpx.HTTPStatusError.
        """
        if not texts:
            return []
        if not self._keys:
            raise RuntimeError("DEEPL_API_KEY is not configured")

        lang = target_lang or settings.deepl_target_lang

        for key in self._keys:
            if key in self._exhausted:
                continue
            try:
                return await self._request(key, texts, lang, source_lang)
            except DeepLQuotaExceeded:
                self._exhausted.add(key)
                log.warning(
                    "deepl.key_exhausted",
                    key=_mask(key),
                    remaining=len(self._keys) - len(self._exhausted),
                )
                continue

        raise DeepLQuotaExceeded(f"all {len(self._keys)} DeepL key(s) exhausted")

    async def translate_one(self, text: str, target_lang: str | None = None) -> str:
        results = await self.translate_batch([text], target_lang=target_lang)
        return results[0]

    async def get_usage_all(self) -> list[dict]:
        """Per-key usage for the admin report: masked key, count, limit, flags."""
        out: list[dict] = []
        async with httpx.AsyncClient(timeout=15) as client:
            for key in self._keys:
                entry: dict = {"key": _mask(key)}
                try:
                    resp = await client.get(
                        _usage_url(key),
                        headers={"Authorization": f"DeepL-Auth-Key {key}"},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    count = int(data.get("character_count", 0))
                    limit = int(data.get("character_limit", 0))
                    entry.update(
                        count=count,
                        limit=limit,
                        exhausted=limit > 0 and count >= limit,
                    )
                except Exception as exc:
                    entry.update(count=None, limit=None, exhausted=None, error=str(exc))
                out.append(entry)
        return out


# Module-level singleton
_client: DeepLClient | None = None


def get_deepl_client() -> DeepLClient:
    global _client
    if _client is None:
        _client = DeepLClient()
    return _client
