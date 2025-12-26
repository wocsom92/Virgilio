from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable
import asyncio

import ping3

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.config import settings
from backend.app.models.monitors import MetricSnapshot, QuickStatusItem
from backend.app.schemas.quick_status import QuickStatusItemCreate, QuickStatusTileRead


_PERCENT_METRICS = {"disk_usage_percent", "ram_used_percent", "mount_used_percent"}
_REVERSE_THRESHOLD_METRICS = {"last_restart"}
_PING_METRICS = {"ping_result", "ping_delay_ms"}


@dataclass(slots=True)
class PingCheckResult:
    checked_at: datetime
    success: bool
    latency_ms: float | None


_PING_CACHE: dict[int, PingCheckResult] = {}
_PING_LOCK = asyncio.Lock()

ping3.EXCEPTIONS = True


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


def _extract_cpu_load_five(snapshot: MetricSnapshot) -> float | None:
    payload = snapshot.cpu_load or {}
    if isinstance(payload, dict):
        value = payload.get("five")
        return float(value) if isinstance(value, (int, float)) else None
    return None


def _extract_cpu_load_fifteen(snapshot: MetricSnapshot) -> float | None:
    payload = snapshot.cpu_load or {}
    if isinstance(payload, dict):
        value = payload.get("fifteen")
        return float(value) if isinstance(value, (int, float)) else None
    return None


def _extract_uptime_hours(snapshot: MetricSnapshot) -> float | None:
    if snapshot.uptime_seconds is None:
        return None
    return float(snapshot.uptime_seconds) / 3600


def _metric_value(snapshot: MetricSnapshot, metric_key: str, mount_path: str | None) -> float | None:
    if metric_key == "disk_usage_percent":
        return float(snapshot.disk_usage_percent) if snapshot.disk_usage_percent is not None else None
    if metric_key == "ram_used_percent":
        return float(snapshot.ram_used_percent) if snapshot.ram_used_percent is not None else None
    if metric_key == "cpu_temperature_c":
        return float(snapshot.cpu_temperature_c) if snapshot.cpu_temperature_c is not None else None
    if metric_key == "cpu_load_one":
        return _extract_cpu_load_one(snapshot)
    if metric_key == "cpu_load_five":
        return _extract_cpu_load_five(snapshot)
    if metric_key == "cpu_load_fifteen":
        return _extract_cpu_load_fifteen(snapshot)
    if metric_key == "mount_used_percent":
        return _extract_mount_used_percent(snapshot, mount_path)
    if metric_key == "last_restart":
        return _extract_uptime_hours(snapshot)
    return None


def _format_value(metric_key: str, value: float | None) -> str:
    if value is None:
        return "—"
    if metric_key in _PERCENT_METRICS:
        return f"{value:.0f}%"
    if metric_key == "cpu_temperature_c":
        return f"{value:.1f}C"
    if metric_key == "last_restart":
        return _format_uptime_hours(value)
    if metric_key == "ping_delay_ms":
        return f"{value:.0f}ms"
    return f"{value:.2f}"


def _format_uptime_hours(value: float) -> str:
    total_minutes = int(round(value * 60))
    days, rem_minutes = divmod(total_minutes, 1440)
    hours, minutes = divmod(rem_minutes, 60)
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    return f"{minutes}m"


def _resolve_status(value: float | None, warning_threshold: float, critical_threshold: float, metric_key: str) -> str:
    if value is None:
        return "unknown"
    if metric_key in _REVERSE_THRESHOLD_METRICS:
        if value <= critical_threshold:
            return "critical"
        if value <= warning_threshold:
            return "warn"
        return "ok"
    if value >= critical_threshold:
        return "critical"
    if value >= warning_threshold:
        return "warn"
    return "ok"


async def _check_ping(item: QuickStatusItem) -> PingCheckResult | None:
    if not item.ping_endpoint:
        return None
    interval = max(5, int(item.ping_interval_seconds or 60))
    now = datetime.now(tz=timezone.utc)
    async with _PING_LOCK:
        cached = _PING_CACHE.get(item.id)
        if cached and now - cached.checked_at < timedelta(seconds=interval):
            return cached
    timeout_seconds = max(1, int(settings.monitor_request_timeout_seconds or 1))

    def _run_ping() -> float | None:
        return ping3.ping(item.ping_endpoint, timeout=timeout_seconds)

    try:
        result = await asyncio.to_thread(_run_ping)
    except Exception:
        result = None

    success = result is not None
    latency_ms = float(result) * 1000 if result is not None else None
    result = PingCheckResult(checked_at=now, success=success, latency_ms=latency_ms)
    async with _PING_LOCK:
        _PING_CACHE[item.id] = result
    return result


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
        ping_result = await _check_ping(item) if item.metric_key in _PING_METRICS else None
        value = _metric_value(snapshot, item.metric_key, item.mount_path) if snapshot else None
        status = _resolve_status(value, item.warning_threshold, item.critical_threshold, item.metric_key)
        display_value = _format_value(item.metric_key, value)
        reported_at = snapshot.reported_at if snapshot else None
        if item.metric_key in _PING_METRICS:
            if ping_result is None:
                status = "unknown"
                display_value = "—"
                value = None
                reported_at = None
            else:
                reported_at = ping_result.checked_at
                if item.metric_key == "ping_result":
                    status = "ok" if ping_result.success else "critical"
                    display_value = "OK" if ping_result.success else "NOK"
                    value = 1.0 if ping_result.success else 0.0
                else:
                    if ping_result.success and ping_result.latency_ms is not None:
                        value = ping_result.latency_ms
                        display_value = _format_value(item.metric_key, value)
                        status = _resolve_status(value, item.warning_threshold, item.critical_threshold, item.metric_key)
                    else:
                        value = None
                        display_value = "timeout"
                        status = "critical"
        tiles.append(
            QuickStatusTileRead(
                id=item.id,
                backend_id=item.backend_id,
                backend_name=backend.name if backend else "Unknown",
                label=item.label,
                metric_key=item.metric_key,
                value=value,
                display_value=display_value,
                status=status,
                reported_at=reported_at,
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
        ping_endpoint=payload.ping_endpoint,
        ping_interval_seconds=payload.ping_interval_seconds,
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
    item.ping_endpoint = payload.ping_endpoint
    item.ping_interval_seconds = payload.ping_interval_seconds
    item.display_order = payload.display_order
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item
