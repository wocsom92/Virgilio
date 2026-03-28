from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable
import asyncio

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.config import settings
from backend.app.models.monitors import MetricSnapshot, QuickStatusItem, QuickStatusPingSample
from backend.app.schemas.quick_status import (
    QuickStatusDetailLine,
    QuickStatusItemCreate,
    QuickStatusTileRead,
    is_supported_quick_status_metric,
)
from backend.app.services.monitor_client import MonitorClientError, fetch_ping
from backend.app.services.system_settings import metric_retention_timedelta


_PERCENT_METRICS = {"disk_usage_percent", "ram_used_percent", "swap_used_percent", "mount_used_percent"}
_REVERSE_THRESHOLD_METRICS = {"last_restart", "memory_available_gb", "mount_available_gb", "ssh_last_unsuccessful_attempt"}
_PING_METRICS = {"ping_result", "ping_delay_ms"}
_INFO_ONLY_METRICS = {"swap_used_percent"}
_ALERT_STATUSES = {"warn", "critical"}
_HISTORY_SEGMENT_COUNT = 12
_HISTORY_SEGMENT_DURATION = timedelta(hours=2)
_HISTORY_WINDOW = _HISTORY_SEGMENT_DURATION * _HISTORY_SEGMENT_COUNT
_HISTORY_STATUS_PRIORITY = {
    "critical": 4,
    "warn": 3,
    "unknown": 2,
    "info": 1,
    "ok": 1,
}


@dataclass(slots=True)
class PingCheckResult:
    checked_at: datetime
    success: bool
    latency_ms: float | None


@dataclass(slots=True)
class QuickStatusTransition:
    item_id: int
    backend_id: int
    backend_name: str
    label: str
    metric_key: str
    previous_status: str
    current_status: str
    display_value: str


@dataclass(slots=True)
class QuickStatusEvaluation:
    value: float | None
    display_value: str
    status: str


_PING_CACHE: dict[int, PingCheckResult] = {}
_PING_LOCK = asyncio.Lock()


def _quick_status_item_sort_key(item: QuickStatusItem) -> tuple[int, str, int, int, int]:
    backend = getattr(item, "backend", None)
    backend_order = backend.display_order if backend is not None else 0
    backend_name = backend.name if backend is not None else ""
    return (
        backend_order,
        backend_name.casefold(),
        item.backend_id,
        item.display_order,
        item.id,
    )


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


def _extract_mount_available_gb(snapshot: MetricSnapshot, mount_path: str | None) -> float | None:
    if not mount_path:
        return None
    mounts = snapshot.mounted_usage or []
    if not isinstance(mounts, list):
        mounts = []
    for entry in mounts:
        if not isinstance(entry, dict) or entry.get("mount_point") != mount_path:
            continue
        total_gb = entry.get("total_gb")
        used_percent = entry.get("used_percent")
        if not isinstance(total_gb, (int, float)) or not isinstance(used_percent, (int, float)):
            return None
        free_gb = float(total_gb) * max(0.0, min(100.0, 100.0 - float(used_percent))) / 100.0
        return free_gb
    if mount_path == "/":
        return _extract_raw_payload_value(snapshot, "disk_available_gb")
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


def _extract_running_container_count(snapshot: MetricSnapshot) -> float | None:
    if snapshot.docker_container_count is not None:
        return float(snapshot.docker_container_count)
    containers = snapshot.docker_running_containers
    if not isinstance(containers, list):
        return None
    names = [name for name in containers if isinstance(name, str) and name.strip()]
    return float(len(names))


def _extract_raw_payload_value(snapshot: MetricSnapshot, key: str) -> float | None:
    payload = snapshot.raw_payload if isinstance(snapshot.raw_payload, dict) else {}
    raw = payload.get(key)
    return float(raw) if isinstance(raw, (int, float)) else None


