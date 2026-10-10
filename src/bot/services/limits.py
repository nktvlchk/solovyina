"""Abuse and cost limits: per-user rate, global concurrency, daily LLM budget."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque

from bot.storage.repositories import MetricsRepository


class RateLimiter:
    """Sliding one-minute window per user (in memory)."""

    def __init__(self, per_minute: int, clock=time.monotonic):
        self.per_minute = per_minute
        self._clock = clock
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, user_id: int) -> bool:
        now = self._clock()
        hits = self._hits[user_id]
        while hits and now - hits[0] >= 60:
            hits.popleft()
        if len(hits) >= self.per_minute:
            return False
        hits.append(now)
        if len(self._hits) > 10_000:  # bound memory
            for uid in [u for u, h in self._hits.items() if not h][:5_000]:
                del self._hits[uid]
        return True


class LlmBudget:
    """Global daily cap on paid model calls, persisted in SQLite so a restart
    does not reset it."""

    METRIC = "llm.calls"

    def __init__(self, metrics: MetricsRepository, daily_limit: int):
        self.metrics = metrics
        self.daily_limit = daily_limit
        self._lock = asyncio.Lock()

    async def try_consume(self) -> bool:
        async with self._lock:
            if self.metrics.count(self.METRIC) >= self.daily_limit:
                return False
            self.metrics.incr(self.METRIC)
            return True


class ConcurrencyLimiter:
    def __init__(self, limit: int):
        self._semaphore = asyncio.Semaphore(limit)

    def __call__(self) -> asyncio.Semaphore:
        return self._semaphore
