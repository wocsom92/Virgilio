from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from monitor.app.config import settings
from monitor.app.schemas import MetricPayload


def _normalize_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class MetricsRepository:
    """Keeps only the newest metric snapshot (the backend stores history; nothing else reads it)."""

    def __init__(self, max_entries: int | None = None) -> None:  # max_entries kept for compatibility
        self._lock = asyncio.Lock()
        self._latest: MetricPayload | None = None

    async def initialize(self) -> None:
        await self.prune()

    async def close(self) -> None:
        return

    async def record(self, payload: MetricPayload) -> None:
        async with self._lock:
            self._latest = payload

    async def latest(self) -> MetricPayload | None:
        async with self._lock:
            self._prune_locked()
            if self._latest is None:
                return None
            # Shallow copy: callers may tweak top-level fields without touching the stored sample.
            return self._latest.model_copy()

    async def prune(self) -> None:
        async with self._lock:
            self._prune_locked()

    def _prune_locked(self) -> None:
        if self._latest is None:
            return
        cutoff = datetime.now(tz=timezone.utc) - settings.history_retention()
        if _normalize_timestamp(self._latest.reported_at) < cutoff:
            self._latest = None


repository = MetricsRepository()
