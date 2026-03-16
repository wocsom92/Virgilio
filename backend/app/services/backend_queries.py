from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.monitors import MetricSnapshot, MonitoredBackend
from backend.app.schemas.backend import BackendWithLatestSnapshot, MonitoredBackendRead
from backend.app.schemas.common import MetricSnapshotRead


async def fetch_backends_with_latest_snapshots(
    session: AsyncSession,
    *,
    only_active: bool = False,
    backend_id: int | None = None,
    backend_ids: Sequence[int] | None = None,
    backend_name: str | None = None,
    require_admin_ordering: bool = False,
) -> list[BackendWithLatestSnapshot]:
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
        payload.append(
            BackendWithLatestSnapshot(
                **base_payload,
                latest_snapshot=MetricSnapshotRead.model_validate(latest_snapshot) if latest_snapshot else None,
            )
        )
    return payload
