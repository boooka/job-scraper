"""Persist runtime metrics to disk as JSONL snapshots."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.config import settings
from src.logger import get_logger
from src.services.metrics import metrics_registry

log = get_logger(__name__)


async def dump_metrics_to_disk() -> None:
    snapshot = metrics_registry.snapshot()
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cache_hits": snapshot.cache_hits,
        "cache_misses": snapshot.cache_misses,
        "cache_hit_rate": snapshot.cache_hit_rate,
        "notifications_sent": snapshot.notifications_sent,
        "notifications_skipped": snapshot.notifications_skipped,
        "notifications_errors": snapshot.notifications_errors,
        "search_count": snapshot.search_count,
        "search_latency_avg_ms": snapshot.search_latency_avg_ms,
        "top_search_queries": [
            {"query": query, "latency_ms": latency}
            for query, latency in snapshot.top_search_queries
        ],
    }

    target = Path(settings.metrics_dump_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

    log.info("metrics.dumped_to_disk", path=str(target))
