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


@pytest.mark.asyncio
async def test_translate_batch_raises_quota_on_456(monkeypatch):
    async def fake_post(self, *args, **kwargs):
        return httpx.Response(456, request=httpx.Request("POST", dc.DEEPL_API_URL))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(dc.settings, "deepl_api_key", "test-key:fx", raising=False)

    client = dc.DeepLClient()
    with pytest.raises(DeepLQuotaExceeded):
        await client.translate_batch(["hello"], target_lang="RU")