def _metric_value(snapshot: MetricSnapshot, metric_key: str, mount_path: str | None) -> float | None:
    if metric_key == "disk_usage_percent":
        return float(snapshot.disk_usage_percent) if snapshot.disk_usage_percent is not None else None
    if metric_key == "ram_used_percent":
        return float(snapshot.ram_used_percent) if snapshot.ram_used_percent is not None else None
    if metric_key == "cpu_temperature_c":
        return float(snapshot.cpu_temperature_c) if snapshot.cpu_temperature_c is not None else None
    if metric_key == "memory_available_gb":
        return float(snapshot.memory_available_gb) if snapshot.memory_available_gb is not None else None
    if metric_key == "swap_used_percent":
        return float(snapshot.swap_used_percent) if snapshot.swap_used_percent is not None else None
    if metric_key == "docker_container_count":
        return _extract_running_container_count(snapshot)
    if metric_key == "cpu_load_one":
        return _extract_cpu_load_one(snapshot)
    if metric_key == "cpu_load_five":
        return _extract_cpu_load_five(snapshot)
    if metric_key == "cpu_load_fifteen":
        return _extract_cpu_load_fifteen(snapshot)
    if metric_key == "mount_used_percent":
        return _extract_mount_used_percent(snapshot, mount_path)
    if metric_key == "mount_available_gb":
        return _extract_mount_available_gb(snapshot, mount_path)
    if metric_key == "last_restart":
        return _extract_uptime_hours(snapshot)
    if metric_key == "ssh_last_successful_login":
        return _extract_raw_payload_value(snapshot, "ssh_last_successful_login_seconds")
    if metric_key == "ssh_last_unsuccessful_attempt":
        seconds = _extract_raw_payload_value(snapshot, "ssh_last_unsuccessful_attempt_seconds")
        return (seconds / 3600) if seconds is not None else None
    if metric_key == "ssh_status":
        return _extract_raw_payload_value(snapshot, "ssh_status_level")
    return None


def _format_value(metric_key: str, value: float | None) -> str:
    if value is None:
        return "—"
    if metric_key in _PERCENT_METRICS:
        return f"{value:.0f}%"
    if metric_key == "cpu_temperature_c":
        return f"{value:.1f} °C"
    if metric_key in {"memory_available_gb", "mount_available_gb"}:
        return _format_binary_size_from_gib(value)
    if metric_key == "docker_container_count":
        return f"{int(round(value))}"
    if metric_key == "last_restart":
        return _format_uptime_hours(value)
    if metric_key == "ping_delay_ms":
        return f"{value:.0f}ms"
    if metric_key in {"ssh_last_successful_login", "ssh_last_unsuccessful_attempt"}:
        hours = value / 3600 if metric_key == "ssh_last_successful_login" else value
        return _format_uptime_hours(hours)
    if metric_key == "ssh_status":
        level = int(round(value))
        if level <= 0:
            return "OK"
        if level == 1:
            return "WARN"
        return "CRIT"
    return f"{value:.2f}"


def _format_binary_size_from_gib(value_gib: float) -> str:
    if value_gib >= 1024:
        return f"{round(value_gib / 1024):.0f} TiB"
    if value_gib >= 1:
        return f"{round(value_gib):.0f} GiB"
    value_mib = value_gib * 1024
    if value_mib >= 1:
        return f"{round(value_mib):.0f} MiB"
    return f"{round(value_mib * 1024):.0f} KiB"


