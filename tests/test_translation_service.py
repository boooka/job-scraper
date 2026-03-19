from __future__ import annotations

from src.services.translation_service import _chunk_by_char_quota


def test_chunk_by_char_quota_splits_batches():
    texts = ["a" * 4000, "b" * 4000, "c" * 4000]
    chunks = _chunk_by_char_quota(texts, 10_000)
    assert len(chunks) == 2
    assert sum(len(t) for t in chunks[0]) <= 10_000
    assert sum(len(t) for t in chunks[1]) <= 10_000
