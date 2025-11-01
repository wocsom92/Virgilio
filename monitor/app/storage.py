from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import Deque

from monitor.app.config import settings
from monitor.app.schemas import MetricPayload


def _normalize_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class MetricsRepository:
    """Lightweight in-memory storage for recent metric snapshots."""

    def __init__(self, max_entries: int) -> None:
        self._max_entries = max_entries
        self._lock = asyncio.Lock()
        self._items: Deque[MetricPayload] = deque()

    async def initialize(self) -> None:
        # Initialization kept for API symmetry; nothing to load yet.
        await self.prune()

    async def close(self) -> None:
        # Nothing to clean up right now, but kept for future persistence layers.
        return

    async def record(self, payload: MetricPayload) -> None:
        snapshot = payload.model_copy(deep=True)
        async with self._lock:
            self._items.append(snapshot)
            self._prune_locked()

    async def latest(self) -> MetricPayload | None:
        async with self._lock:
            if not self._items:
                return None
            return self._items[-1].model_copy(deep=True)

    async def prune(self) -> None:
        async with self._lock:
            self._prune_locked()

    def _prune_locked(self) -> None:
        retention_cutoff = _normalize_timestamp(datetime.now(tz=timezone.utc)) - settings.history_retention()
        while self._items and _normalize_timestamp(self._items[0].reported_at) < retention_cutoff:
            self._items.popleft()
        while len(self._items) > self._max_entries:
            self._items.popleft()


repository = MetricsRepository(settings.history_max_entries)