def _build_detail_lines(item: QuickStatusItem, snapshot: MetricSnapshot | None) -> list[QuickStatusDetailLine] | None:
    if snapshot is None or item.metric_key != "ssh_status":
        return None
    payload = snapshot.raw_payload if isinstance(snapshot.raw_payload, dict) else {}
    pubkey_enabled = payload.get("ssh_pubkey_auth_enabled")
    password_auth_disabled = payload.get("ssh_password_auth_disabled")
    kbd_interactive_disabled = payload.get("ssh_kbd_interactive_auth_disabled")
    permit_root_mode = payload.get("ssh_permit_root_login_mode")
    pubkey_line = payload.get("ssh_pubkey_auth_line")
    password_auth_line = payload.get("ssh_password_auth_line")
    kbd_interactive_line = payload.get("ssh_kbd_interactive_auth_line")
    permit_root_line = payload.get("ssh_permit_root_login_line")

    lines: list[QuickStatusDetailLine] = []

    if isinstance(pubkey_line, str) and pubkey_line.strip():
        lines.append(
            QuickStatusDetailLine(
                text=pubkey_line.strip(),
                severity="ok" if pubkey_enabled is True else "warn",
            )
        )
    if isinstance(password_auth_line, str) and password_auth_line.strip():
        lines.append(
            QuickStatusDetailLine(
                text=password_auth_line.strip(),
                severity="ok" if password_auth_disabled is True else "warn",
            )
        )
    if isinstance(kbd_interactive_line, str) and kbd_interactive_line.strip():
        lines.append(
            QuickStatusDetailLine(
                text=kbd_interactive_line.strip(),
                severity="ok" if kbd_interactive_disabled is True else "warn",
            )
        )
    if isinstance(permit_root_line, str) and permit_root_line.strip():
        permit_root_token = permit_root_mode.strip().lower() if isinstance(permit_root_mode, str) else ""
        if permit_root_token == "no":
            severity = "ok"
        elif permit_root_token in {"prohibit-password", "without-password", "forced-commands-only"}:
            severity = "warn"
        else:
            severity = "critical"
        lines.append(QuickStatusDetailLine(text=permit_root_line.strip(), severity=severity))

    return lines or None


def _format_uptime_hours(value: float) -> str:
    total_minutes = int(round(value * 60))
    days, rem_minutes = divmod(total_minutes, 1440)
    hours, minutes = divmod(rem_minutes, 60)
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    return f"{minutes}m"


def _format_elapsed_seconds(value: float) -> str:
    total = max(0, int(round(value)))
    days, rem = divmod(total, 86_400)
    hours, rem = divmod(rem, 3_600)
    minutes, seconds = divmod(rem, 60)
    return f"{days:02d}:{hours:02d}:{minutes:02d}:{seconds:02d}"


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _history_window_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    window_end = _coerce_utc(now or datetime.now(tz=timezone.utc))
    return window_end - _HISTORY_WINDOW, window_end


def _empty_history() -> list[str]:
    return ["unknown"] * _HISTORY_SEGMENT_COUNT


def _history_segment_ranges(window_start: datetime) -> list[tuple[datetime, datetime]]:
    return [
        (
            window_start + (_HISTORY_SEGMENT_DURATION * index),
            window_start + (_HISTORY_SEGMENT_DURATION * (index + 1)),
        )
        for index in range(_HISTORY_SEGMENT_COUNT)
    ]


def _dominant_status(durations: dict[str, float]) -> str:
    if not durations:
        return "unknown"
    return max(
        durations.items(),
        key=lambda entry: (
            entry[1],
            _HISTORY_STATUS_PRIORITY.get(entry[0], 0),
        ),
    )[0]


def _accumulate_status_durations(
    buckets: list[dict[str, float]],
    bucket_ranges: list[tuple[datetime, datetime]],
    status: str,
    start: datetime,
    end: datetime,
) -> None:
    if end <= start:
        return
    for index, (bucket_start, bucket_end) in enumerate(bucket_ranges):
        overlap_start = max(start, bucket_start)
        overlap_end = min(end, bucket_end)
        if overlap_end <= overlap_start:
            continue
        buckets[index][status] = buckets[index].get(status, 0.0) + (overlap_end - overlap_start).total_seconds()


def _finalize_history_buckets(buckets: list[dict[str, float]]) -> list[str]:
    return [_dominant_status(bucket) for bucket in buckets]


def _ping_result_from_sample(sample: QuickStatusPingSample) -> PingCheckResult:
    return PingCheckResult(
        checked_at=_coerce_utc(sample.checked_at),
        success=bool(sample.success),
        latency_ms=float(sample.latency_ms) if sample.latency_ms is not None else None,
    )


def _evaluate_ping_result(item: QuickStatusItem, ping_result: PingCheckResult | None) -> QuickStatusEvaluation:
    if ping_result is None:
        return QuickStatusEvaluation(value=None, display_value="—", status="unknown")
    if item.metric_key == "ping_result":
        return QuickStatusEvaluation(
            value=1.0 if ping_result.success else 0.0,
            display_value="OK" if ping_result.success else "NOK",
            status="ok" if ping_result.success else "critical",
        )
    if ping_result.success and ping_result.latency_ms is not None:
        value = ping_result.latency_ms
        return QuickStatusEvaluation(
            value=value,
            display_value=_format_value(item.metric_key, value),
            status=_resolve_status(value, item.warning_threshold, item.critical_threshold, item.metric_key),
        )
    return QuickStatusEvaluation(value=None, display_value="timeout", status="critical")


