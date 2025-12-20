from datetime import datetime, timedelta, timezone
from math import floor

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.security import get_current_user
from backend.app.db.session import get_session
from backend.app.models.monitors import MetricSnapshot, MonitoredBackend, QuickStatusItem
from backend.app.schemas.backend import BackendWithLatestSnapshot
from backend.app.schemas.common import MetricSnapshotRead
from backend.app.schemas.metrics import MetricSeriesPoint, MetricSeriesResponse
from backend.app.schemas.quick_status import QuickStatusTileRead
from backend.app.services.quick_status import build_quick_status_tiles


router = APIRouter(prefix="/dashboard", tags=["dashboard"])

_RANGE_TO_DELTA = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
}


@router.get("/", response_model=list[BackendWithLatestSnapshot])
async def fetch_dashboard_data(
    _: object = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[BackendWithLatestSnapshot]:
    result = await session.execute(
        select(MonitoredBackend)
        .options(selectinload(MonitoredBackend.snapshots))
        .where(MonitoredBackend.is_active.is_(True))
        .order_by(MonitoredBackend.display_order, MonitoredBackend.name)
    )
    payload: list[BackendWithLatestSnapshot] = []
    for backend in result.scalars():
        latest_snapshot = backend.snapshots[-1] if backend.snapshots else None
        base = BackendWithLatestSnapshot.model_validate(backend)
        payload.append(
            BackendWithLatestSnapshot(
                **base.model_dump(exclude={"latest_snapshot"}),
                latest_snapshot=MetricSnapshotRead.model_validate(latest_snapshot) if latest_snapshot else None,
            )
        )
    return payload


@router.get("/quick-status", response_model=list[QuickStatusTileRead])
async def fetch_quick_status_tiles(
    _: object = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[QuickStatusTileRead]:
    result = await session.execute(
        select(QuickStatusItem)
        .options(selectinload(QuickStatusItem.backend))
        .order_by(QuickStatusItem.display_order, QuickStatusItem.id)
    )
    items = list(result.scalars())
    return await build_quick_status_tiles(session, items)


@router.get(
    "/{backend_id}/series",
    response_model=MetricSeriesResponse,
)
async def fetch_backend_series(
    backend_id: int,
    range_name: str = Query("hourly"),
    offset: int = Query(0, ge=0),
    _: object = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> MetricSeriesResponse:
    backend = await session.get(MonitoredBackend, backend_id)
    if not backend or not backend.is_active:
        raise HTTPException(status_code=404, detail="Backend not found")

    selected = backend.selected_metrics or {}
    selected_ifaces = selected.get("network_interfaces") or []
    if isinstance(selected_ifaces, str):
        selected_ifaces = [token.strip() for token in selected_ifaces.split(",") if token.strip()]

    key = range_name.lower()
    if key not in _RANGE_TO_DELTA:
        raise HTTPException(status_code=400, detail="Invalid range. Use hourly, daily, or weekly.")

    duration = _RANGE_TO_DELTA[key]
    now = datetime.now(tz=timezone.utc)
    window_end = now - duration * offset
    window_start = window_end - duration

    base_query = select(MetricSnapshot).where(MetricSnapshot.backend_id == backend_id)
    result = await session.execute(
        base_query.where(
            MetricSnapshot.reported_at >= window_start,
            MetricSnapshot.reported_at <= window_end,
        ).order_by(MetricSnapshot.reported_at.asc())
    )
    snapshots = list(result.scalars())

    previous_snapshot_date = await session.scalar(
        select(MetricSnapshot.reported_at)
        .where(MetricSnapshot.backend_id == backend_id, MetricSnapshot.reported_at < window_start)
        .order_by(MetricSnapshot.reported_at.desc())
        .limit(1)
    )
    next_snapshot_date = await session.scalar(
        select(MetricSnapshot.reported_at)
        .where(MetricSnapshot.backend_id == backend_id, MetricSnapshot.reported_at > window_end)
        .order_by(MetricSnapshot.reported_at.asc())
        .limit(1)
    )

    def _calculate_offset(sample_date: datetime | None) -> int | None:
        if not sample_date:
            return None
        sample = sample_date
        if sample.tzinfo is None:
            sample = sample.replace(tzinfo=timezone.utc)
        delta = now - sample
        if delta.total_seconds() < 0:
            return 0
        return floor(delta.total_seconds() / duration.total_seconds())

    previous_offset = _calculate_offset(previous_snapshot_date)
    next_offset = _calculate_offset(next_snapshot_date)

    # Compute reboot markers by detecting uptime drops
    reboot_markers: list[datetime] = []
    # Build network throughput
    def _filter_counters(snapshot: MetricSnapshot) -> dict[str, dict]:
        counters = snapshot.network_counters or {}
        if isinstance(counters, list):
            return {entry.get("interface"): entry for entry in counters if isinstance(entry, dict) and entry.get("interface")}
        if isinstance(counters, dict):
            return counters
        return {}

    points: list[MetricSeriesPoint] = []
    prev_snapshot: MetricSnapshot | None = None
    for snapshot in snapshots:
        if prev_snapshot and snapshot.uptime_seconds is not None and prev_snapshot.uptime_seconds is not None:
            if snapshot.uptime_seconds + 60 < prev_snapshot.uptime_seconds:
                reboot_markers.append(snapshot.reported_at)

        network_bps: list[dict] | None = None
        current_counters = _filter_counters(snapshot)
        if current_counters and selected_ifaces:
            previous_counters = _filter_counters(prev_snapshot) if prev_snapshot else {}
            network_bps = []
            for iface in selected_ifaces:
                current = current_counters.get(iface)
                previous = previous_counters.get(iface)
                tx_bps = rx_bps = None
                if current and previous:
                    delta_sent = (current.get("bytes_sent") or 0) - (previous.get("bytes_sent") or 0)
                    delta_recv = (current.get("bytes_recv") or 0) - (previous.get("bytes_recv") or 0)
                    elapsed = (snapshot.reported_at - prev_snapshot.reported_at).total_seconds() if prev_snapshot else 0
                    if elapsed > 0:
                        tx_bps = max(0.0, delta_sent * 8 / elapsed)
                        rx_bps = max(0.0, delta_recv * 8 / elapsed)
                network_bps.append({"interface": iface, "tx_bps": tx_bps, "rx_bps": rx_bps})

        disk_temps: list[dict] | None = None
        temps = snapshot.disk_temperatures or []
        if isinstance(temps, list):
            disk_temps = [
                {"device": entry.get("device"), "temperature_c": entry.get("temperature_c")}
                for entry in temps
                if isinstance(entry, dict) and entry.get("device")
            ]

        points.append(
            MetricSeriesPoint(
                reported_at=snapshot.reported_at,
                cpu_temperature_c=snapshot.cpu_temperature_c,
                ram_used_percent=snapshot.ram_used_percent,
                disk_usage_percent=snapshot.disk_usage_percent,
                cpu_load=snapshot.cpu_load,
                mounted_usage=snapshot.mounted_usage,
                disk_temperatures=disk_temps,
                network_bps=network_bps,
            )
        )
        prev_snapshot = snapshot
    return MetricSeriesResponse(
        backend_id=backend_id,
        range=key,
        window_offset=offset,
        window_start=window_start,
        window_end=window_end,
        previous_offset_with_data=previous_offset,
        next_offset_with_data=next_offset,
        points=points,
        reboot_markers=reboot_markers or None,
    )
