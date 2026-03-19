"""In-process metrics registry for early degradation detection."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from threading import Lock
from typing import Iterable


@dataclass
class MetricsSnapshot:
    cache_hits: int
    cache_misses: int
    notifications_sent: int
    notifications_skipped: int
    notifications_errors: int
    search_count: int
    search_latency_avg_ms: float
    top_search_queries: list[tuple[str, float]]

    @property
    def cache_hit_rate(self) -> float:
        total = self.cache_hits + self.cache_misses
        return (self.cache_hits / total) if total else 0.0


class MetricsRegistry:
    def __init__(self) -> None:
        self._counters: dict[str, int] = defaultdict(int)
        self._timings_sum_ms: dict[str, float] = defaultdict(float)
        self._timings_count: dict[str, int] = defaultdict(int)
        self._search_query_max_latency_ms: dict[str, float] = {}
        self._lock = Lock()

    def incr(self, name: str, value: int = 1) -> None:
        with self._lock:
            self._counters[name] += value

    def observe_ms(self, name: str, value_ms: float) -> None:
        with self._lock:
            self._timings_sum_ms[name] += value_ms
            self._timings_count[name] += 1

    def observe_search_query_latency(self, query: str, value_ms: float) -> None:
        normalized = " ".join(query.split())
        if not normalized:
            return
        with self._lock:
            prev = self._search_query_max_latency_ms.get(normalized, 0.0)
            if value_ms > prev:
                self._search_query_max_latency_ms[normalized] = value_ms

    def top_search_queries(self, n: int) -> list[tuple[str, float]]:
        with self._lock:
            items: Iterable[tuple[str, float]] = self._search_query_max_latency_ms.items()
            return sorted(items, key=lambda x: x[1], reverse=True)[:n]

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            search_count = self._timings_count.get("search_latency_ms", 0)
            search_avg = (
                self._timings_sum_ms.get("search_latency_ms", 0.0) / search_count
                if search_count
                else 0.0
            )
            top_queries = sorted(
                self._search_query_max_latency_ms.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:10]
            return MetricsSnapshot(
                cache_hits=self._counters.get("translation_cache_hits", 0),
                cache_misses=self._counters.get("translation_cache_misses", 0),
                notifications_sent=self._counters.get("notifications_sent", 0),
                notifications_skipped=self._counters.get("notifications_skipped", 0),
                notifications_errors=self._counters.get("notifications_errors", 0),
                search_count=search_count,
                search_latency_avg_ms=search_avg,
                top_search_queries=top_queries,
            )


metrics_registry = MetricsRegistry()