def _resolve_status(value: float | None, warning_threshold: float, critical_threshold: float, metric_key: str) -> str:
    if value is None:
        return "unknown"
    if metric_key in _INFO_ONLY_METRICS:
        return "info"
    if metric_key == "docker_container_count":
        min_allowed = min(warning_threshold, critical_threshold)
        max_allowed = max(warning_threshold, critical_threshold)
        if value < min_allowed or value > max_allowed:
            return "critical"
        return "ok"
    if metric_key == "ssh_last_successful_login":
        return "ok"
    if metric_key == "ssh_status":
        if value >= 2:
            return "critical"
        if value >= 1:
            return "warn"
        return "ok"
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


def evaluate_quick_status_item(item: QuickStatusItem, snapshot: MetricSnapshot | None) -> QuickStatusEvaluation:
    if snapshot is None:
        return QuickStatusEvaluation(value=None, display_value="—", status="unknown")
    value = _metric_value(snapshot, item.metric_key, item.mount_path)
    return QuickStatusEvaluation(
        value=value,
        display_value=_format_value(item.metric_key, value),
        status=_resolve_status(value, item.warning_threshold, item.critical_threshold, item.metric_key),
    )


def _build_item_history(
    item: QuickStatusItem,
    snapshots: list[MetricSnapshot],
    window_start: datetime,
    window_end: datetime,
) -> list[str]:
    if item.metric_key in _PING_METRICS:
        return _empty_history()

    bucket_ranges = _history_segment_ranges(window_start)
    buckets = [{} for _ in range(_HISTORY_SEGMENT_COUNT)]
    ordered_snapshots = sorted(
        snapshots,
        key=lambda snapshot: (_coerce_utc(snapshot.reported_at), snapshot.id),
    )

    previous_snapshot = next(
        (
            snapshot
            for snapshot in reversed(ordered_snapshots)
            if _coerce_utc(snapshot.reported_at) < window_start
        ),
        None,
    )
    current_status = evaluate_quick_status_item(item, previous_snapshot).status
    current_start = window_start

    for snapshot in ordered_snapshots:
        reported_at = _coerce_utc(snapshot.reported_at)
        if reported_at < window_start:
            continue
        if reported_at > window_end:
            break
        _accumulate_status_durations(buckets, bucket_ranges, current_status, current_start, reported_at)
        current_status = evaluate_quick_status_item(item, snapshot).status
        current_start = reported_at

    _accumulate_status_durations(buckets, bucket_ranges, current_status, current_start, window_end)
    return _finalize_history_buckets(buckets)


def _build_ping_history(
    item: QuickStatusItem,
    samples: list[QuickStatusPingSample],
    window_start: datetime,
    window_end: datetime,
) -> list[str]:
    bucket_ranges = _history_segment_ranges(window_start)
    buckets = [{} for _ in range(_HISTORY_SEGMENT_COUNT)]
    ordered_samples = sorted(
        samples,
        key=lambda sample: (_coerce_utc(sample.checked_at), sample.id),
    )

    previous_sample = next(
        (
            sample
            for sample in reversed(ordered_samples)
            if _coerce_utc(sample.checked_at) < window_start
        ),
        None,
    )
    current_status = _evaluate_ping_result(
        item,
        _ping_result_from_sample(previous_sample) if previous_sample is not None else None,
    ).status
    current_start = window_start

    for sample in ordered_samples:
        checked_at = _coerce_utc(sample.checked_at)
        if checked_at < window_start:
            continue
        if checked_at > window_end:
            break
        _accumulate_status_durations(buckets, bucket_ranges, current_status, current_start, checked_at)
        current_status = _evaluate_ping_result(item, _ping_result_from_sample(sample)).status
        current_start = checked_at

    _accumulate_status_durations(buckets, bucket_ranges, current_status, current_start, window_end)
    return _finalize_history_buckets(buckets)


