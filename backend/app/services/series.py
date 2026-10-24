"""Column-only loading and downsampling for the time-range chart endpoint."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.time import coerce_utc
from backend.app.models.monitors import MetricSnapshot
from backend.app.schemas.metrics import MetricSeriesPoint

DEFAULT_MAX_POINTS = 300
_NUMERIC_FIELDS = (
    "cpu_temperature_c",
    "ram_used_percent",
    "memory_available_gb",
    "swap_used_percent",
    "disk_usage_percent",
)
_LOAD_KEYS = ("one", "five", "fifteen")


@dataclass(slots=True)
class SeriesRow:
    reported_at: datetime
    cpu_temperature_c: float | None
    fan_level: int | None
    fan_max_level: int | None
    fan_rpm: int | None
    ram_used_percent: float | None
    memory_available_gb: float | None
    swap_used_percent: float | None
    disk_usage_percent: float | None
    cpu_load: dict | None
    mounted_usage: list | None
    disk_temperatures: list | None
    network_counters: Any
    uptime_seconds: int | None


async def load_series_rows(
    session: AsyncSession,
    backend_id: int,
    window_start: datetime,
    window_end: datetime,
) -> list[SeriesRow]:
    """Select just the chart columns (never the deferred raw_payload) for the window."""
    result = await session.execute(
        select(
            MetricSnapshot.reported_at,
            MetricSnapshot.cpu_temperature_c,
            MetricSnapshot.fan_level,
            MetricSnapshot.fan_max_level,
            MetricSnapshot.fan_rpm,
            MetricSnapshot.ram_used_percent,
            MetricSnapshot.memory_available_gb,
            MetricSnapshot.swap_used_percent,
            MetricSnapshot.disk_usage_percent,
            MetricSnapshot.cpu_load,
            MetricSnapshot.mounted_usage,
            MetricSnapshot.disk_temperatures,
            MetricSnapshot.network_counters,
            MetricSnapshot.uptime_seconds,
        )
        .where(
            MetricSnapshot.backend_id == backend_id,
            MetricSnapshot.reported_at >= window_start,
            MetricSnapshot.reported_at <= window_end,
        )
        .order_by(MetricSnapshot.reported_at.asc(), MetricSnapshot.id.asc())
    )
    return [SeriesRow(*row) for row in result.all()]


def _counters_by_interface(counters: Any) -> dict[str, dict]:
    if isinstance(counters, list):
        return {
            entry.get("interface"): entry for entry in counters if isinstance(entry, dict) and entry.get("interface")
        }
    if isinstance(counters, dict):
        return counters
    return {}


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _network_bps(
    current: SeriesRow | None,
    previous: SeriesRow | None,
    interfaces: Sequence[str],
) -> list[dict] | None:
    if current is None or not interfaces:
        return None
    current_counters = _counters_by_interface(current.network_counters)
    if not current_counters:
        return None
    previous_counters = _counters_by_interface(previous.network_counters) if previous is not None else {}
    elapsed = (
        (coerce_utc(current.reported_at) - coerce_utc(previous.reported_at)).total_seconds()
        if previous is not None
        else 0.0
    )
    result: list[dict] = []
    for iface in interfaces:
        now_entry = current_counters.get(iface)
        then_entry = previous_counters.get(iface)
        tx_bps = rx_bps = None
        if now_entry and then_entry and elapsed > 0:
            delta_sent = (now_entry.get("bytes_sent") or 0) - (then_entry.get("bytes_sent") or 0)
            delta_recv = (now_entry.get("bytes_recv") or 0) - (then_entry.get("bytes_recv") or 0)
            tx_bps = max(0.0, delta_sent * 8 / elapsed)
            rx_bps = max(0.0, delta_recv * 8 / elapsed)
        result.append({"interface": iface, "tx_bps": tx_bps, "rx_bps": rx_bps})
    return result


def _disk_temperatures(row: SeriesRow) -> list[dict] | None:
    temps = row.disk_temperatures or []
    if not isinstance(temps, list):
        return None
    return [
        {"device": entry.get("device"), "temperature_c": entry.get("temperature_c")}
        for entry in temps
        if isinstance(entry, dict) and entry.get("device")
    ]


def reboot_markers(rows: Sequence[SeriesRow]) -> list[datetime]:
    """Uptime drops between consecutive raw rows (computed before any bucketing)."""
    markers: list[datetime] = []
    previous: SeriesRow | None = None
    for row in rows:
        if previous is not None and row.uptime_seconds is not None and previous.uptime_seconds is not None:
            if row.uptime_seconds + 60 < previous.uptime_seconds:
                markers.append(row.reported_at)
        previous = row
    return markers


def downsample_series(
    rows: Sequence[SeriesRow],
    window_start: datetime,
    window_end: datetime,
    *,
    interfaces: Sequence[str],
    max_points: int = DEFAULT_MAX_POINTS,
) -> tuple[list[MetricSeriesPoint], int | None]:
    """Bucket rows so at most ``max_points`` points are returned.

    Numeric metrics and load averages are averaged per bucket, mounts and disk temperatures take
    the bucket's last value, and network throughput is the counter delta between the bucket's
    last row and the previous bucket's last row. Returns ``(points, bucket_seconds)``; a window
    with ``max_points`` rows or fewer is returned untouched (``bucket_seconds`` is None).
    """
    if not rows:
        return [], None
    max_points = max(1, int(max_points))
    if len(rows) <= max_points:
        points: list[MetricSeriesPoint] = []
        previous: SeriesRow | None = None
        for row in rows:
            points.append(
                MetricSeriesPoint(
                    reported_at=row.reported_at,
                    cpu_temperature_c=row.cpu_temperature_c,
                    fan_level=row.fan_level,
                    fan_max_level=row.fan_max_level,
                    fan_rpm=row.fan_rpm,
                    ram_used_percent=row.ram_used_percent,
                    memory_available_gb=row.memory_available_gb,
                    swap_used_percent=row.swap_used_percent,
                    disk_usage_percent=row.disk_usage_percent,
                    cpu_load=row.cpu_load,
                    mounted_usage=row.mounted_usage,
                    disk_temperatures=_disk_temperatures(row),
                    network_bps=_network_bps(row, previous, interfaces),
                )
            )
            previous = row
        return points, None

    span = (coerce_utc(window_end) - coerce_utc(window_start)).total_seconds()
    bucket_seconds = max(1, int(-(-span // max_points)))  # ceil
    bucket = timedelta(seconds=bucket_seconds)
    start = coerce_utc(window_start)

    buckets: list[list[SeriesRow]] = []
    current: list[SeriesRow] = []
    current_end = start + bucket
    for row in rows:
        at = coerce_utc(row.reported_at)
        while at >= current_end:
            if current:
                buckets.append(current)
                current = []
            current_end += bucket
        current.append(row)
    if current:
        buckets.append(current)

    points = []
    previous_last: SeriesRow | None = None
    for group in buckets:
        last = group[-1]
        numeric = {
            field: _mean([getattr(row, field) for row in group if getattr(row, field) is not None])
            for field in _NUMERIC_FIELDS
        }
        load_values: dict[str, list[float]] = {key: [] for key in _LOAD_KEYS}
        for row in group:
            if isinstance(row.cpu_load, dict):
                for key in _LOAD_KEYS:
                    value = row.cpu_load.get(key)
                    if isinstance(value, (int, float)):
                        load_values[key].append(float(value))
        cpu_load = {key: _mean(values) for key, values in load_values.items()} if any(load_values.values()) else None
        # The fan level keeps the bucket's peak so short bursts stay visible; RPM is averaged.
        fan_levels = [row.fan_level for row in group if row.fan_level is not None]
        fan_max_levels = [row.fan_max_level for row in group if row.fan_max_level is not None]
        fan_rpm_mean = _mean([float(row.fan_rpm) for row in group if row.fan_rpm is not None])
        points.append(
            MetricSeriesPoint(
                reported_at=last.reported_at,
                fan_level=max(fan_levels) if fan_levels else None,
                fan_max_level=fan_max_levels[-1] if fan_max_levels else None,
                fan_rpm=int(round(fan_rpm_mean)) if fan_rpm_mean is not None else None,
                cpu_load=cpu_load,
                mounted_usage=last.mounted_usage,
                disk_temperatures=_disk_temperatures(last),
                network_bps=_network_bps(last, previous_last, interfaces),
                **numeric,
            )
        )
        previous_last = last
    return points, bucket_seconds
