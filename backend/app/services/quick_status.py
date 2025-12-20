from __future__ import annotations

from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.models.monitors import MetricSnapshot, QuickStatusItem
from backend.app.schemas.quick_status import QuickStatusItemCreate, QuickStatusTileRead


_PERCENT_METRICS = {"disk_usage_percent", "ram_used_percent", "mount_used_percent"}


def _extract_mount_used_percent(snapshot: MetricSnapshot, mount_path: str | None) -> float | None:
    if not mount_path:
        return None
    mounts = snapshot.mounted_usage or []
    if not isinstance(mounts, list):
        return None
    for entry in mounts:
        if isinstance(entry, dict) and entry.get("mount_point") == mount_path:
            value = entry.get("used_percent")
            return float(value) if isinstance(value, (int, float)) else None
    return None


def _extract_cpu_load_one(snapshot: MetricSnapshot) -> float | None:
    payload = snapshot.cpu_load or {}
    if isinstance(payload, dict):
        value = payload.get("one")
        return float(value) if isinstance(value, (int, float)) else None
    return None


def _metric_value(snapshot: MetricSnapshot, metric_key: str, mount_path: str | None) -> float | None:
    if metric_key == "disk_usage_percent":
        return float(snapshot.disk_usage_percent) if snapshot.disk_usage_percent is not None else None
    if metric_key == "ram_used_percent":
        return float(snapshot.ram_used_percent) if snapshot.ram_used_percent is not None else None
    if metric_key == "cpu_temperature_c":
        return float(snapshot.cpu_temperature_c) if snapshot.cpu_temperature_c is not None else None
    if metric_key == "cpu_load_one":
        return _extract_cpu_load_one(snapshot)
    if metric_key == "mount_used_percent":
        return _extract_mount_used_percent(snapshot, mount_path)
    return None


def _format_value(metric_key: str, value: float | None) -> str:
    if value is None:
        return "—"
    if metric_key in _PERCENT_METRICS:
        return f"{value:.0f}%"
    if metric_key == "cpu_temperature_c":
        return f"{value:.1f}C"
    return f"{value:.2f}"


def _resolve_status(value: float | None, warning_threshold: float, critical_threshold: float) -> str:
    if value is None:
        return "unknown"
    if value >= critical_threshold:
        return "critical"
    if value >= warning_threshold:
        return "warn"
    return "ok"


async def list_quick_status_items(session: AsyncSession) -> list[QuickStatusItem]:
    result = await session.execute(
        select(QuickStatusItem)
        .options(selectinload(QuickStatusItem.backend))
        .order_by(QuickStatusItem.display_order, QuickStatusItem.id)
    )
    return list(result.scalars())


async def build_quick_status_tiles(
    session: AsyncSession,
    items: Iterable[QuickStatusItem],
) -> list[QuickStatusTileRead]:
    items_list = list(items)
    if not items_list:
        return []

    backend_ids = {item.backend_id for item in items_list}
    latest_snapshot_sq = (
        select(
            MetricSnapshot.backend_id.label("backend_id"),
            func.max(MetricSnapshot.reported_at).label("reported_at"),
        )
        .where(MetricSnapshot.backend_id.in_(backend_ids))
        .group_by(MetricSnapshot.backend_id)
        .subquery()
    )
    result = await session.execute(
        select(MetricSnapshot).join(
            latest_snapshot_sq,
            (MetricSnapshot.backend_id == latest_snapshot_sq.c.backend_id)
            & (MetricSnapshot.reported_at == latest_snapshot_sq.c.reported_at),
        )
    )
    snapshots = {snap.backend_id: snap for snap in result.scalars()}

    tiles: list[QuickStatusTileRead] = []
    for item in items_list:
        backend = getattr(item, "backend", None)
        snapshot = snapshots.get(item.backend_id)
        value = _metric_value(snapshot, item.metric_key, item.mount_path) if snapshot else None
        tiles.append(
            QuickStatusTileRead(
                id=item.id,
                backend_id=item.backend_id,
                backend_name=backend.name if backend else "Unknown",
                label=item.label,
                metric_key=item.metric_key,
                value=value,
                display_value=_format_value(item.metric_key, value),
                status=_resolve_status(value, item.warning_threshold, item.critical_threshold),
                reported_at=snapshot.reported_at if snapshot else None,
            )
        )
    return tiles


async def create_quick_status_item(session: AsyncSession, payload: QuickStatusItemCreate) -> QuickStatusItem:
    item = QuickStatusItem(
        backend_id=payload.backend_id,
        label=payload.label,
        metric_key=payload.metric_key,
        mount_path=payload.mount_path,
        warning_threshold=payload.warning_threshold,
        critical_threshold=payload.critical_threshold,
        display_order=payload.display_order,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


async def update_quick_status_item(
    session: AsyncSession,
    item: QuickStatusItem,
    payload: QuickStatusItemCreate,
) -> QuickStatusItem:
    item.backend_id = payload.backend_id
    item.label = payload.label
    item.metric_key = payload.metric_key
    item.mount_path = payload.mount_path
    item.warning_threshold = payload.warning_threshold
    item.critical_threshold = payload.critical_threshold
    item.display_order = payload.display_order
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item
