from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.monitors import MetricSnapshot, MonitoredBackend
from backend.app.schemas.telegram import WarnThresholds

DEFAULT_CPU_TEMP = 80.0
DEFAULT_RAM_PERCENT = 90.0
DEFAULT_DISK_PERCENT = 90.0


def _resolve_threshold(value: float | None, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) else default


def _get_value(payload: Any, key: str):
    if isinstance(payload, dict):
        return payload.get(key)
    return getattr(payload, key, None)


def _iter_mounts(payload: Any) -> list[Any]:
    mounts = _get_value(payload, "mounted_usage")
    if isinstance(mounts, list):
        return mounts
    return []


def _mount_percent(entry: Any) -> float | None:
    if isinstance(entry, dict):
        return entry.get("used_percent")
    return getattr(entry, "used_percent", None)


def _mount_label(entry: Any) -> str:
    if isinstance(entry, dict):
        value = entry.get("mount_point")
    else:
        value = getattr(entry, "mount_point", None)
    return str(value) if value is not None else "mount"


def detect_warnings(payload: Any, thresholds: WarnThresholds) -> list[str]:
    """Recalculate warnings for a payload using the configured thresholds."""

    cpu_limit = _resolve_threshold(thresholds.cpu_temperature_c, DEFAULT_CPU_TEMP)
    ram_limit = _resolve_threshold(thresholds.ram_used_percent, DEFAULT_RAM_PERCENT)
    disk_limit = _resolve_threshold(thresholds.disk_usage_percent, DEFAULT_DISK_PERCENT)
    mount_limit = thresholds.mounted_usage_percent
    if mount_limit is None:
        mount_limit = disk_limit

    warnings: list[str] = []

    temp = _get_value(payload, "cpu_temperature_c")
    if isinstance(temp, (int, float)) and temp >= cpu_limit:
        warnings.append(f"High CPU temperature {temp:.1f}°C")

    ram_percent = _get_value(payload, "ram_used_percent")
    if isinstance(ram_percent, (int, float)) and ram_percent >= ram_limit:
        warnings.append(f"High RAM usage {ram_percent:.1f}%")

    disk_percent = _get_value(payload, "disk_usage_percent")
    if isinstance(disk_percent, (int, float)) and disk_percent >= disk_limit:
        warnings.append(f"Disk usage critical at {disk_percent:.1f}%")

    for volume in _iter_mounts(payload):
        percent = _mount_percent(volume)
        if isinstance(percent, (int, float)) and percent >= mount_limit:
            warnings.append(f"{_mount_label(volume)} usage critical at {percent:.1f}%")

    return warnings


async def recalculate_latest_snapshot_warnings(
    session: AsyncSession,
    thresholds: WarnThresholds,
) -> None:
    """Apply the latest thresholds to each backend's most recent snapshot."""

    latest_snapshot_sq = (
        select(
            MetricSnapshot.backend_id.label("backend_id"),
            func.max(MetricSnapshot.reported_at).label("reported_at"),
        )
        .group_by(MetricSnapshot.backend_id)
        .subquery()
    )

    result = await session.execute(
        select(MetricSnapshot)
        .join(
            latest_snapshot_sq,
            (MetricSnapshot.backend_id == latest_snapshot_sq.c.backend_id)
            & (MetricSnapshot.reported_at == latest_snapshot_sq.c.reported_at),
        )
    )
    snapshots = list(result.scalars())
    if not snapshots:
        return

    backend_ids = {snapshot.backend_id for snapshot in snapshots}
    backend_rows = await session.execute(
        select(MonitoredBackend).where(MonitoredBackend.id.in_(backend_ids))
    )
    backend_map = {backend.id: backend for backend in backend_rows.scalars()}

    for snapshot in snapshots:
        warnings = detect_warnings(snapshot, thresholds)
        snapshot.warnings = warnings or None
        backend = backend_map.get(snapshot.backend_id)
        if backend:
            backend.last_warning = "; ".join(warnings) if warnings else None

    await session.commit()