def _build_heartbeat_history(
    backend: object,
    snapshots: list[MetricSnapshot],
    window_start: datetime,
    window_end: datetime,
) -> list[str]:
    bucket_ranges = _history_segment_ranges(window_start)
    buckets = [{} for _ in range(_HISTORY_SEGMENT_COUNT)]
    ordered_snapshots = sorted(
        snapshots,
        key=lambda snapshot: (_coerce_utc(snapshot.reported_at), snapshot.id),
    )
    if not ordered_snapshots:
        for bucket in buckets:
            bucket["unknown"] = _HISTORY_SEGMENT_DURATION.total_seconds()
        return _finalize_history_buckets(buckets)

    freshness_seconds = max(getattr(backend, "poll_interval_seconds", 60) or 60, 30) * 3
    freshness_window = timedelta(seconds=freshness_seconds)
    has_previous_snapshot = any(_coerce_utc(snapshot.reported_at) < window_start for snapshot in ordered_snapshots)
    first_known_at = window_start if has_previous_snapshot else max(window_start, _coerce_utc(ordered_snapshots[0].reported_at))

    if first_known_at > window_start:
        _accumulate_status_durations(buckets, bucket_ranges, "unknown", window_start, first_known_at)
    _accumulate_status_durations(buckets, bucket_ranges, "critical", first_known_at, window_end)

    for snapshot in ordered_snapshots:
        reported_at = _coerce_utc(snapshot.reported_at)
        ok_start = max(window_start, reported_at)
        ok_end = min(window_end, reported_at + freshness_window)
        _accumulate_status_durations(buckets, bucket_ranges, "ok", ok_start, ok_end)

    return _finalize_history_buckets(buckets)


async def _load_history_snapshots(
    session: AsyncSession,
    backend_ids: set[int],
    window_start: datetime,
    window_end: datetime,
) -> dict[int, list[MetricSnapshot]]:
    if not backend_ids:
        return {}

    ranked_previous_snapshots = (
        select(
            MetricSnapshot.id.label("snapshot_id"),
            func.row_number().over(
                partition_by=MetricSnapshot.backend_id,
                order_by=(MetricSnapshot.reported_at.desc(), MetricSnapshot.id.desc()),
            ).label("row_number"),
        )
        .where(
            MetricSnapshot.backend_id.in_(backend_ids),
            MetricSnapshot.reported_at < window_start,
        )
        .subquery()
    )

    previous_result = await session.execute(
        select(MetricSnapshot).join(
            ranked_previous_snapshots,
            MetricSnapshot.id == ranked_previous_snapshots.c.snapshot_id,
        ).where(ranked_previous_snapshots.c.row_number == 1)
    )
    current_result = await session.execute(
        select(MetricSnapshot)
        .where(
            MetricSnapshot.backend_id.in_(backend_ids),
            MetricSnapshot.reported_at >= window_start,
            MetricSnapshot.reported_at <= window_end,
        )
        .order_by(MetricSnapshot.backend_id, MetricSnapshot.reported_at, MetricSnapshot.id)
    )

    snapshots_by_backend: dict[int, list[MetricSnapshot]] = {backend_id: [] for backend_id in backend_ids}
    for snapshot in previous_result.scalars():
        snapshots_by_backend.setdefault(snapshot.backend_id, []).append(snapshot)
    for snapshot in current_result.scalars():
        snapshots_by_backend.setdefault(snapshot.backend_id, []).append(snapshot)
    for snapshots in snapshots_by_backend.values():
        snapshots.sort(key=lambda snapshot: (_coerce_utc(snapshot.reported_at), snapshot.id))
    return snapshots_by_backend


