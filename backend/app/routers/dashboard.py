from datetime import datetime, timedelta, timezone
from math import floor

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.security import get_current_user
from backend.app.db.session import get_session
from backend.app.models.monitors import MetricSnapshot, MonitoredBackend, QuickStatusItem
from backend.app.schemas.backend import BackendDashboardRead
from backend.app.schemas.metrics import MetricSeriesResponse
from backend.app.schemas.quick_status import QuickStatusTileRead
from backend.app.schemas.site_monitoring import SiteMonitorStatusRead
from backend.app.services.backend_queries import fetch_backends_with_latest_snapshots
from backend.app.services.quick_status import build_quick_status_tile_summaries, build_quick_status_tiles
from backend.app.services.series import DEFAULT_MAX_POINTS, downsample_series, load_series_rows, reboot_markers
from backend.app.services.site_monitoring import build_site_monitor_statuses, list_site_monitors


router = APIRouter(prefix="/dashboard", tags=["dashboard"])

_RANGE_TO_DELTA = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
}


async def _load_dashboard_quick_status_context(
    session: AsyncSession,
) -> tuple[list[MonitoredBackend], list[QuickStatusItem]]:
    backends_result = await session.execute(
        select(MonitoredBackend)
        .where(MonitoredBackend.is_active.is_(True))
        .order_by(MonitoredBackend.display_order, MonitoredBackend.name, MonitoredBackend.id)
    )
    item_query = select(QuickStatusItem).options(selectinload(QuickStatusItem.backend))
    item_result = await session.execute(item_query.order_by(QuickStatusItem.display_order, QuickStatusItem.id))
    items = [
        item
        for item in item_result.scalars()
        if item.backend is not None and item.backend.is_active
    ]
    return list(backends_result.scalars()), items


@router.get("/", response_model=list[BackendDashboardRead])
async def fetch_dashboard_data(
    _: object = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[BackendDashboardRead]:
    """Backends with their newest snapshot, without the raw monitor payload."""
    return await fetch_backends_with_latest_snapshots(session, only_active=True, include_raw_payload=False)


@router.get("/quick-status", response_model=list[QuickStatusTileRead])
async def fetch_quick_status_tiles(
    _: object = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[QuickStatusTileRead]:
    backends, items = await _load_dashboard_quick_status_context(session)
    return await build_quick_status_tile_summaries(
        session,
        items,
        include_heartbeat_tiles=True,
        backends=backends,
    )


@router.get("/quick-status/history", response_model=list[QuickStatusTileRead])
async def fetch_quick_status_tile_history(
    tile_ids: list[int] | None = Query(None),
    _: object = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[QuickStatusTileRead]:
    """24h strips/series for the requested tiles; without ``tile_ids`` every tile (and heartbeat)."""
    backends, items = await _load_dashboard_quick_status_context(session)
    if tile_ids is None:
        return await build_quick_status_tiles(session, items, include_heartbeat_tiles=True, backends=backends)

    requested_ids = [tile_id for tile_id in tile_ids if tile_id != 0]
    if not requested_ids:
        return []

    positive_ids = {tile_id for tile_id in requested_ids if tile_id > 0}
    scoped_items = [item for item in items if item.id in positive_ids]
    include_heartbeat_tiles = any(tile_id < 0 for tile_id in requested_ids)

    tiles = await build_quick_status_tiles(
        session,
        scoped_items,
        include_heartbeat_tiles=include_heartbeat_tiles,
        backends=backends,
    )

    tiles_by_id = {tile.id: tile for tile in tiles if tile.id in set(requested_ids)}
    return [tiles_by_id[tile_id] for tile_id in requested_ids if tile_id in tiles_by_id]


@router.get("/quick-status/{tile_id}/detail", response_model=QuickStatusTileRead)
async def fetch_quick_status_tile_detail(
    tile_id: int,
    _: object = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> QuickStatusTileRead:
    backends, items = await _load_dashboard_quick_status_context(session)
    include_heartbeat_tiles = tile_id < 0
    scoped_items = [item for item in items if item.id == tile_id] if tile_id > 0 else []

    tiles = await build_quick_status_tiles(
        session,
        scoped_items,
        include_heartbeat_tiles=include_heartbeat_tiles,
        backends=backends,
    )

    for tile in tiles:
        if tile.id == tile_id:
            return tile
    raise HTTPException(status_code=404, detail="Quick status item not found")


@router.get("/site-monitors", response_model=list[SiteMonitorStatusRead])
async def fetch_site_monitor_statuses(
    _: object = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[SiteMonitorStatusRead]:
    items = await list_site_monitors(session, only_active=True)
    return await build_site_monitor_statuses(session, items)


@router.get(
    "/{backend_id}/series",
    response_model=MetricSeriesResponse,
)
async def fetch_backend_series(
    backend_id: int,
    range_name: str = Query("hourly"),
    offset: int = Query(0, ge=0),
    max_points: int = Query(DEFAULT_MAX_POINTS, ge=10, le=5000),
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

    rows = await load_series_rows(session, backend_id, window_start, window_end)

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

    markers = reboot_markers(rows)
    points, bucket_seconds = downsample_series(
        rows,
        window_start,
        window_end,
        interfaces=[iface for iface in selected_ifaces if isinstance(iface, str)],
        max_points=max_points,
    )
    return MetricSeriesResponse(
        backend_id=backend_id,
        range=key,
        window_offset=offset,
        window_start=window_start,
        window_end=window_end,
        previous_offset_with_data=_calculate_offset(previous_snapshot_date),
        next_offset_with_data=_calculate_offset(next_snapshot_date),
        points=points,
        reboot_markers=markers or None,
        bucket_seconds=bucket_seconds,
    )
