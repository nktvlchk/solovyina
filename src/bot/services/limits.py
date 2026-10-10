"""Abuse and cost limits: per-user rate, global concurrency, daily LLM budget."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque

from bot.storage.repositories import MetricsRepository, today


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

    def __init__(self, metrics: MetricsRepository, daily_limit: int, user_daily_limit: int | None = None):
        self.metrics = metrics
        self.daily_limit = daily_limit
        self.user_daily_limit = user_daily_limit
        self._lock = asyncio.Lock()
        # Per-user counters stay in memory (never persisted, reset daily/on restart)
        self._per_user: dict[int, int] = {}
        self._per_user_day = ""

    async def try_consume(self, user_id: int | None = None) -> bool:
        async with self._lock:
            day = today()
            if day != self._per_user_day:
                self._per_user, self._per_user_day = {}, day
            if (
                user_id is not None
                and self.user_daily_limit is not None
                and self._per_user.get(user_id, 0) >= self.user_daily_limit
            ):
                return False
            if self.metrics.count(self.METRIC) >= self.daily_limit:
                return False
            self.metrics.incr(self.METRIC)
            if user_id is not None:
                self._per_user[user_id] = self._per_user.get(user_id, 0) + 1
            return True


class ConcurrencyLimiter:
    def __init__(self, limit: int):
        self._semaphore = asyncio.Semaphore(limit)

    def __call__(self) -> asyncio.Semaphore:
        return self._semaphore