async def _load_ping_history_samples(
    session: AsyncSession,
    item_ids: set[int],
    window_start: datetime,
    window_end: datetime,
) -> dict[int, list[QuickStatusPingSample]]:
    if not item_ids:
        return {}

    ranked_previous_samples = (
        select(
            QuickStatusPingSample.id.label("sample_id"),
            func.row_number().over(
                partition_by=QuickStatusPingSample.quick_status_item_id,
                order_by=(QuickStatusPingSample.checked_at.desc(), QuickStatusPingSample.id.desc()),
            ).label("row_number"),
        )
        .where(
            QuickStatusPingSample.quick_status_item_id.in_(item_ids),
            QuickStatusPingSample.checked_at < window_start,
        )
        .subquery()
    )

    previous_result = await session.execute(
        select(QuickStatusPingSample).join(
            ranked_previous_samples,
            QuickStatusPingSample.id == ranked_previous_samples.c.sample_id,
        ).where(ranked_previous_samples.c.row_number == 1)
    )
    current_result = await session.execute(
        select(QuickStatusPingSample)
        .where(
            QuickStatusPingSample.quick_status_item_id.in_(item_ids),
            QuickStatusPingSample.checked_at >= window_start,
            QuickStatusPingSample.checked_at <= window_end,
        )
        .order_by(QuickStatusPingSample.quick_status_item_id, QuickStatusPingSample.checked_at, QuickStatusPingSample.id)
    )

    samples_by_item: dict[int, list[QuickStatusPingSample]] = {item_id: [] for item_id in item_ids}
    for sample in previous_result.scalars():
        samples_by_item.setdefault(sample.quick_status_item_id, []).append(sample)
    for sample in current_result.scalars():
        samples_by_item.setdefault(sample.quick_status_item_id, []).append(sample)
    for samples in samples_by_item.values():
        samples.sort(key=lambda sample: (_coerce_utc(sample.checked_at), sample.id))
    return samples_by_item


async def _persist_ping_result(
    session: AsyncSession,
    item: QuickStatusItem,
    ping_result: PingCheckResult,
    latest_sample: QuickStatusPingSample | None,
) -> QuickStatusPingSample | None:
    if latest_sample is not None and _coerce_utc(latest_sample.checked_at) >= ping_result.checked_at:
        return None

    retention_window = await metric_retention_timedelta(session)
    cutoff = ping_result.checked_at - retention_window
    await session.execute(
        QuickStatusPingSample.__table__.delete()
        .where(QuickStatusPingSample.checked_at < cutoff)
        .execution_options(synchronize_session=False)
    )
    sample = QuickStatusPingSample(
        quick_status_item_id=item.id,
        checked_at=ping_result.checked_at,
        success=ping_result.success,
        latency_ms=ping_result.latency_ms,
    )
    session.add(sample)
    await session.flush()
    return sample


async def _check_ping(
    session: AsyncSession,
    item: QuickStatusItem,
    *,
    latest_sample: QuickStatusPingSample | None = None,
    persist_history: bool = False,
    now: datetime | None = None,
) -> tuple[PingCheckResult | None, QuickStatusPingSample | None]:
    if not item.ping_endpoint:
        return None, None
    backend = getattr(item, "backend", None)
    if backend is None:
        return None, None
    interval = max(5, int(item.ping_interval_seconds or 60))
    checked_at = _coerce_utc(now or datetime.now(tz=timezone.utc))
    latest_result = _ping_result_from_sample(latest_sample) if latest_sample is not None else None
    async with _PING_LOCK:
        cached = _PING_CACHE.get(item.id)
        if cached and checked_at - cached.checked_at < timedelta(seconds=interval):
            if persist_history:
                persisted_sample = await _persist_ping_result(session, item, cached, latest_sample)
                return cached, persisted_sample
            return cached, None
    if latest_result is not None and checked_at - latest_result.checked_at < timedelta(seconds=interval):
        async with _PING_LOCK:
            _PING_CACHE[item.id] = latest_result
        return latest_result, None
    timeout_seconds = max(1, int(settings.monitor_request_timeout_seconds or 1))
    timeout_seconds = min(30, timeout_seconds)
    try:
        payload = await fetch_ping(
            backend.base_url,
            backend.api_token,
            item.ping_endpoint,
            timeout_seconds,
        )
    except (MonitorClientError, Exception):
        payload = None

    if payload is None:
        success = False
        latency_ms = None
    else:
        success = bool(payload.get("success"))
        latency = payload.get("latency_ms")
        latency_ms = float(latency) if isinstance(latency, (int, float)) else None
    result = PingCheckResult(checked_at=checked_at, success=success, latency_ms=latency_ms)
    async with _PING_LOCK:
        _PING_CACHE[item.id] = result
    persisted_sample = None
    if persist_history:
        persisted_sample = await _persist_ping_result(session, item, result, latest_sample)
    return result, persisted_sample


