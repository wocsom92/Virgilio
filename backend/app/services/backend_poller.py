from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.models.monitors import MonitoredBackend
from backend.app.services.backend_ingest import safe_ingest_backend_metrics


logger = logging.getLogger(__name__)

MIN_INTERVAL_SECONDS = 30
DEFAULT_TICK_SECONDS = 5


@dataclass(slots=True)
class BackendSchedule:
    backend_id: int
    poll_interval: int
    last_seen_at: datetime | None


def _ensure_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class BackendPoller:
    """Background task that keeps backend metrics in sync with their poll interval."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        tick_seconds: int = DEFAULT_TICK_SECONDS,
    ) -> None:
        self._session_factory = session_factory
        self._tick_seconds = max(1, tick_seconds)
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._next_run: dict[int, datetime] = {}

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        logger.info("Starting backend poller")
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="backend-poller")

    async def stop(self) -> None:
        if not self._task:
            return
        logger.info("Stopping backend poller")
        self._stop_event.set()
        await self._task
        self._task = None
        self._next_run.clear()

    async def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    await self._tick()
                except Exception:  # pragma: no cover - defensive logging
                    logger.exception("Unexpected error during backend polling tick")
                await self._sleep()
        finally:
            self._next_run.clear()

    async def _sleep(self) -> None:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=self._tick_seconds)
        except asyncio.TimeoutError:
            pass

    async def _tick(self) -> None:
        schedules = await self._load_schedules()
        now = datetime.now(tz=timezone.utc)

        active_ids = {schedule.backend_id for schedule in schedules}
        # Drop entries for deleted backends
        for backend_id in list(self._next_run.keys()):
            if backend_id not in active_ids:
                self._next_run.pop(backend_id, None)

        for schedule in schedules:
            backend_id = schedule.backend_id
            interval_seconds = max(schedule.poll_interval, MIN_INTERVAL_SECONDS)
            next_due = self._next_run.get(backend_id)

            if schedule.last_seen_at:
                last_seen = _ensure_aware(schedule.last_seen_at)
                expected = last_seen + timedelta(seconds=interval_seconds)
                if not next_due or expected > next_due:
                    next_due = expected

            if not next_due:
                next_due = now
            elif next_due.tzinfo is None:
                next_due = next_due.replace(tzinfo=timezone.utc)

            if now >= next_due:
                success = await self._poll_backend(backend_id)
                delay = interval_seconds if success else min(interval_seconds, 60)
                self._next_run[backend_id] = datetime.now(tz=timezone.utc) + timedelta(seconds=delay)
            else:
                self._next_run[backend_id] = next_due

    async def _load_schedules(self) -> list[BackendSchedule]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(
                    MonitoredBackend.id,
                    MonitoredBackend.poll_interval_seconds,
                    MonitoredBackend.last_seen_at,
                ).where(MonitoredBackend.is_active.is_(True))
            )
            rows = result.all()
        return [
            BackendSchedule(
                backend_id=row[0],
                poll_interval=row[1] or MIN_INTERVAL_SECONDS,
                last_seen_at=_ensure_aware(row[2]),
            )
            for row in rows
        ]

    async def _poll_backend(self, backend_id: int) -> bool:
        async with self._session_factory() as session:
            backend = await session.get(MonitoredBackend, backend_id)
            if not backend or not backend.is_active:
                return False
            snapshot = await safe_ingest_backend_metrics(session, backend)
            return snapshot is not None
