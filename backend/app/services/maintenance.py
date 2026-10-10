from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.session import engine, wal_checkpoint
from backend.app.models.monitors import (
    MetricSnapshot,
    NotificationEvent,
    QuickStatusPingSample,
    QuickStatusPrometheusSample,
    QuickStatusStatusEvent,
    SiteMonitorSample,
)
from backend.app.services.system_settings import metric_retention_timedelta


logger = logging.getLogger(__name__)

RETENTION_BATCH_SIZE = 5000

# (model, timestamp column) pairs pruned by the retention task.
_RETENTION_TARGETS = (
    (MetricSnapshot, MetricSnapshot.reported_at),
    (QuickStatusPingSample, QuickStatusPingSample.checked_at),
    (QuickStatusPrometheusSample, QuickStatusPrometheusSample.checked_at),
    (SiteMonitorSample, SiteMonitorSample.checked_at),
    (NotificationEvent, NotificationEvent.created_at),
)


async def run_retention(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    batch_size: int = RETENTION_BATCH_SIZE,
) -> dict[str, int]:
    """Delete history older than the configured retention, in bounded batches per table.

    Runs once an hour from the poller instead of on every insert, which keeps each write
    transaction tiny and lets the WAL stay small on SD-card storage.
    """
    retention_window: timedelta = await metric_retention_timedelta(session)
    cutoff = (now or datetime.now(tz=timezone.utc)) - retention_window
    deleted: dict[str, int] = {}
    for model, timestamp_column in _RETENTION_TARGETS:
        table_name = model.__tablename__
        total = 0
        while True:
            stale_ids = select(model.id).where(timestamp_column < cutoff).limit(batch_size).scalar_subquery()
            result = await session.execute(
                delete(model).where(model.id.in_(stale_ids)).execution_options(synchronize_session=False)
            )
            count = int(result.rowcount or 0)
            await session.commit()
            total += count
            if count < batch_size:
                break
        if total:
            logger.info("Retention removed %s rows from %s older than %s", total, table_name, cutoff.isoformat())
        deleted[table_name] = total

    # Status events: drop old rows but always keep each item's newest one (it anchors the strip).
    newest_per_item = (
        select(func.max(QuickStatusStatusEvent.id)).group_by(QuickStatusStatusEvent.quick_status_item_id)
    ).scalar_subquery()
    stale_events = (
        select(QuickStatusStatusEvent.id)
        .where(QuickStatusStatusEvent.started_at < cutoff, QuickStatusStatusEvent.id.not_in(newest_per_item))
        .limit(batch_size)
        .scalar_subquery()
    )
    events_total = 0
    while True:
        result = await session.execute(
            delete(QuickStatusStatusEvent)
            .where(QuickStatusStatusEvent.id.in_(stale_events))
            .execution_options(synchronize_session=False)
        )
        count = int(result.rowcount or 0)
        await session.commit()
        events_total += count
        if count < batch_size:
            break
    deleted[QuickStatusStatusEvent.__tablename__] = events_total
    return deleted


async def run_maintenance(session: AsyncSession, *, now: datetime | None = None, optimize: bool = False) -> dict[str, int]:
    """Retention pass followed by a WAL checkpoint (and ``PRAGMA optimize`` once a day)."""
    deleted = await run_retention(session, now=now)
    try:
        await wal_checkpoint(engine)
        if optimize:
            async with engine.connect() as conn:
                await conn.execute(text("PRAGMA optimize"))
    except Exception as exc:  # pragma: no cover - sqlite-specific housekeeping is best effort
        logger.warning("SQLite maintenance step failed: %s", exc)
    return deleted