async def list_quick_status_items(session: AsyncSession) -> list[QuickStatusItem]:
    result = await session.execute(
        select(QuickStatusItem)
        .options(selectinload(QuickStatusItem.backend))
        .order_by(QuickStatusItem.display_order, QuickStatusItem.id)
    )
    return [item for item in result.scalars() if is_supported_quick_status_metric(getattr(item, "metric_key", None))]


async def list_quick_status_items_for_backend(session: AsyncSession, backend_id: int) -> list[QuickStatusItem]:
    result = await session.execute(
        select(QuickStatusItem)
        .where(QuickStatusItem.backend_id == backend_id)
        .order_by(QuickStatusItem.display_order, QuickStatusItem.id)
    )
    return [item for item in result.scalars() if is_supported_quick_status_metric(getattr(item, "metric_key", None))]


async def build_quick_status_tiles(
    session: AsyncSession,
    items: Iterable[QuickStatusItem],
    *,
    include_heartbeat_tiles: bool = False,
    backends: Iterable[object] | None = None,
    now: datetime | None = None,
    persist_ping_history: bool = False,
) -> list[QuickStatusTileRead]:
    items_list = sorted(
        [item for item in items if is_supported_quick_status_metric(getattr(item, "metric_key", None))],
        key=_quick_status_item_sort_key,
    )
    backend_by_id: dict[int, object] = {}
    for item in items_list:
        backend = getattr(item, "backend", None)
        if backend is not None:
            backend_by_id[item.backend_id] = backend
    for backend in backends or []:
        backend_id = getattr(backend, "id", None)
        if backend_id is not None:
            backend_by_id[backend_id] = backend

    if not items_list and not (include_heartbeat_tiles and backend_by_id):
        return []

    backend_ids = set(backend_by_id) | {item.backend_id for item in items_list}
    window_start, window_end = _history_window_bounds(now)
    ping_history_by_item = await _load_ping_history_samples(
        session,
        {item.id for item in items_list if item.metric_key in _PING_METRICS},
        window_start,
        window_end,
    )
    ping_results_by_item: dict[int, PingCheckResult | None] = {}
    for item in items_list:
        if item.metric_key not in _PING_METRICS:
            continue
        latest_sample = ping_history_by_item.get(item.id, [])[-1] if ping_history_by_item.get(item.id) else None
        ping_result, persisted_sample = await _check_ping(
            session,
            item,
            latest_sample=latest_sample,
            persist_history=persist_ping_history,
            now=window_end,
        )
        ping_results_by_item[item.id] = ping_result
        if persisted_sample is not None:
            ping_history_by_item.setdefault(item.id, []).append(persisted_sample)
            ping_history_by_item[item.id].sort(key=lambda sample: (_coerce_utc(sample.checked_at), sample.id))
    history_snapshots_by_backend = await _load_history_snapshots(session, backend_ids, window_start, window_end)
    latest_snapshots = {
        backend_id: snapshots[-1]
        for backend_id, snapshots in history_snapshots_by_backend.items()
        if snapshots
    }

    tiles: list[QuickStatusTileRead] = []
    if include_heartbeat_tiles:
        for backend_id in sorted(
            backend_ids,
            key=lambda current_backend_id: (
                getattr(backend_by_id.get(current_backend_id), "display_order", 0),
                getattr(backend_by_id.get(current_backend_id), "name", "").casefold(),
                current_backend_id,
            ),
        ):
            backend = backend_by_id.get(backend_id)
            latest_snapshot = latest_snapshots.get(backend_id)
            freshness_seconds = max(getattr(backend, "poll_interval_seconds", 60) or 60, 30) * 3
            reference_at = getattr(backend, "last_seen_at", None) or getattr(latest_snapshot, "reported_at", None)
            reference_date = _coerce_utc(reference_at) if isinstance(reference_at, datetime) else None
            is_fresh = reference_date is not None and (window_end - reference_date).total_seconds() <= freshness_seconds
            tiles.append(
                QuickStatusTileRead(
                    id=-backend_id,
                    backend_id=backend_id,
                    backend_display_order=getattr(backend, "display_order", 0),
                    backend_name=getattr(backend, "name", f"Backend #{backend_id}"),
                    label="HB",
                    metric_key="ssh_status",
                    value=1.0 if is_fresh else (0.0 if reference_date is not None else None),
                    display_value="Now" if is_fresh else ("Late" if reference_date is not None else "—"),
                    status="ok" if is_fresh else ("critical" if reference_date is not None else "unknown"),
                    history=_build_heartbeat_history(
                        backend,
                        history_snapshots_by_backend.get(backend_id, []),
                        window_start,
                        window_end,
                    ),
                    reported_at=reference_date,
                    details=[
                        QuickStatusDetailLine(
                            text=f"Heartbeat threshold: {freshness_seconds}s",
                            severity="ok" if is_fresh else ("critical" if reference_date is not None else "warn"),
                        ),
                        QuickStatusDetailLine(
                            text=f"Last seen: {reference_date.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
                            if reference_date is not None
                            else "No heartbeat received yet",
                            severity="ok" if is_fresh else ("critical" if reference_date is not None else "warn"),
                        ),
                    ],
                )
            )

    for item in items_list:
        backend = getattr(item, "backend", None)
        snapshot = latest_snapshots.get(item.backend_id)
        ping_result = ping_results_by_item.get(item.id) if item.metric_key in _PING_METRICS else None
        value = _metric_value(snapshot, item.metric_key, item.mount_path) if snapshot else None
        status = _resolve_status(value, item.warning_threshold, item.critical_threshold, item.metric_key)
        display_value = _format_value(item.metric_key, value)
        reported_at = snapshot.reported_at if snapshot else None
        if item.metric_key in _PING_METRICS:
            ping_evaluation = _evaluate_ping_result(item, ping_result)
            value = ping_evaluation.value
            display_value = ping_evaluation.display_value
            status = ping_evaluation.status
            reported_at = ping_result.checked_at if ping_result is not None else None
        tiles.append(
            QuickStatusTileRead(
                id=item.id,
                backend_id=item.backend_id,
                backend_display_order=backend.display_order if backend else 0,
                backend_name=backend.name if backend else "Unknown",
                label=item.label,
                metric_key=item.metric_key,
                value=value,
                display_value=display_value,
                status=status,
                history=(
                    _build_ping_history(item, ping_history_by_item.get(item.id, []), window_start, window_end)
                    if item.metric_key in _PING_METRICS
                    else _build_item_history(
                        item,
                        history_snapshots_by_backend.get(item.backend_id, []),
                        window_start,
                        window_end,
                    )
                ),
                reported_at=reported_at,
                details=_build_detail_lines(item, snapshot),
            )
        )
    return tiles


