from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.models.monitors import MonitoredBackend
from backend.app.services.backend_ingest import safe_ingest_backend_metrics
from backend.app.services.maintenance import run_maintenance
from backend.app.services.quick_status import refresh_quick_status_alert_state, run_due_quick_status_checks
from backend.app.services.site_monitoring import run_due_site_monitor_checks
from backend.app.services.telegram_notifications import dispatch_due_quick_status_notifications


logger = logging.getLogger(__name__)

MIN_INTERVAL_SECONDS = 30
DEFAULT_TICK_SECONDS = 5
MAX_SLEEP_SECONDS = 15
POLL_CONCURRENCY = 3
MAINTENANCE_INTERVAL = timedelta(hours=1)
MAINTENANCE_INITIAL_DELAY = timedelta(seconds=60)
OPTIMIZE_INTERVAL = timedelta(hours=24)


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
    """Background task that keeps backend metrics in sync with their poll interval.

    One loop drives everything that needs the network or periodic writes: monitor polls
    (concurrently, bounded), site-monitor and ping/Prometheus checks, alert-state bookkeeping,
    notification dispatch, and the hourly retention/WAL maintenance pass.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        tick_seconds: int = DEFAULT_TICK_SECONDS,
        max_sleep_seconds: int = MAX_SLEEP_SECONDS,
    ) -> None:
        self._session_factory = session_factory
        self._tick_seconds = max(1, tick_seconds)
        self._max_sleep_seconds = max(self._tick_seconds, max_sleep_seconds)
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._next_run: dict[int, datetime] = {}
        self._poll_semaphore = asyncio.Semaphore(POLL_CONCURRENCY)
        self._next_maintenance_at: datetime | None = None
        self._next_optimize_at: datetime | None = None
        self._next_sleep_seconds: float = float(self._tick_seconds)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        logger.info("Starting backend poller")
        self._stop_event.clear()
        now = datetime.now(tz=timezone.utc)
        self._next_maintenance_at = now + MAINTENANCE_INITIAL_DELAY
        self._next_optimize_at = now + OPTIMIZE_INTERVAL
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
            await asyncio.wait_for(self._stop_event.wait(), timeout=self._next_sleep_seconds)
        except asyncio.TimeoutError:
            pass

    async def _tick(self) -> None:
        schedules = await self._load_schedules()
        now = datetime.now(tz=timezone.utc)

        active_ids = {schedule.backend_id for schedule in schedules}
        for backend_id in list(self._next_run.keys()):
            if backend_id not in active_ids:
                self._next_run.pop(backend_id, None)

        due: list[tuple[int, int]] = []
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
                due.append((backend_id, interval_seconds))
            else:
                self._next_run[backend_id] = next_due

        if due:
            results = await asyncio.gather(
                *(self._poll_backend(backend_id) for backend_id, _ in due), return_exceptions=True
            )
            finished = datetime.now(tz=timezone.utc)
            for (backend_id, interval_seconds), success in zip(due, results):
                ok = success is True
                delay = interval_seconds if ok else min(interval_seconds, 60)
                self._next_run[backend_id] = finished + timedelta(seconds=delay)

        async with self._session_factory() as session:
            try:
                await run_due_site_monitor_checks(session, now=now)
                await run_due_quick_status_checks(session, now=now)
                await refresh_quick_status_alert_state(session, now=now)
            except Exception:
                await session.rollback()
                logger.exception("Background status polling tick failed at %s", now.isoformat())
                raise
            await dispatch_due_quick_status_notifications(session)

        await self._maybe_run_maintenance(now)
        self._next_sleep_seconds = self._compute_sleep_seconds(datetime.now(tz=timezone.utc))

    def _compute_sleep_seconds(self, now: datetime) -> float:
        """Sleep until the next backend is due, capped so checks and dispatch stay responsive."""
        candidates = [self._max_sleep_seconds]
        for next_due in self._next_run.values():
            candidates.append((next_due - now).total_seconds())
        if self._next_maintenance_at is not None:
            candidates.append((self._next_maintenance_at - now).total_seconds())
        return float(max(1.0, min(candidates)))

    async def _maybe_run_maintenance(self, now: datetime) -> None:
        if self._next_maintenance_at is None or now < self._next_maintenance_at:
            return
        optimize = self._next_optimize_at is not None and now >= self._next_optimize_at
        async with self._session_factory() as session:
            try:
                await run_maintenance(session, now=now, optimize=optimize)
            except Exception:
                await session.rollback()
                logger.exception("Maintenance pass failed")
        self._next_maintenance_at = now + MAINTENANCE_INTERVAL
        if optimize:
            self._next_optimize_at = now + OPTIMIZE_INTERVAL

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
        async with self._poll_semaphore:
            async with self._session_factory() as session:
                backend = await session.get(MonitoredBackend, backend_id)
                if not backend or not backend.is_active:
                    return False
                snapshot = await safe_ingest_backend_metrics(session, backend)
                return snapshot is not None
