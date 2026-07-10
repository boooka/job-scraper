from __future__ import annotations

import httpx
import pytest

from src.services import deepl_client as dc
from src.services.deepl_client import DeepLQuotaExceeded
from src.services.translation_service import _chunk_by_char_quota


def test_chunk_by_char_quota_splits_batches():
    texts = ["a" * 4000, "b" * 4000, "c" * 4000]
    chunks = _chunk_by_char_quota(texts, 10_000)
    assert len(chunks) == 2
    assert sum(len(t) for t in chunks[0]) <= 10_000
    assert sum(len(t) for t in chunks[1]) <= 10_000


def test_deepl_api_key_list_parses_multiple():
    from src.config import Settings

    s = Settings(deepl_api_key="a:fx, b:fx ,c")
    assert s.deepl_api_key_list == ["a:fx", "b:fx", "c"]


@pytest.mark.asyncio
async def test_translate_batch_raises_quota_on_single_key(monkeypatch):
    async def fake_post(self, url, headers=None, json=None, **kwargs):
        return httpx.Response(456, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(dc.settings, "deepl_api_key", "test-key:fx", raising=False)

    client = dc.DeepLClient()
    with pytest.raises(DeepLQuotaExceeded):
        await client.translate_batch(["hello"], target_lang="RU")


@pytest.mark.asyncio
async def test_translate_batch_rotates_to_next_key(monkeypatch):
    monkeypatch.setattr(dc.settings, "deepl_api_key", "keyA:fx,keyB:fx", raising=False)
    used: list[str] = []

    async def fake_post(self, url, headers=None, json=None, **kwargs):
        key = headers["Authorization"].split()[-1]
        used.append(key)
        if key == "keyA:fx":  # first key is out of quota
            return httpx.Response(456, request=httpx.Request("POST", url))
        return httpx.Response(
            200,
            json={"translations": [{"text": "привет"}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    client = dc.DeepLClient()
    out = await client.translate_batch(["hello"], target_lang="RU")

    assert out == ["привет"]
    assert used == ["keyA:fx", "keyB:fx"]  # rotated after the first 456


@pytest.mark.asyncio
async def test_translate_batch_raises_only_when_all_keys_exhausted(monkeypatch):
    monkeypatch.setattr(dc.settings, "deepl_api_key", "k1:fx,k2:fx", raising=False)

    async def fake_post(self, url, headers=None, json=None, **kwargs):
        return httpx.Response(456, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    client = dc.DeepLClient()
    with pytest.raises(DeepLQuotaExceeded):
        await client.translate_batch(["hi"], target_lang="RU")


@pytest.mark.asyncio
async def test_get_usage_all_reports_counts(monkeypatch):
    monkeypatch.setattr(dc.settings, "deepl_api_key", "k1:fx", raising=False)

    async def fake_get(self, url, headers=None, **kwargs):
        return httpx.Response(
            200,
            json={"character_count": 500000, "character_limit": 500000},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    usage = await dc.DeepLClient().get_usage_all()

    assert usage[0]["count"] == 500000
    assert usage[0]["exhausted"] is True