def detect_quick_status_transitions_for_snapshot(
    items: Iterable[QuickStatusItem],
    previous_snapshot: MetricSnapshot | None,
    current_snapshot: MetricSnapshot,
    backend_name: str,
) -> list[QuickStatusTransition]:
    transitions: list[QuickStatusTransition] = []
    for item in items:
        if not is_supported_quick_status_metric(getattr(item, "metric_key", None)):
            continue
        if item.metric_key in _PING_METRICS:
            continue
        previous_value = _metric_value(previous_snapshot, item.metric_key, item.mount_path) if previous_snapshot else None
        previous_status = _resolve_status(
            previous_value,
            item.warning_threshold,
            item.critical_threshold,
            item.metric_key,
        )
        current_value = _metric_value(current_snapshot, item.metric_key, item.mount_path)
        current_status = _resolve_status(
            current_value,
            item.warning_threshold,
            item.critical_threshold,
            item.metric_key,
        )
        if current_status in _ALERT_STATUSES and current_status != previous_status:
            transitions.append(
                QuickStatusTransition(
                    item_id=item.id,
                    backend_id=item.backend_id,
                    backend_name=backend_name,
                    label=item.label,
                    metric_key=item.metric_key,
                    previous_status=previous_status,
                    current_status=current_status,
                    display_value=_format_value(item.metric_key, current_value),
                )
            )
    return transitions


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
