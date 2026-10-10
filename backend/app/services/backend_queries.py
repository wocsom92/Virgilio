from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from backend.app.models.monitors import (
    MetricSnapshot,
    MonitoredBackend,
    NotificationEvent,
    QuickStatusItem,
    QuickStatusPingSample,
    QuickStatusPrometheusSample,
)
from backend.app.schemas.backend import BackendWithLatestSnapshot, MonitoredBackendRead
from backend.app.schemas.common import MetricSnapshotRead, MetricSnapshotSummaryRead

# Everything on MetricSnapshot except the deferred raw_payload (reading it would trigger a lazy load).
_SUMMARY_COLUMNS = tuple(
    column.key for column in MetricSnapshot.__table__.columns if column.key != "raw_payload"
)


async def fetch_backends_with_latest_snapshots(
    session: AsyncSession,
    *,
    only_active: bool = False,
    backend_id: int | None = None,
    backend_ids: Sequence[int] | None = None,
    backend_name: str | None = None,
    require_admin_ordering: bool = False,
    include_raw_payload: bool = True,
) -> list[BackendWithLatestSnapshot]:
    """Backends joined to their newest snapshot.

    ``include_raw_payload=False`` leaves the deferred JSON column unloaded; callers that only
    serialise ``BackendDashboardRead`` never need it.
    """
    latest_snapshot_sq = (
        select(
            MetricSnapshot.backend_id.label("backend_id"),
            func.max(MetricSnapshot.id).label("snapshot_id"),
        )
        .group_by(MetricSnapshot.backend_id)
        .subquery()
    )

    query: Select = (
        select(MonitoredBackend, MetricSnapshot)
        .options(*([undefer(MetricSnapshot.raw_payload)] if include_raw_payload else []))
        .outerjoin(latest_snapshot_sq, latest_snapshot_sq.c.backend_id == MonitoredBackend.id)
        .outerjoin(MetricSnapshot, MetricSnapshot.id == latest_snapshot_sq.c.snapshot_id)
    )

    if only_active:
        query = query.where(MonitoredBackend.is_active.is_(True))
    if backend_id is not None:
        query = query.where(MonitoredBackend.id == backend_id)
    elif backend_ids:
        query = query.where(MonitoredBackend.id.in_(backend_ids))
    if backend_name:
        query = query.where(MonitoredBackend.name.ilike(f"%{backend_name}%"))

    if require_admin_ordering:
        query = query.order_by(MonitoredBackend.display_order)
    else:
        query = query.order_by(MonitoredBackend.display_order, MonitoredBackend.name)

    result = await session.execute(query)
    payload: list[BackendWithLatestSnapshot] = []
    for backend, latest_snapshot in result.all():
        base = MonitoredBackendRead.model_validate(backend)
        base_payload = base.model_dump()
        base_payload["api_token"] = None
        snapshot_payload = None
        if latest_snapshot is not None:
            if include_raw_payload:
                snapshot_payload = MetricSnapshotRead.model_validate(latest_snapshot)
            else:
                snapshot_payload = MetricSnapshotSummaryRead.model_validate(
                    {column: getattr(latest_snapshot, column) for column in _SUMMARY_COLUMNS}
                )
        payload.append(BackendWithLatestSnapshot(**base_payload, latest_snapshot=snapshot_payload))
    return payload


async def delete_monitored_backend(session: AsyncSession, backend_id: int) -> bool:
    """Remove a backend and all dependent monitoring data.

    The backend can be referenced by quick-status rows, quick-status history, metric snapshots, and
    notification events. Delete those explicitly in dependency order so the operation succeeds even
    when the database does not have cascading foreign keys configured.
    """
    backend = await session.get(MonitoredBackend, backend_id)
    if backend is None:
        return False

    item_ids = list(
        (
            await session.execute(select(QuickStatusItem.id).where(QuickStatusItem.backend_id == backend_id))
        ).scalars()
    )
    if item_ids:
        await session.execute(
            delete(QuickStatusPingSample).where(QuickStatusPingSample.quick_status_item_id.in_(item_ids))
        )
        await session.execute(
            delete(QuickStatusPrometheusSample).where(QuickStatusPrometheusSample.quick_status_item_id.in_(item_ids))
        )

    await session.execute(delete(NotificationEvent).where(NotificationEvent.backend_id == backend_id))
    await session.execute(delete(MetricSnapshot).where(MetricSnapshot.backend_id == backend_id))
    await session.execute(delete(QuickStatusItem).where(QuickStatusItem.backend_id == backend_id))
    await session.delete(backend)
    await session.commit()
    return True
